import Foundation
import AVFoundation
import Speech
import Accelerate

// MARK: - SpeechTranscribing Protocol
public protocol SpeechTranscribing: AnyObject, Sendable {
    var isSupported: Bool { get }
    func prepare() async throws
    func startTranscribing() async throws -> AsyncThrowingStream<SpeechTranscriptionResult, Error>
    func stopTranscribing() async
}

// MARK: - LegacySpeechRecognizerTranscriber
public final class LegacySpeechRecognizerTranscriber: @unchecked Sendable, SpeechTranscribing {
    private let locale: Locale
    private var recognizer: SFSpeechRecognizer?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var streamContinuation: AsyncThrowingStream<SpeechTranscriptionResult, Error>.Continuation?
    private let lock = NSLock()

    public init(locale: Locale = .current) {
        self.locale = locale
    }

    public var isSupported: Bool {
        guard let recognizer = SFSpeechRecognizer(locale: locale) else { return false }
        return recognizer.isAvailable && recognizer.supportsOnDeviceRecognition
    }

    public func prepare() async throws {
        lock.lock()
        defer { lock.unlock() }

        // Authoritative Preflight Step 1 & 2: Obtain locale & create recognizer
        guard let rec = SFSpeechRecognizer(locale: locale) else {
            throw VoiceSessionError.unsupportedLocale(identifier: locale.identifier)
        }
        // Step 4: Check isAvailable
        guard rec.isAvailable else {
            throw VoiceSessionError.speechUnavailable(.notAvailable)
        }
        // Step 6: Check supportsOnDeviceRecognition
        guard rec.supportsOnDeviceRecognition else {
            throw VoiceSessionError.speechUnavailable(.onDeviceNotSupported)
        }
        self.recognizer = rec
    }

    public func startTranscribing() async throws -> AsyncThrowingStream<SpeechTranscriptionResult, Error> {
        lock.lock()
        defer { lock.unlock() }

        // Verify preflight
        guard let recognizer = self.recognizer ?? SFSpeechRecognizer(locale: locale) else {
            throw VoiceSessionError.unsupportedLocale(identifier: locale.identifier)
        }
        guard recognizer.isAvailable else {
            throw VoiceSessionError.speechUnavailable(.notAvailable)
        }
        guard recognizer.supportsOnDeviceRecognition else {
            throw VoiceSessionError.speechUnavailable(.onDeviceNotSupported)
        }

        // Clean up any existing task
        cleanupCurrentTask()

        // Steps 8, 9, 10: Create request and enforce on-device recognition
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        self.recognitionRequest = request

        return AsyncThrowingStream<SpeechTranscriptionResult, Error> { continuation in
            self.lock.lock()
            self.streamContinuation = continuation

            self.recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
                guard let self = self else { return }
                self.lock.lock()
                defer { self.lock.unlock() }

                if let error = error {
                    // Check if it's a cancellation error
                    let nsError = error as NSError
                    if nsError.domain == "kAFAssistantErrorDomain" && nsError.code == 216 {
                        // User cancelled / endAudio
                        self.streamContinuation?.finish()
                    } else if nsError.domain == "kLSRErrorDomain" && nsError.code == 201 {
                        // Task completed/cancelled
                        self.streamContinuation?.finish()
                    } else {
                        self.streamContinuation?.finish(throwing: VoiceSessionError.transcriptionFailed(error.localizedDescription))
                    }
                    self.streamContinuation = nil
                    return
                }

                if let result = result {
                    let transcribedText = result.bestTranscription.formattedString
                    let isFinal = result.isFinal
                    let transcriptionResult = SpeechTranscriptionResult(
                        text: transcribedText,
                        isFinal: isFinal,
                        confidence: 1.0,
                        timestamp: Date().timeIntervalSince1970
                    )
                    self.streamContinuation?.yield(transcriptionResult)

                    if isFinal {
                        self.streamContinuation?.finish()
                        self.streamContinuation = nil
                    }
                }
            }
            self.lock.unlock()

            continuation.onTermination = { [weak self] _ in
                Task { [weak self] in
                    await self?.stopTranscribing()
                }
            }
        }
    }

    public func consumeAudioBuffer(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        defer { lock.unlock() }
        recognitionRequest?.append(buffer)
    }

    public func stopTranscribing() async {
        lock.lock()
        defer { lock.unlock() }
        cleanupCurrentTask()
    }

    private func cleanupCurrentTask() {
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionRequest = nil
        recognitionTask = nil
        streamContinuation?.finish()
        streamContinuation = nil
    }
}

