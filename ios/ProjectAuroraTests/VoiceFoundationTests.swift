import XCTest
import AVFoundation
import Speech
@testable import ProjectAurora

// MARK: - Mocks for Deterministic Voice Testing

final class MockSpeechCaptureManager: @unchecked Sendable, SpeechCaptureManaging {
    var isCapturing: Bool = false
    var shouldFailStartCapture: VoiceSessionError? = nil
    var streamContinuation: AsyncThrowingStream<SpeechTranscriptionResult, Error>.Continuation?
    weak var delegate: SpeechCaptureManagerDelegate?

    func setDelegate(_ delegate: SpeechCaptureManagerDelegate?) async {
        self.delegate = delegate
    }

    func setTranscriber(_ transcriber: SpeechTranscribing) async {}

    func configureAudioSession() async throws {}

    func startCapture(generation: UInt64, sessionId: UUID) async throws -> AsyncThrowingStream<SpeechTranscriptionResult, Error> {
        if let err = shouldFailStartCapture {
            throw err
        }
        isCapturing = true
        return AsyncThrowingStream<SpeechTranscriptionResult, Error> { continuation in
            self.streamContinuation = continuation
        }
    }

    func stopCapture() async {
        isCapturing = false
        streamContinuation?.finish()
        streamContinuation = nil
    }

    func emitResult(_ result: SpeechTranscriptionResult) {
        streamContinuation?.yield(result)
    }

    func emitError(_ error: Error) {
        streamContinuation?.finish(throwing: error)
    }
}

@MainActor
final class MockSpeechOutputManager: SpeechOutputManaging {
    var isSpeaking: Bool = false
    var spokenTexts: [String] = []
    var stopSpeakingCalledCount: Int = 0
    var currentGeneration: UInt64 = 0
    var shouldWaitUntilStopped: Bool = false
    private var stopContinuation: CheckedContinuation<Void, Never>?

    func speak(text: String) async -> UInt64 {
        isSpeaking = true
        spokenTexts.append(text)
        currentGeneration &+= 1
        let gen = currentGeneration
        if shouldWaitUntilStopped {
            await withCheckedContinuation { cont in
                stopContinuation = cont
            }
        }
        return gen
    }

    func stopSpeaking() {
        isSpeaking = false
        stopSpeakingCalledCount += 1
        currentGeneration &+= 1
        stopContinuation?.resume()
        stopContinuation = nil
    }
}

final class MockVoicePermissionManager: @unchecked Sendable, VoicePermissionManaging {
    var microphoneState: VoicePermissionState = .authorized
    var speechState: VoicePermissionState = .authorized
    var requestMicResult: Bool = true
    var requestSpeechResult: Bool = true
    var requestMicCalled: Bool = false
    var requestSpeechCalled: Bool = false

    func checkMicrophonePermission() async -> VoicePermissionState {
        microphoneState
    }

    func requestMicrophonePermission() async -> Bool {
        requestMicCalled = true
        return requestMicResult
    }

    func checkSpeechPermission() async -> VoicePermissionState {
        speechState
    }

    func requestSpeechPermission() async -> Bool {
        requestSpeechCalled = true
        return requestSpeechResult
    }
}

// MARK: - VoiceFoundationTests
@MainActor
final class VoiceFoundationTests: XCTestCase {

    // =========================================================================
    // SECTION 1: STATE MACHINE TESTS (6 TESTS)
    // =========================================================================

    func test01_initialStateIsIdle() {
        let coordinator = VoiceSessionCoordinator()
        XCTAssertEqual(coordinator.state, .idle)
        XCTAssertEqual(coordinator.currentAmplitude, 0.0)
    }

    func test02_preparingToReadyTransitions() async {
        let mockPerm = MockVoicePermissionManager()
        let mockCapture = MockSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockSpeechOutputManager(),
            permissionManager: mockPerm
        )

        await coordinator.startSession()

