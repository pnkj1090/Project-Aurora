import Foundation
import AVFoundation
import Speech

// MARK: - VoicePermissionManaging Protocol
public protocol VoicePermissionManaging: Sendable {
    func checkMicrophonePermission() async -> VoicePermissionState
    func requestMicrophonePermission() async -> Bool
    func checkSpeechPermission() async -> VoicePermissionState
    func requestSpeechPermission() async -> Bool
}

// MARK: - DefaultVoicePermissionManager
public final class DefaultVoicePermissionManager: VoicePermissionManaging {
    public init() {}

    public func checkMicrophonePermission() async -> VoicePermissionState {
        switch AVAudioSession.sharedInstance().recordPermission {
        case .granted:
            return .authorized
        case .denied:
            return .denied
        case .undetermined:
            return .notDetermined
        @unknown default:
            return .notDetermined
        }
    }

    public func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    public func checkSpeechPermission() async -> VoicePermissionState {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            return .authorized
        case .denied:
            return .denied
        case .restricted:
            return .restricted
        case .notDetermined:
            return .notDetermined
        @unknown default:
            return .notDetermined
        }
    }

    public func requestSpeechPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }
}

// MARK: - VoiceSessionCoordinator Delegate / Callback
@MainActor
public protocol VoiceSessionCoordinatorDelegate: AnyObject {
    func voiceSessionDidUpdateState(_ state: VoiceSessionState)
    func voiceSessionDidFinalizeTranscript(_ text: String) async
}

// MARK: - VoiceSessionCoordinator
@MainActor
public final class VoiceSessionCoordinator: ObservableObject {
    @Published public private(set) var state: VoiceSessionState = .idle
    @Published public private(set) var currentAmplitude: Float = 0.0

    private var sessionGeneration: UInt64 = 0
    private var sessionId: UUID = UUID()
    public private(set) var turnGeneration: UInt64 = 0
    public private(set) var silenceTimeoutGeneration: UInt64 = 0

    private let captureManager: SpeechCaptureManaging
    private let outputManager: SpeechOutputManaging
    private let permissionManager: VoicePermissionManaging
    public let configuration: VoiceConfiguration

    public weak var delegate: VoiceSessionCoordinatorDelegate?

    private var activeTranscriptionTask: Task<Void, Never>?
    private var silenceTimeoutTask: Task<Void, Never>?

    public init(
        captureManager: SpeechCaptureManaging? = nil,
        outputManager: SpeechOutputManaging? = nil,
        permissionManager: VoicePermissionManaging = DefaultVoicePermissionManager(),
        configuration: VoiceConfiguration = VoiceConfiguration()
    ) {
        self.configuration = configuration
        self.permissionManager = permissionManager
        let capture = captureManager ?? SpeechCaptureManager(configuration: configuration)
        let output = outputManager ?? SpeechOutputManager(configuration: configuration)
        self.captureManager = capture
        self.outputManager = output
    }

    public var activeGeneration: UInt64 {
        sessionGeneration
    }

    public var activeSessionId: UUID {
        sessionId
    }

    public var activeTurnGeneration: UInt64 {
        turnGeneration
    }

    public var activeSilenceTimeoutGeneration: UInt64 {
        silenceTimeoutGeneration
    }

    // MARK: - Session Control

    public func startSession() async {
        guard state == .idle || isTerminalState else { return }

        sessionGeneration &+= 1
        sessionId = UUID()
        turnGeneration = 0
        silenceTimeoutTask?.cancel()
        silenceTimeoutTask = nil
        let currentGen = sessionGeneration

        transition(to: .preparing)

        // 1. Check & Request Permissions
        let micState = await permissionManager.checkMicrophonePermission()
        if micState == .denied || micState == .restricted {
            transition(to: .failed(.permissionDenied(.microphone)))
            return
        }

        let speechState = await permissionManager.checkSpeechPermission()
        if speechState == .denied || speechState == .restricted {
            transition(to: .failed(.permissionDenied(.speechRecognition)))
            return
        }

        if micState == .notDetermined || speechState == .notDetermined {
            transition(to: .requestingPermission)
            if micState == .notDetermined {
                let granted = await permissionManager.requestMicrophonePermission()
                guard granted, currentGen == self.sessionGeneration else {
                    transition(to: .failed(.permissionDenied(.microphone)))
                    return
                }
            }
            if speechState == .notDetermined {
                let granted = await permissionManager.requestSpeechPermission()
                guard granted, currentGen == self.sessionGeneration else {
                    transition(to: .failed(.permissionDenied(.speechRecognition)))
                    return
                }
            }
        }

        guard currentGen == self.sessionGeneration else { return }
        transition(to: .ready)

        // 2. Begin listening
        await beginListening(generation: currentGen)
    }

    public func stopSession() async {
        sessionGeneration &+= 1
        transition(to: .stopping)

        silenceTimeoutTask?.cancel()
        silenceTimeoutTask = nil

        activeTranscriptionTask?.cancel()
        activeTranscriptionTask = nil

        await captureManager.stopCapture()
        outputManager.stopSpeaking()

        currentAmplitude = 0.0
        transition(to: .idle)
    }

    public func stopSpeaking() {
        outputManager.stopSpeaking()
    }

    // MARK: - Conversational Turn Transitions

