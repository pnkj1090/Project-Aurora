import XCTest
import AVFoundation
import Speech
@testable import ProjectAurora

// MARK: - Mocks for Voice Conversation Testing

final class MockConversationSpeechCaptureManager: @unchecked Sendable, SpeechCaptureManaging {
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
final class MockConversationSpeechOutputManager: @preconcurrency SpeechOutputManaging {
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
        shouldWaitUntilStopped = false
        stopContinuation?.resume()
        stopContinuation = nil
    }
}

final class MockConversationVoicePermissionManager: @unchecked Sendable, VoicePermissionManaging {
    var microphoneState: VoicePermissionState = .authorized
    var speechState: VoicePermissionState = .authorized

    func checkMicrophonePermission() async -> VoicePermissionState { microphoneState }
    func requestMicrophonePermission() async -> Bool { true }
    func checkSpeechPermission() async -> VoicePermissionState { speechState }
    func requestSpeechPermission() async -> Bool { true }
}

// MARK: - VoiceConversationTests (28 Deterministic Tests)

@MainActor
final class VoiceConversationTests: XCTestCase {

    private var apiClient: AuroraAPIClient!
    private var session: URLSession!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        session = URLSession(configuration: config)
        apiClient = AuroraAPIClient(
            baseURL: URL(string: "http://localhost:8000")!,
            session: session,
            credentialProvider: { "aurora_cred_test_token" }
        )
    }

    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        super.tearDown()
    }

    private func extractHTTPBody(from request: URLRequest) -> Data? {
        if let body = request.httpBody {
            return body
        }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: buffer.count)
            if read > 0 {
                data.append(buffer, count: read)
            } else {
                break
            }
        }
        return data.isEmpty ? nil : data
    }

    // =========================================================================
    // SECTION 1: TURN FLOW (6 TESTS)
    // =========================================================================

    func test01_fullTurnLoop() async {
        let mockCapture = MockConversationSpeechCaptureManager()
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager(),
            configuration: VoiceConfiguration(autoListenAfterSpeaking: true)
        )

        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))

        mockCapture.emitResult(SpeechTranscriptionResult(text: "Hello", isFinal: false))
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(coordinator.state, .transcribing(partialText: "Hello"))

        mockCapture.emitResult(SpeechTranscriptionResult(text: "Hello Aurora", isFinal: true))
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(coordinator.state, .processing)

        await coordinator.speakResponse("Hello, how can I help?")
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))
    }

    func test02_autoListenDisabled() async {
        let mockCapture = MockConversationSpeechCaptureManager()
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager(),
            configuration: VoiceConfiguration(autoListenAfterSpeaking: false)
        )

        await coordinator.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Question", isFinal: true))
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(coordinator.state, .processing)

        await coordinator.speakResponse("Here is the answer.")
        XCTAssertEqual(coordinator.state, .ready)
    }

    func test03_silenceTimeout() async {
        let mockCapture = MockConversationSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager(),
            configuration: VoiceConfiguration(conversationalSilenceTimeoutSeconds: 0.05)
        )

        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))

        try? await Task.sleep(nanoseconds: 80_000_000)
        XCTAssertEqual(coordinator.state, .ready)
    }

    func test04_nonEmptyFinalTranscript() async {
        let mockCapture = MockConversationSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager()
        )

        var finalizedText: String?
        final class TestDelegate: VoiceSessionCoordinatorDelegate {
            var onFinalize: (String) -> Void
            init(onFinalize: @escaping (String) -> Void) { self.onFinalize = onFinalize }
            func voiceSessionDidUpdateState(_ state: VoiceSessionState) {}
            func voiceSessionDidFinalizeTranscript(_ text: String) async { onFinalize(text) }
        }

        let delegate = TestDelegate { finalizedText = $0 }
        coordinator.delegate = delegate

        await coordinator.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "  Schedule meeting  ", isFinal: true))
        try? await Task.sleep(nanoseconds: 10_000_000)

        XCTAssertEqual(finalizedText, "Schedule meeting")
        XCTAssertEqual(coordinator.activeTurnGeneration, 1)
    }

    func test05_emptyFinalTranscript() async {
        let mockCapture = MockConversationSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager()
        )

        var didCallDelegate = false
        final class TestDelegate: VoiceSessionCoordinatorDelegate {
            var onFinalize: () -> Void
            init(onFinalize: @escaping () -> Void) { self.onFinalize = onFinalize }
            func voiceSessionDidUpdateState(_ state: VoiceSessionState) {}
            func voiceSessionDidFinalizeTranscript(_ text: String) async { onFinalize() }
        }

        let delegate = TestDelegate { didCallDelegate = true }
        coordinator.delegate = delegate

        await coordinator.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "   \n  ", isFinal: true))
        try? await Task.sleep(nanoseconds: 10_000_000)

        XCTAssertFalse(didCallDelegate)
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))
        XCTAssertEqual(coordinator.activeTurnGeneration, 0)
    }

    func test06_staleNetworkResponse() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)

        await vm.startVoiceSession()

        MockURLProtocol.requestHandler = { _ in
            Thread.sleep(forTimeInterval: 0.05)
            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Stale late reply" },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: URL(string: "http://localhost:8000/api/v1/chat")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        let task = Task {
            await vm.sendMessage(prompt: "Hello")
        }

        try? await Task.sleep(nanoseconds: 10_000_000)
        // User stops session while request is in-flight
        await vm.stopVoiceSession()

        await task.value

        XCTAssertFalse(mockOutput.spokenTexts.contains("Stale late reply"))
        XCTAssertEqual(vm.voiceState, .idle)
    }

    // =========================================================================
    // SECTION 2: CONFIRMATION INTERPRETER (6 TESTS)
    // =========================================================================

    func test07_exactApproval() {
        let exactPhrases = ["yes", "yeah", "yep", "sure", "confirm", "approve"]
        for phrase in exactPhrases {
            let intent = VoiceConfirmationInterpreter.interpret(transcript: phrase, hasActiveConfirmation: true)
            XCTAssertEqual(intent, .approve, "Failed for phrase: \(phrase)")
        }
    }

    func test08_safeApprovalVariants() {
        let safeVariants = ["yes please", "do it", "go ahead", "okay", "ok", "proceed", "please do", "yes do it", "sounds good"]
        for phrase in safeVariants {
            let intent = VoiceConfirmationInterpreter.interpret(transcript: phrase, hasActiveConfirmation: true)
            XCTAssertEqual(intent, .approve, "Failed for variant: \(phrase)")
        }
    }

    func test09_exactRejection() {
        let exactPhrases = ["no", "nope", "cancel", "reject", "stop", "abort", "discard"]
        for phrase in exactPhrases {
            let intent = VoiceConfirmationInterpreter.interpret(transcript: phrase, hasActiveConfirmation: true)
            XCTAssertEqual(intent, .reject, "Failed for phrase: \(phrase)")
        }
    }

    func test10_safeRejectionVariants() {
        let safeVariants = ["don't do it", "do not do it", "never mind", "nevermind", "no thanks", "don't", "dont"]
        for phrase in safeVariants {
            let intent = VoiceConfirmationInterpreter.interpret(transcript: phrase, hasActiveConfirmation: true)
            XCTAssertEqual(intent, .reject, "Failed for variant: \(phrase)")
        }
    }

    func test11_conditionalModifiedAmbiguity() {
        let ambiguousPhrases = [
            "yes but change the time",
            "yes, make it 5 PM",
            "okay but rename it",
            "sure, also remind me tomorrow",
            "no, change it instead",
            "yes and cancel the other one",
            "do it after lunch",
            "what about 5 PM?",
            "maybe"
        ]
        for phrase in ambiguousPhrases {
            let intent = VoiceConfirmationInterpreter.interpret(transcript: phrase, hasActiveConfirmation: true)
            XCTAssertEqual(intent, .ambiguous(rawTranscript: phrase), "Expected ambiguous for: \(phrase)")
        }
    }

    func test12_noActiveConfirmation() {
        let intent = VoiceConfirmationInterpreter.interpret(transcript: "yes", hasActiveConfirmation: false)
        XCTAssertEqual(intent, .noActiveConfirmation)
    }

    // =========================================================================
    // SECTION 3: CONFIRMATION EXECUTION (5 TESTS)
    // =========================================================================

    func test13_spokenApprove() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "delete_task", arguments: [:], riskLevel: "HIGH", description: "Delete task")

        var didCallConfirm = false
        var sentApproved = false

        MockURLProtocol.requestHandler = { [weak self] request in
            didCallConfirm = true
            let bodyData = self?.extractHTTPBody(from: request) ?? request.httpBody ?? Data()
            let body = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any]
            sentApproved = body?["approved"] as? Bool ?? false

            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Action completed successfully." },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        await vm.voiceSessionDidFinalizeTranscript("yes")

        XCTAssertTrue(didCallConfirm)
        XCTAssertTrue(sentApproved)
        XCTAssertNil(vm.pendingConfirmation)
    }

    func test14_spokenReject() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "delete_task", arguments: [:], riskLevel: "HIGH", description: "Delete task")

        var didCallConfirm = false
        var sentApproved = true

        MockURLProtocol.requestHandler = { [weak self] request in
            didCallConfirm = true
            let bodyData = self?.extractHTTPBody(from: request) ?? request.httpBody ?? Data()
            let body = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any]
            sentApproved = body?["approved"] as? Bool ?? true

            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Action was cancelled." },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        await vm.voiceSessionDidFinalizeTranscript("cancel")

        XCTAssertTrue(didCallConfirm)
        XCTAssertFalse(sentApproved)
        XCTAssertNil(vm.pendingConfirmation)
    }

    func test15_successfulConfirmationTTS() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        await vm.startVoiceSession()

        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "create_reminder", arguments: [:], riskLevel: "LOW", description: "Create reminder")

        MockURLProtocol.requestHandler = { request in
            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Reminder created for tomorrow." },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        // Voice state must be in processing to accept speakResponse
        await vm.respondToConfirmation(approved: true)

        XCTAssertEqual(vm.messages.last?.content, "Reminder created for tomorrow.")
    }

    func test16_confirmation409() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "delete_task", arguments: [:], riskLevel: "HIGH", description: "Delete task")

        MockURLProtocol.requestHandler = { request in
            let json = """
            { "detail": "409 Conflict: active execution expired" }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        await vm.respondToConfirmation(approved: true)

        XCTAssertNil(vm.pendingConfirmation)
        XCTAssertNil(vm.pendingConfirmationId)
        XCTAssertEqual(vm.errorMessage, "The pending action has already been resolved or expired.")
    }

    func test17_ambiguousConfirmation() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        vm.pendingConfirmation = ToolConfirmationDetails(name: "send_email", arguments: [:], riskLevel: "MEDIUM", description: "Send email")

        var networkCalled = false
        MockURLProtocol.requestHandler = { _ in
            networkCalled = true
            throw NSError(domain: "test", code: -1)
        }

        await vm.voiceSessionDidFinalizeTranscript("yes but change the recipient")

        XCTAssertFalse(networkCalled, "Ambiguous confirmation must NEVER call network")
        XCTAssertNotNil(vm.pendingConfirmation)
    }

    // =========================================================================
    // SECTION 4: CHANNEL & FENCING (4 TESTS)
    // =========================================================================

    func test18_textWhileTTSSpeaking() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        await vm.startVoiceSession()

        // Put coordinator in processing then speaking
        mockOutput.shouldWaitUntilStopped = true
        let speakTask = Task {
            // Force state to processing to accept speakResponse
            await coordinator.speakResponse("Long speech playing right now...")
        }
        try? await Task.sleep(nanoseconds: 10_000_000)

        MockURLProtocol.requestHandler = { request in
            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Text reply" },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        // Submitting typed text stops active speech
        vm.currentInput = "Interrupting text"
        await vm.sendMessage()

        await speakTask.value
        XCTAssertGreaterThan(mockOutput.stopSpeakingCalledCount, 0)
    }

    func test19_manualConfirmationInteraction() async {
        let vm = AssistantViewModel(apiClient: apiClient)
        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "archive", arguments: [:], riskLevel: "LOW", description: "Archive item")

        MockURLProtocol.requestHandler = { request in
            let json = """
            {
                "type": "message",
                "conversation_id": "conv-1",
                "message": { "role": "assistant", "content": "Archived." },
                "provider": "test",
                "model": "test"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, json)
        }

        await vm.respondToConfirmation(approved: true)
        XCTAssertNil(vm.pendingConfirmation)
        XCTAssertNil(vm.pendingConfirmationId)
    }

    func test20_inFlightTextBlocksVoice() async {
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        vm.isSending = true

        await vm.startVoiceSession()

        XCTAssertEqual(coordinator.state, .idle, "Voice session initiation must be blocked while isSending == true")
    }

    func test21_doubleConfirmationFencing() async {
        let vm = AssistantViewModel(apiClient: apiClient)
        vm.conversationId = "conv-1"
        vm.activeExecutionId = "exec-1"
        vm.pendingConfirmationId = "conf-1"
        vm.pendingConfirmation = ToolConfirmationDetails(name: "pay", arguments: [:], riskLevel: "HIGH", description: "Pay invoice")

        var callCount = 0
        MockURLProtocol.requestHandler = { request in
            callCount += 1
            if callCount == 1 {
                let json = """
                {
                    "type": "message",
                    "conversation_id": "conv-1",
                    "message": { "role": "assistant", "content": "Paid." },
                    "provider": "test",
                    "model": "test"
                }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, json)
            } else {
                let json = """
                { "detail": "409 Conflict: Already executed" }
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!, json)
            }
        }

        // First confirmation succeeds
        await vm.respondToConfirmation(approved: true)
        XCTAssertNil(vm.pendingConfirmationId)

        // Second confirmation cannot proceed because pendingConfirmationId is already nil
        await vm.respondToConfirmation(approved: true)
        XCTAssertEqual(callCount, 1)
    }

    // =========================================================================
    // SECTION 5: TTS SANITIZATION IN CONTEXT (3 TESTS)
    // =========================================================================

    func test22_confirmationResponseStripsIdsParameters() {
        let raw = """
        Action confirmed.
        ```json
        {"execution_id": "exec-123", "room_id": 42}
        ```
        Everything is set.
        """
        let sanitized = TTSTextSanitizer.sanitizeForSpeech(raw)
        XCTAssertFalse(sanitized.contains("exec-123"))
        XCTAssertTrue(sanitized.contains("Everything is set."))
        XCTAssertTrue(sanitized.contains("code snippet omitted."))
    }

    func test23_errorSpeechSanitization() {
        let rawError = "Error: Failed to fetch from https://api.internal.aurora/v1/auth/token at 2026-10-08T10:00:00Z."
        let sanitized = TTSTextSanitizer.sanitizeForSpeech(rawError)
        XCTAssertFalse(sanitized.contains("https://"))
        XCTAssertTrue(sanitized.contains("link to api.internal.aurora"))
        XCTAssertTrue(sanitized.contains("October 8, 2026"))
    }

    func test24_citationStripping() {
        let rawWithCitations = "The meeting was rescheduled to 3 PM [doc:1] [source:calendar] [42]."
        let sanitized = TTSTextSanitizer.sanitizeForSpeech(rawWithCitations)
        XCTAssertFalse(sanitized.contains("[doc:1]"))
        XCTAssertFalse(sanitized.contains("[source:calendar]"))
        XCTAssertFalse(sanitized.contains("[42]"))
        XCTAssertTrue(sanitized.contains("The meeting was rescheduled to 3 PM"))
    }

    // =========================================================================
    // SECTION 6: RECOVERY & LIFECYCLE (4 TESTS)
    // =========================================================================

    func test25_networkFailure() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )
        let vm = AssistantViewModel(apiClient: apiClient, voiceCoordinator: coordinator)
        await vm.startVoiceSession()

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet, userInfo: [NSLocalizedDescriptionKey: "The Internet connection appears to be offline."])
        }

        await vm.sendMessage(prompt: "Hello")

        XCTAssertNotNil(vm.errorMessage)
        XCTAssertTrue(vm.errorMessage?.contains("offline") == true)
    }

    func test26_audioRouteDisconnect() async {
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager()
        )
        await coordinator.startSession()
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))

        coordinator.handleAudioRouteChange(reason: "Bluetooth headphones disconnected")

        if case .failed(let err) = coordinator.state {
            XCTAssertEqual(err, .audioRouteChange("Bluetooth headphones disconnected"))
        } else {
            XCTFail("Expected .failed state, got \(coordinator.state)")
        }
    }

    func test27_incomingCallInterruption() async {
        let coordinator = VoiceSessionCoordinator(
            captureManager: MockConversationSpeechCaptureManager(),
            outputManager: MockConversationSpeechOutputManager(),
            permissionManager: MockConversationVoicePermissionManager()
        )
        await coordinator.startSession()

        coordinator.handleAudioSessionInterruption(began: true)
        XCTAssertEqual(coordinator.state, .interrupted)

        coordinator.handleAudioSessionInterruption(began: false)
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))
    }

    func test28_bargeIn() async {
        let mockOutput = MockConversationSpeechOutputManager()
        let mockCapture = MockConversationSpeechCaptureManager()
        let coordinator = VoiceSessionCoordinator(
            captureManager: mockCapture,
            outputManager: mockOutput,
            permissionManager: MockConversationVoicePermissionManager()
        )

        await coordinator.startSession()
        mockCapture.emitResult(SpeechTranscriptionResult(text: "Query", isFinal: true))
        try? await Task.sleep(nanoseconds: 10_000_000)

        mockOutput.shouldWaitUntilStopped = true
        let speakTask = Task {
            await coordinator.speakResponse("Assistant response that is currently being spoken.")
        }
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertTrue(mockOutput.isSpeaking)

        // User speaks -> barge in
        coordinator.handleDetectedSpeechActivity()

        await speakTask.value
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertFalse(mockOutput.isSpeaking)
        XCTAssertEqual(coordinator.state, .listening(amplitude: 0.0))
        XCTAssertEqual(coordinator.activeTurnGeneration, 2)
    }
}