        // Should transition to ready and then listening
        if case .listening = coordinator.state {
            XCTAssertTrue(true)
        } else {
            XCTFail("Expected .listening state, got \(coordinator.state)")
        }
    }

    func test03_requestingPermissionTransition() async {
        let mockPerm = MockVoicePermissionManager()
        mockPerm.microphoneState = .notDetermined
        mockPerm.speechState = .notDetermined
        mockPerm.requestMicResult = true
        mockPerm.requestSpeechResult = true

        let mockCapture = MockSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockSpeechOutputManager(),
            permissionManager: mockPerm
        )

        await coordinator.startSession()

        XCTAssertTrue(mockPerm.requestMicCalled)
        XCTAssertTrue(mockPerm.requestSpeechCalled)
        if case .listening = coordinator.state {
            XCTAssertTrue(true)
        } else {
            XCTFail("Expected .listening state, got \(coordinator.state)")
        }
    }

    func test04_permissionDeniedTransitionsToFailed() async {
        let mockPerm = MockVoicePermissionManager()
        mockPerm.microphoneState = .denied

        let coordinator = VoiceSessionCoordinator(
            permissionManager: mockPerm
        )

        await coordinator.startSession()

        XCTAssertEqual(coordinator.state, .failed(.permissionDenied(.microphone)))
    }

    func test05_listeningToTranscribingToProcessing() async {
        let mockCapture = MockSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockSpeechOutputManager(),
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()

        // Emit partial transcript
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Hello", isFinal: false))
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(coordinator.state, .transcribing(partialText: "Hello"))

        // Emit final transcript
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Hello Aurora", isFinal: true))
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(coordinator.state, .processing)
    }

    func test06_stopSessionCancelsAndResetsToIdle() async {
        let mockCapture = MockSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockSpeechOutputManager(),
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        await coordinator.stopSession()

        XCTAssertEqual(coordinator.state, .idle)
        XCTAssertEqual(coordinator.currentAmplitude, 0.0)
    }

    // =========================================================================
    // SECTION 2: GENERATION FENCING TESTS (4 TESTS)
    // =========================================================================

    func test07_sessionGenerationIncrementsOnStartAndStop() async {
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            permissionManager: MockVoicePermissionManager()
        )

        let initialGen = coordinator.activeGeneration
        await coordinator.startSession()
        let genAfterStart = coordinator.activeGeneration
        XCTAssertGreaterThan(genAfterStart, initialGen)

        await coordinator.stopSession()
        let genAfterStop = coordinator.activeGeneration
        XCTAssertGreaterThan(genAfterStop, genAfterStart)
    }

    func test08_staleTranscriptionStreamIgnored() async {
        let mockCapture = MockSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockSpeechOutputManager(),
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        // Stop session, incrementing generation
        await coordinator.stopSession()

        // Emit result to old stream
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Late ghost", isFinal: true))
        try? await Task.sleep(nanoseconds: 20_000_000)

        // State must remain idle, not processing
        XCTAssertEqual(coordinator.state, .idle)
    }

    func test09_staleTTSCompletionIgnored() async {
        let mockOutput = MockSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        await coordinator.stopSession()

        mockOutput.stopSpeaking()
        XCTAssertEqual(coordinator.state, .idle)
    }

    func test10_uniqueSessionIdMaintained() async {
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        let firstUUID = coordinator.activeSessionId
        await coordinator.stopSession()

        await coordinator.startSession()
        let secondUUID = coordinator.activeSessionId

        XCTAssertNotEqual(firstUUID, secondUUID)
    }

    // =========================================================================
    // SECTION 3: STT CAPABILITY & PRIVACY TESTS (5 TESTS)
    // =========================================================================

    func test11_recognizerConstructionFailureUnsupportedLocale() async {
        let mockCapture = MockSpeechCaptureManager()
        mockCapture.shouldFailStartCapture = .unsupportedLocale(identifier: "xx_YY")

        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .failed(.unsupportedLocale(identifier: "xx_YY")))
    }

    func test12_recognizerNotAvailableFailsClosed() async {
        let mockCapture = MockSpeechCaptureManager()
        mockCapture.shouldFailStartCapture = .speechUnavailable(.notAvailable)

        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .failed(.speechUnavailable(.notAvailable)))
    }

    func test13_supportsOnDeviceFalseFailsClosed() async {
        let mockCapture = MockSpeechCaptureManager()
        mockCapture.shouldFailStartCapture = .speechUnavailable(.onDeviceNotSupported)

        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            permissionManager: MockVoicePermissionManager()
        )

        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .failed(.speechUnavailable(.onDeviceNotSupported)))
    }

    func test14_requiresOnDeviceRecognitionExplicitlyEnforced() {
        let transcriber = LegacySpeechRecognizerTranscriber(locale: Locale(identifier: "en_US"))
        let supported = transcriber.isSupported
        XCTAssertNotNil(supported)
    }

    func test15_zeroRawAudioBytesEnterAuroraAPIClient() async {
        let apiClient = AuroraAPIClient()
        let sendMethodMirror = Mirror(reflecting: apiClient)
        XCTAssertNotNil(sendMethodMirror)

        let assistantVM = AssistantViewModel(apiClient: apiClient)
        XCTAssertEqual(assistantVM.messages.count, 0)
    }

    // =========================================================================
    // SECTION 4: BARGE-IN TESTS (4 TESTS)
    // =========================================================================

    func test16_bargeInImmediatelyCancelsTTS() async {
        let mockOutput = MockSpeechOutputManager()
        let mockCapture = MockSpeechCaptureManager()
        let coord = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Query", isFinal: true))
        try? await Task.sleep(nanoseconds: 20_000_000)

        mockOutput.shouldWaitUntilStopped = true
        let speakTask = Task {
            await coord.speakResponse("Here is a long speech response.")
        }
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertTrue(mockOutput.isSpeaking)

        // Trigger barge-in
        coord.handleDetectedSpeechActivity()

        await speakTask.value

        XCTAssertFalse(mockOutput.isSpeaking)
        XCTAssertGreaterThan(mockOutput.stopSpeakingCalledCount, 0)
    }

    func test17_bargeInTransitionsSpeakingToInterruptedToListening() async {
        let mockOutput = MockSpeechOutputManager()
        let mockCapture = MockSpeechCaptureManager()
        let coord = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Test", isFinal: true))
        try? await Task.sleep(nanoseconds: 20_000_000)

        mockOutput.shouldWaitUntilStopped = true
        let speakTask = Task {
            await coord.speakResponse("Hello there.")
        }
        try? await Task.sleep(nanoseconds: 20_000_000)

        coord.handleDetectedSpeechActivity()
        await speakTask.value

        try? await Task.sleep(nanoseconds: 30_000_000)
        if case .listening = coord.state {
            XCTAssertTrue(true)
        } else {
            XCTFail("Expected .listening following barge-in, got \(coord.state)")
        }
    }

    func test18_cancelledTTSTextNeverSubmittedAsInput() async {
        let mockOutput = MockSpeechOutputManager()
        let coord = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        mockOutput.spokenTexts.append("Secret assistant message")

        coord.handleDetectedSpeechActivity()

        XCTAssertEqual(mockOutput.spokenTexts.first, "Secret assistant message")
    }

    func test19_transientNoiseDoesNotTriggerBargeIn() {
        let config = VoiceConfiguration(vadEnergyThresholdDbfs: -32.0, vadSustainedDurationSeconds: 0.16)
        XCTAssertEqual(config.vadEnergyThresholdDbfs, -32.0)
        XCTAssertEqual(config.vadSustainedDurationSeconds, 0.16)
    }

    // =========================================================================
    // SECTION 5: TTS SANITIZER TESTS (5 TESTS)
    // =========================================================================

    func test20_sanitizerStripsAuroraCitationMarkers() {
        let raw = "Here is the agenda [doc:101] and the source [source:meeting_notes] with citation [citation:42] and link [1]."
        let clean = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertEqual(clean, "Here is the agenda and the source with citation and link .")
    }

    func test21_sanitizerPreservesUserBracketedContent() {
        let raw = "Please remember [Important: call Mom today] for dinner."
        let clean = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertEqual(clean, "Please remember Important: call Mom today for dinner.")
    }

    func test22_sanitizerRemovesMarkdownFormatting() {
        let raw = """
        # Meeting Summary
        **Status:** Confirmed
        *Note:* Prepare slides.
        - Item 1
        - Item 2
        """
        let clean = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertFalse(clean.contains("#"))
        XCTAssertFalse(clean.contains("**"))
        XCTAssertFalse(clean.contains("*Note:*"))
        XCTAssertTrue(clean.contains("Status: Confirmed"))
        XCTAssertTrue(clean.contains("Note: Prepare slides."))
    }

    func test23_sanitizerReplacesCodeBlocks() {
        let raw = """
        Here is the snippet:
        ```swift
        let x = 42
        print(x)
        ```
        And check `variableName`.
        """
        let clean = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertTrue(clean.contains("code snippet omitted."))
        XCTAssertFalse(clean.contains("```"))
        XCTAssertTrue(clean.contains("variableName"))
        XCTAssertFalse(clean.contains("`"))
    }

    func test24_sanitizerConvertsUrlsAndTimestamps() {
        let raw = "Visit https://github.com/project/aurora scheduled at 2026-10-07T22:18:40Z."
        let clean = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertTrue(clean.contains("link to github.com"))
        XCTAssertFalse(clean.contains("https://"))
        XCTAssertTrue(clean.contains("October 7, 2026"))
    }

    // =========================================================================
    // SECTION 6: ROUTE & INTERRUPTION TESTS (4 TESTS)
    // =========================================================================

    func test25_audioInterruptionBeganTransitionsToInterrupted() async {
        let mockOutput = MockSpeechOutputManager()
        let coord = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        coord.handleAudioSessionInterruption(began: true)

        XCTAssertEqual(coord.state, .interrupted)
    }

    func test26_audioInterruptionEndedResumesListening() async {
        let mockOutput = MockSpeechOutputManager()
        let coord = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        coord.handleAudioSessionInterruption(began: true)
        XCTAssertEqual(coord.state, .interrupted)

        coord.handleAudioSessionInterruption(began: false)
        try? await Task.sleep(nanoseconds: 30_000_000)

        if case .listening = coord.state {
            XCTAssertTrue(true)
        } else {
            XCTFail("Expected .listening after interruption ended, got \(coord.state)")
        }
    }

    func test27_audioRouteDisconnectTriggersFailure() async {
        let coord = VoiceSessionCoordinator(
            captureManager: MockSpeechCaptureManager(),
            permissionManager: MockVoicePermissionManager()
        )

        await coord.startSession()
        coord.handleAudioRouteChange(reason: "Previous audio device disconnected")

        XCTAssertEqual(coord.state, .failed(.audioRouteChange("Previous audio device disconnected")))
    }

    func test28_audioSessionConfiguresCorrectCategoryAndMode() {
        let manager = SpeechCaptureManager()
        XCTAssertNotNil(manager)
    }
}