    public func beginListening(generation: UInt64? = nil) async {
        let gen = generation ?? sessionGeneration
        guard gen == self.sessionGeneration else { return }

        activeTranscriptionTask?.cancel()
        silenceTimeoutTask?.cancel()

        do {
            let stream = try await self.captureManager.startCapture(generation: gen, sessionId: self.sessionId)
            guard gen == self.sessionGeneration else { return }

            transition(to: .listening(amplitude: 0.0))
            startSilenceTimeout(generation: gen)

            activeTranscriptionTask = Task { [weak self, gen] in
                guard let self = self else { return }
                do {
                    for try await result in stream {
                        guard gen == self.sessionGeneration else { break }

                        if result.isFinal {
                            let trimmed = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
                            if !trimmed.isEmpty {
                                self.silenceTimeoutTask?.cancel()
                                self.silenceTimeoutTask = nil
                                self.turnGeneration &+= 1
                                self.transition(to: .processing)
                                await self.captureManager.stopCapture()
                                await self.delegate?.voiceSessionDidFinalizeTranscript(trimmed)
                            } else {
                                // Empty final transcript, return to listening with silence timer
                                self.transition(to: .listening(amplitude: 0.0))
                                self.startSilenceTimeout(generation: gen)
                            }
                            break
                        } else {
                            self.transition(to: .transcribing(partialText: result.text))
                            // Partial transcript activity resets the silence timeout
                            self.startSilenceTimeout(generation: gen)
                        }
                    }
                } catch {
                    guard gen == self.sessionGeneration else { return }
                    self.silenceTimeoutTask?.cancel()
                    self.silenceTimeoutTask = nil
                    if let voiceErr = error as? VoiceSessionError {
                        self.transition(to: .failed(voiceErr))
                    } else {
                        self.transition(to: .failed(.transcriptionFailed(error.localizedDescription)))
                    }
                }
            }
        } catch {
            guard gen == self.sessionGeneration else { return }
            self.silenceTimeoutTask?.cancel()
            self.silenceTimeoutTask = nil
            if let voiceErr = error as? VoiceSessionError {
                self.transition(to: .failed(voiceErr))
            } else {
                self.transition(to: .failed(.transcriptionFailed(error.localizedDescription)))
            }
        }
    }

    // MARK: - Silence Timeout Management

    public func startSilenceTimeout(generation: UInt64) {
        silenceTimeoutTask?.cancel()
        silenceTimeoutGeneration &+= 1
        let capturedTimeoutGen = silenceTimeoutGeneration
        let timeoutSeconds = configuration.conversationalSilenceTimeoutSeconds

        silenceTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
            guard let self = self else { return }
            guard capturedTimeoutGen == self.silenceTimeoutGeneration,
                  generation == self.sessionGeneration,
                  case .listening = self.state else { return }

            await self.captureManager.stopCapture()
            self.transition(to: .ready)
        }
    }

    /// Delivers assistant speech response to output manager
    public func speakResponse(_ text: String) async {
        switch state {
        case .processing, .listening, .ready:
            break
        default:
            return
        }

        silenceTimeoutTask?.cancel()
        silenceTimeoutTask = nil
        await captureManager.stopCapture()

        let currentGen = sessionGeneration
        let currentTurn = turnGeneration
        transition(to: .speaking(speakingText: text))

        await outputManager.speak(text: text)

        // When speech output completes without interruption and session is still active:
        if currentGen == self.sessionGeneration, currentTurn == self.turnGeneration, case .speaking = state {
            if configuration.autoListenAfterSpeaking {
                await beginListening(generation: currentGen)
            } else {
                transition(to: .ready)
            }
        }
    }

    // MARK: - Barge-In Handling

    public func handleDetectedSpeechActivity() {
        guard case .speaking = state else { return }

        // User started speaking while Aurora was speaking:
        // 1. Immediately cancel TTS
        outputManager.stopSpeaking()
        turnGeneration &+= 1

        // 2. Transition state speaking -> interrupted -> listening
        transition(to: .interrupted)

        let currentGen = self.sessionGeneration
        Task { [weak self] in
            guard let self = self, currentGen == self.sessionGeneration else { return }
            await self.beginListening(generation: currentGen)
        }
    }

    // MARK: - Interruption & Route Events

    public func handleAudioSessionInterruption(began: Bool) {
        if began {
            silenceTimeoutTask?.cancel()
            silenceTimeoutTask = nil
            transition(to: .interrupted)
            outputManager.stopSpeaking()
        } else {
            // Interruption ended, if still active try to resume
            if case .interrupted = state {
                let currentGen = self.sessionGeneration
                Task { [weak self] in
                    guard let self = self, currentGen == self.sessionGeneration else { return }
                    await self.beginListening(generation: currentGen)
                }
            }
        }
    }

    public func handleAudioRouteChange(reason: String) {
        // Handle peripheral disconnection
        if case .listening = state, reason.contains("disconnected") {
            silenceTimeoutTask?.cancel()
            silenceTimeoutTask = nil
            sessionGeneration &+= 1
            transition(to: .failed(.audioRouteChange(reason)))
        }
    }

    public func updateAmplitude(_ amp: Float) {
        self.currentAmplitude = amp
        if case .listening = state {
            self.state = .listening(amplitude: amp)
            // If significant voice activity detected, keep silence timer refreshed
            if amp > -32.0 {
                startSilenceTimeout(generation: sessionGeneration)
            }
        }
    }

    // MARK: - State Management Helper

    private func transition(to newState: VoiceSessionState) {
        self.state = newState
        self.delegate?.voiceSessionDidUpdateState(newState)
    }

    private var isTerminalState: Bool {
        switch state {
        case .idle, .failed:
            return true
        default:
            return false
        }
    }
}