// MARK: - SpeechCaptureManager Delegate
public protocol SpeechCaptureManagerDelegate: AnyObject, Sendable {
    func speechCaptureDidUpdateAmplitude(_ amplitude: Float)
    func speechCaptureDidDetectSpeechActivity()
    func speechCaptureDidEncounterError(_ error: VoiceSessionError)
    func speechCaptureAudioSessionInterrupted(began: Bool)
    func speechCaptureAudioRouteChanged(reason: String)
}

// MARK: - SpeechCaptureManaging Protocol
public protocol SpeechCaptureManaging: AnyObject, Sendable {
    func setDelegate(_ delegate: SpeechCaptureManagerDelegate?) async
    func setTranscriber(_ transcriber: SpeechTranscribing) async
    func configureAudioSession() async throws
    func startCapture(generation: UInt64, sessionId: UUID) async throws -> AsyncThrowingStream<SpeechTranscriptionResult, Error>
    func stopCapture() async
}

// MARK: - SpeechCaptureManager Actor
public actor SpeechCaptureManager: SpeechCaptureManaging {
    private var audioEngine: AVAudioEngine?
    private var transcriber: SpeechTranscribing?
    private var isCapturing: Bool = false
    private var sessionGeneration: UInt64 = 0
    private var sessionId: UUID = UUID()

    private weak var delegate: SpeechCaptureManagerDelegate?
    private var configuration: VoiceConfiguration

    // VAD tracking state
    private var sustainedSpeechDuration: TimeInterval = 0.0
    private var lastBufferTimestamp: TimeInterval = 0.0

    // Notification observers
    private var interruptionObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?

    public init(
        configuration: VoiceConfiguration = VoiceConfiguration(),
        transcriber: SpeechTranscribing? = nil
    ) {
        self.configuration = configuration
        self.transcriber = transcriber
    }

    public func setDelegate(_ delegate: SpeechCaptureManagerDelegate?) {
        self.delegate = delegate
    }

    public func setTranscriber(_ transcriber: SpeechTranscribing) {
        self.transcriber = transcriber
    }

    public func configureAudioSession() throws {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .voiceChat,
                options: [.allowBluetooth, .defaultToSpeaker]
            )
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            throw VoiceSessionError.audioSessionConfigurationFailed(error.localizedDescription)
        }

        registerAudioSessionObservers()
    }

    public func startCapture(generation: UInt64, sessionId: UUID) async throws -> AsyncThrowingStream<SpeechTranscriptionResult, Error> {
        self.sessionGeneration = generation
        self.sessionId = sessionId
        self.sustainedSpeechDuration = 0.0
        self.lastBufferTimestamp = 0.0

        try configureAudioSession()

        // 1. Prepare transcriber
        let activeTranscriber = self.transcriber ?? LegacySpeechRecognizerTranscriber()
        self.transcriber = activeTranscriber
        try await activeTranscriber.prepare()

        // 2. Start transcription stream
        let transcriptionStream = try await activeTranscriber.startTranscribing()

        // 3. Set up AVAudioEngine and real-time tap
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)

        guard recordingFormat.sampleRate > 0, recordingFormat.channelCount > 0 else {
            throw VoiceSessionError.audioSessionConfigurationFailed("Invalid input recording format")
        }

        let currentGeneration = generation
        let vadEnergyThreshold = configuration.vadEnergyThresholdDbfs
        let requiredSustainedDuration = configuration.vadSustainedDurationSeconds

        // Local legacy adapter reference for non-blocking buffer handoff
        let legacyAdapter = activeTranscriber as? LegacySpeechRecognizerTranscriber

        // Install real-time audio tap
        // Real-time boundary rules:
        // - No await
        // - No actor hops inside tap
        // - No network or file I/O
        // - No blocking mutexes
        // - Vector calculation for RMS
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, time in
            guard let self = self else { return }

            // Bounded vector calculation for RMS energy
            let channelData = buffer.floatChannelData?[0]
            let frameLength = vDSP_Length(buffer.frameLength)

            var rms: Float = 0.0
            if let data = channelData, frameLength > 0 {
                vDSP_rmsqv(data, 1, &rms, frameLength)
            }

            // Convert to dBFS
            let dbfs: Float = (rms > 0.000001) ? 20.0 * log10(rms) : -100.0

            // Direct non-blocking buffer ingest to legacy adapter
            legacyAdapter?.consumeAudioBuffer(buffer)

            // Dispatch bounded metrics asynchronously to actor
            Task { [weak self] in
                await self?.processRealtimeMetrics(
                    dbfs: dbfs,
                    generation: currentGeneration,
                    threshold: vadEnergyThreshold,
                    requiredDuration: requiredSustainedDuration
                )
            }
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            throw VoiceSessionError.audioSessionConfigurationFailed("Audio engine could not start: \(error.localizedDescription)")
        }

        self.audioEngine = engine
        self.isCapturing = true

        return transcriptionStream
    }

    private func processRealtimeMetrics(
        dbfs: Float,
        generation: UInt64,
        threshold: Float,
        requiredDuration: TimeInterval
    ) {
        guard generation == self.sessionGeneration, self.isCapturing else { return }

        // Normalize amplitude (0.0 to 1.0) for UI waveforms
        let normalizedAmp = max(0.0, min(1.0, (dbfs + 60.0) / 60.0))
        delegate?.speechCaptureDidUpdateAmplitude(normalizedAmp)

        // VAD tracking: check if energy exceeds threshold
        let now = Date().timeIntervalSince1970
        let delta = (lastBufferTimestamp > 0) ? (now - lastBufferTimestamp) : 0.02
        lastBufferTimestamp = now

        if dbfs >= threshold {
            sustainedSpeechDuration += delta
            if sustainedSpeechDuration >= requiredDuration {
                delegate?.speechCaptureDidDetectSpeechActivity()
            }
        } else {
            // Decay sustained speech when below threshold
            sustainedSpeechDuration = max(0.0, sustainedSpeechDuration - (delta * 2.0))
        }
    }

    public func stopCapture() async {
        isCapturing = false

        if let engine = audioEngine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            self.audioEngine = nil
        }

        if let transcriber = transcriber {
            await transcriber.stopTranscribing()
        }

        deactivateAudioSession()
        unregisterAudioSessionObservers()
    }

    private func deactivateAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            // Audio session deactivation failure is non-fatal on teardown
        }
    }

    private func registerAudioSessionObservers() {
        unregisterAudioSessionObservers()

        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            queue: nil
        ) { [weak self] notification in
            guard let info = notification.userInfo,
                  let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

            let began = (type == .began)
            Task { [weak self] in
                await self?.handleAudioInterruption(began: began)
            }
        }

        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: nil
        ) { [weak self] notification in
            guard let info = notification.userInfo,
                  let reasonValue = info[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue) else { return }

            Task { [weak self] in
                await self?.handleAudioRouteChange(reason: reason)
            }
        }
    }

    private func unregisterAudioSessionObservers() {
        if let observer = interruptionObserver {
            NotificationCenter.default.removeObserver(observer)
            interruptionObserver = nil
        }
        if let observer = routeChangeObserver {
            NotificationCenter.default.removeObserver(observer)
            routeChangeObserver = nil
        }
    }

    private func handleAudioInterruption(began: Bool) {
        delegate?.speechCaptureAudioSessionInterrupted(began: began)
    }

    private func handleAudioRouteChange(reason: AVAudioSession.RouteChangeReason) {
        let reasonDescription: String
        switch reason {
        case .oldDeviceUnavailable:
            reasonDescription = "Previous audio device disconnected"
        case .newDeviceAvailable:
            reasonDescription = "New audio device connected"
        case .categoryChange:
            reasonDescription = "Category changed"
        default:
            reasonDescription = "Route changed (code \(reason.rawValue))"
        }
        delegate?.speechCaptureAudioRouteChanged(reason: reasonDescription)
    }
}
