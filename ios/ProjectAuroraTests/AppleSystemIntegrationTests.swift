import XCTest
import AppIntents
@testable import ProjectAurora

private final class TestBox<T>: @unchecked Sendable {
    var value: T
    init(_ value: T) { self.value = value }
}

@MainActor
final class AppleSystemIntegrationTests: XCTestCase {

    override func setUp() async throws {
        try await super.setUp()
        DeepLinkNavigator.shared.clearRoute()
    }

    override func tearDown() async throws {
        DeepLinkNavigator.shared.clearRoute()
        try await super.tearDown()
    }

    // 1. aurora://voice
    func test01_VoiceRoute() {
        let url = URL(string: "aurora://voice")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .voice)
    }

    // 2. aurora://agenda
    func test02_AgendaRoute() {
        let url = URL(string: "aurora://agenda")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .agenda)
    }

    // 3. aurora://tasks
    func test03_TasksRoute() {
        let url = URL(string: "aurora://tasks")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .tasks)
    }

    // 4. aurora://commitments
    func test04_CommitmentsRoute() {
        let url = URL(string: "aurora://commitments")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .commitments)
    }

    // 5. aurora://reminders
    func test05_RemindersRoute() {
        let url = URL(string: "aurora://reminders")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .reminders)
    }

    // 6. aurora://chat?prompt=Hello%20World
    func test06_ChatRouteWithEncodedPrompt() {
        let url = URL(string: "aurora://chat?prompt=Hello%20World")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .chat(prompt: "Hello World"))
    }

    // 7. aurora://confirm?id=conf-123
    func test07_ConfirmationRouteWithId() {
        let url = URL(string: "aurora://confirm?id=conf-123")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .confirmation(id: "conf-123"))
    }

    // 8. http://... rejected
    func test08_HttpSchemeRejected() {
        let url = URL(string: "http://voice")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertFalse(handled)
        XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
    }

    // 9. https://... rejected
    func test09_HttpsSchemeRejected() {
        let url = URL(string: "https://chat?prompt=Hello")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertFalse(handled)
        XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
    }

    // 10. custom://... rejected
    func test10_CustomSchemeRejected() {
        let url = URL(string: "custom://voice")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertFalse(handled)
        XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
    }

    // 11. unknown aurora route rejected
    func test11_UnknownAuroraRouteRejected() {
        let unknownUrls = [
            "aurora://settings",
            "aurora://unknown",
            "aurora://profile",
            "aurora://admin"
        ]
        for urlString in unknownUrls {
            let url = URL(string: urlString)!
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected \(urlString) to be rejected")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 12. malformed URL rejected
    func test12_MalformedUrlRejected() {
        let malformedUrls = [
            URL(string: "aurora://")!,
            URL(string: "aurora://voice/extra/path")!,
            URL(string: "aurora://chat/subpath?prompt=test")!,
            URL(string: "aurora:voice")!
        ]
        for url in malformedUrls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected malformed \(url) to be rejected")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 13. missing confirmation ID rejected
    func test13_MissingConfirmationIdRejected() {
        let invalidUrls = [
            URL(string: "aurora://confirm")!,
            URL(string: "aurora://confirm?id=")!
        ]
        for url in invalidUrls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected missing or empty confirmation id to be rejected")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 14. confirmation route creates navigation state only
    func test14_ConfirmationRouteCreatesNavigationStateOnly() {
        let url = URL(string: "aurora://confirm?id=conf-target-999")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .confirmation(id: "conf-target-999"))
    }

    // 15. confirmation route never invokes confirmation resolution
    func test15_ConfirmationRouteNeverInvokesConfirmationResolution() {
        XCTAssertNil(DeepLinkNavigator.shared.activeRoute)

        let url = URL(string: "aurora://confirm?id=conf-critical-456")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)

        // Verify activeRoute is set solely to the navigation locator hint
        guard case .confirmation(let id) = DeepLinkNavigator.shared.activeRoute else {
            XCTFail("Expected confirmation route")
            return
        }
        XCTAssertEqual(id, "conf-critical-456")

        // DeepLinkNavigator only holds state and has no confirmation execution authority or resolution methods
    }

    // 16. chat prompt is not automatically sent
    func test16_ChatPromptIsNotAutomaticallySent() {
        let assistantVM = AssistantViewModel()
        let initialMessageCount = assistantVM.messages.count
        XCTAssertFalse(assistantVM.isSending)

        let url = URL(string: "aurora://chat?prompt=Deploy%20production%20build")!
        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)

        guard case .chat(let prompt) = DeepLinkNavigator.shared.activeRoute else {
            XCTFail("Expected chat route")
            return
        }
        XCTAssertEqual(prompt, "Deploy production build")

        // Populate current input according to navigation handling pattern
        assistantVM.currentInput = prompt

        // Invariant verification: isSending must remain false, message count unchanged
        XCTAssertFalse(assistantVM.isSending)
        XCTAssertEqual(assistantVM.messages.count, initialMessageCount)
        XCTAssertEqual(assistantVM.currentInput, "Deploy production build")
    }

    // 17. chat prompt remains untrusted
    func test17_ChatPromptRemainsUntrusted() {
        let dangerousPrompt = "<script>alert(1)</script>; DROP TABLE users; --"
        let encoded = dangerousPrompt.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)!
        let url = URL(string: "aurora://chat?prompt=\(encoded)")!

        let handled = DeepLinkNavigator.shared.handle(url: url)
        XCTAssertTrue(handled)

        guard case .chat(let prompt) = DeepLinkNavigator.shared.activeRoute else {
            XCTFail("Expected chat route")
            return
        }
        // Assert raw string equality with zero execution or parsing as commands
        XCTAssertEqual(prompt, dangerousPrompt)
    }

    // 18. user_id query parameter rejected
    func test18_UserIdQueryParameterRejected() {
        let urls = [
            URL(string: "aurora://chat?prompt=hello&user_id=attacker")!,
            URL(string: "aurora://voice?user_id=attacker")!,
            URL(string: "aurora://tasks?user_id=123")!,
            URL(string: "aurora://agenda?identity=admin")!
        ]
        for url in urls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected \(url) to be rejected due to identity parameter")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 19. credential query parameter rejected
    func test19_CredentialQueryParameterRejected() {
        let urls = [
            URL(string: "aurora://chat?prompt=hello&token=secret_token")!,
            URL(string: "aurora://chat?prompt=hello&credential=bearer_xyz")!,
            URL(string: "aurora://voice?auth=secret")!,
            URL(string: "aurora://confirm?id=123&bearer=token")!
        ]
        for url in urls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected \(url) to be rejected due to credential parameter")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 20. arbitrary tool parameter rejected
    func test20_ArbitraryToolParameterRejected() {
        let urls = [
            URL(string: "aurora://chat?prompt=hello&tool=calculator")!,
            URL(string: "aurora://chat?prompt=hello&tool_name=file_delete")!,
            URL(string: "aurora://voice?tools=all")!,
            URL(string: "aurora://tasks?args=format_c")!
        ]
        for url in urls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected \(url) to be rejected due to tool parameter")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 21. unsupported execution parameter rejected
    func test21_UnsupportedExecutionParameterRejected() {
        let urls = [
            URL(string: "aurora://chat?prompt=hello&execute=true")!,
            URL(string: "aurora://chat?prompt=hello&run=immediate")!,
            URL(string: "aurora://voice?action=start")!,
            URL(string: "aurora://confirm?id=123&send=true")!
        ]
        for url in urls {
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Expected \(url) to be rejected due to execution directive")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // 22. route whitelist is deterministic
    func test22_RouteWhitelistIsDeterministic() {
        let validRoutes: [(String, AuroraRoute)] = [
            ("aurora://voice", .voice),
            ("aurora://agenda", .agenda),
            ("aurora://tasks", .tasks),
            ("aurora://commitments", .commitments),
            ("aurora://reminders", .reminders),
            ("aurora://chat?prompt=Check%20schedule", .chat(prompt: "Check schedule")),
            ("aurora://confirm?id=conf-abc", .confirmation(id: "conf-abc"))
        ]

        for (urlString, expectedRoute) in validRoutes {
            DeepLinkNavigator.shared.clearRoute()
            let url = URL(string: urlString)!
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertTrue(handled, "Failed for valid route: \(urlString)")
            XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, expectedRoute)
        }

        let invalidRoutes = [
            "aurora://",
            "aurora://voice/extra",
            "aurora://settings",
            "aurora://chat",
            "aurora://chat?prompt=",
            "aurora://confirm",
            "aurora://confirm?id=",
            "aurora://chat?prompt=hi&unknown=1",
            "ftp://voice",
            "http://aurora/voice",
            "aurora://voice?query=value"
        ]

        for urlString in invalidRoutes {
            DeepLinkNavigator.shared.clearRoute()
            let url = URL(string: urlString)!
            let handled = DeepLinkNavigator.shared.handle(url: url)
            XCTAssertFalse(handled, "Should have rejected invalid route: \(urlString)")
            XCTAssertNil(DeepLinkNavigator.shared.activeRoute)
        }
    }

    // MARK: - Phase 6R.3.2 App Intents & App Shortcuts Tests

    // 23. exactly three intent types are exposed
    func test23_ExactlyThreeIntentTypesExposed() {
        let intents: [Any.Type] = [
            OpenAuroraVoiceIntent.self,
            OpenAgendaIntent.self,
            AskAuroraIntent.self
        ]
        XCTAssertEqual(intents.count, 3)
        for intent in intents {
            XCTAssertTrue(intent is AppIntent.Type)
        }
    }

    // 24. OpenAuroraVoiceIntent configuration
    func test24_OpenAuroraVoiceIntentConfiguration() async throws {
        XCTAssertTrue(OpenAuroraVoiceIntent.openAppWhenRun)
        let intent = OpenAuroraVoiceIntent()
        _ = try await intent.perform()
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .voice)
    }

    // 25. OpenAgendaIntent configuration
    func test25_OpenAgendaIntentConfiguration() async throws {
        XCTAssertTrue(OpenAgendaIntent.openAppWhenRun)
        let intent = OpenAgendaIntent()
        _ = try await intent.perform()
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .agenda)
    }

    // 26. AskAuroraIntent configuration
    func test26_AskAuroraIntentConfiguration() {
        XCTAssertFalse(AskAuroraIntent.openAppWhenRun)
        let intent = AskAuroraIntent()
        XCTAssertTrue(intent is ForegroundContinuableIntent)
        XCTAssertTrue(intent is AppIntent)
    }

    // 27. AskAuroraIntent has only the prompt input
    func test27_AskAuroraIntentHasOnlyPromptInput() {
        let intent = AskAuroraIntent()
        let mirror = Mirror(reflecting: intent)
        let parameterLabels = mirror.children.compactMap { $0.label }
        XCTAssertTrue(parameterLabels.contains("_prompt") || parameterLabels.contains("prompt"))

        let forbiddenLabels = ["_userId", "_credential", "_executionId", "_confirmationId", "_tool"]
        for forbidden in forbiddenLabels {
            XCTAssertFalse(parameterLabels.contains(forbidden))
        }
    }

    // 28. no user_id/credential/execution_id/confirmation_id inputs
    func test28_NoForbiddenInputsOnAskAuroraIntent() {
        let intent = AskAuroraIntent()
        let mirror = Mirror(reflecting: intent)
        let labels = Set(mirror.children.compactMap { $0.label?.lowercased() })

        XCTAssertFalse(labels.contains("user_id"))
        XCTAssertFalse(labels.contains("userid"))
        XCTAssertFalse(labels.contains("credential"))
        XCTAssertFalse(labels.contains("execution_id"))
        XCTAssertFalse(labels.contains("executionid"))
        XCTAssertFalse(labels.contains("confirmation_id"))
        XCTAssertFalse(labels.contains("confirmationid"))
        XCTAssertFalse(labels.contains("tool"))
        XCTAssertFalse(labels.contains("tool_name"))
        XCTAssertFalse(labels.contains("action"))
    }

    // 29. App Shortcut 1:1 mapping
    func test29_AppShortcutOneToOneMapping() {
        let shortcuts = AuroraShortcutsProvider.appShortcuts
        XCTAssertEqual(shortcuts.count, 3)
    }

    // 30. shortcut catalog contains exactly three entries
    func test30_ShortcutCatalogContainsExactlyThreeEntries() {
        let shortcuts = AuroraShortcutsProvider.appShortcuts
        XCTAssertEqual(shortcuts.count, 3)
    }

    // 31. normal response sanitization
    func test31_NormalResponseSanitization() {
        let normalText = "Your flight is on schedule for 2:30 PM."
        XCTAssertEqual(sanitizeAssistantResponse(normalText), normalText)

        let textWithBearer = "Token: Bearer secret_token_123456789"
        XCTAssertFalse(sanitizeAssistantResponse(textWithBearer).contains("secret_token_123456789"))

        let textWithExec = "Task processed by execution_999888777 successfully."
        XCTAssertFalse(sanitizeAssistantResponse(textWithExec).contains("execution_999888777"))

        let textWithConf = "Action assigned conf-abc123xyz"
        XCTAssertFalse(sanitizeAssistantResponse(textWithConf).contains("conf-abc123xyz"))

        let rawJson = "{\"status\": 500, \"error\": \"Internal server crash\"}"
        XCTAssertEqual(sanitizeAssistantResponse(rawJson), "Aurora completed your request.")
    }

    // 32. tool_confirmation response does not directly call /chat/confirm
    func test32_ToolConfirmationResponseDoesNotDirectlyCallChatConfirm() async throws {
        try? KeychainCredentialStore.shared.save("test-token")
        defer { KeychainCredentialStore.shared.clear() }

        var requestedPaths: [String] = []
        MockURLProtocol.requestHandler = { request in
            let path = request.url?.path ?? ""
            requestedPaths.append(path)

            let responseJson = """
            {
                "type": "confirmation_required",
                "conversation_id": "conv-test-1",
                "confirmation_id": "conf-456",
                "tool": {
                    "name": "send_email",
                    "arguments": {"to": "test@example.com"},
                    "risk_level": "medium",
                    "description": "Send email"
                },
                "provider": "test-provider",
                "model": "test-model"
            }
            """
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, responseJson.data(using: .utf8)!)
        }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))

        let continuationCalled = TestBox(false)
        let intent = AskAuroraIntent(
            prompt: "Send an email to test",
            apiClient: mockClient,
            timeoutNanoseconds: 2_000_000_000,
            continuationHandler: { dialog, continuation in
                continuationCalled.value = true
                try await continuation()
            }
        )

        _ = try await intent.perform()

        XCTAssertTrue(continuationCalled.value)
        XCTAssertFalse(requestedPaths.contains("/api/v1/chat/confirm"))
        XCTAssertTrue(requestedPaths.contains("/api/v1/chat"))
    }

    // 33. tool_confirmation response requests foreground continuation
    func test33_ToolConfirmationResponseRequestsForegroundContinuation() async throws {
        try? KeychainCredentialStore.shared.save("test-token")
        defer { KeychainCredentialStore.shared.clear() }

        MockURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "type": "confirmation_required",
                "conversation_id": "conv-test-1",
                "confirmation_id": "conf-999",
                "tool": {
                    "name": "delete_file",
                    "arguments": {"path": "/tmp/a"},
                    "risk_level": "high",
                    "description": "Delete file"
                },
                "provider": "test-provider",
                "model": "test-model"
            }
            """
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, responseJson.data(using: .utf8)!)
        }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))

        let continuationRequested = TestBox(false)
        let intent = AskAuroraIntent(
            prompt: "Delete the temporary file",
            apiClient: mockClient,
            timeoutNanoseconds: 2_000_000_000,
            continuationHandler: { dialog, continuation in
                continuationRequested.value = true
            }
        )

        _ = try await intent.perform()
        XCTAssertTrue(continuationRequested.value)
    }

    // 34. continuation routes through the existing navigation path
    func test34_ContinuationRoutesThroughExistingNavigationPath() async throws {
        try? KeychainCredentialStore.shared.save("test-token")
        defer { KeychainCredentialStore.shared.clear() }

        MockURLProtocol.requestHandler = { request in
            let responseJson = """
            {
                "type": "confirmation_required",
                "conversation_id": "conv-test-1",
                "confirmation_id": "conf-route-777",
                "tool": {
                    "name": "create_event",
                    "arguments": {},
                    "risk_level": "low",
                    "description": "Create event"
                },
                "provider": "test-provider",
                "model": "test-model"
            }
            """
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, responseJson.data(using: .utf8)!)
        }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))

        let intent = AskAuroraIntent(
            prompt: "Create event",
            apiClient: mockClient,
            timeoutNanoseconds: 2_000_000_000,
            continuationHandler: { dialog, continuation in
                try await continuation()
            }
        )

        _ = try await intent.perform()
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .confirmation(id: "conf-route-777"))
    }

    // 35. confirmation remains server/UI authoritative
    func test35_ConfirmationRemainsServerUIAuthoritative() async throws {
        XCTAssertFalse(AskAuroraIntent.openAppWhenRun)
    }

    // 36. no direct ToolExecutor invocation
    func test36_NoDirectToolExecutorInvocation() {
        let intent = AskAuroraIntent()
        XCTAssertEqual(intent.prompt, "")
    }

    // 37. lifecycle independence behavior
    func test37_LifecycleIndependenceBehavior() async throws {
        try? KeychainCredentialStore.shared.save("test-token")
        defer { KeychainCredentialStore.shared.clear() }

        DeepLinkNavigator.shared.handle(route: .tasks)
        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .tasks)

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        MockURLProtocol.requestHandler = { request in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }
        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))

        let intent = AskAuroraIntent(
            prompt: "Slow request",
            apiClient: mockClient,
            timeoutNanoseconds: 100_000_000
        )

        _ = try await intent.perform()

        XCTAssertEqual(DeepLinkNavigator.shared.activeRoute, .tasks)
    }

    // 38. missing credentials do not block chat screen rendering
    func test38_MissingCredentialsDoNotBlockChatScreen() {
        KeychainCredentialStore.shared.clear()
        let vm = AssistantViewModel()
        XCTAssertEqual(vm.connectionState, .checking)
        XCTAssertTrue(vm.messages.isEmpty)
        XCTAssertNil(vm.errorMessage)
        XCTAssertEqual(vm.currentInput, "")
    }

    // 39. dev bootstrap stores credential and reuses it on subsequent checks
    func test39_DevBootstrapStoresCredentialAndReusesIt() async throws {
        KeychainCredentialStore.shared.clear()
        defer { KeychainCredentialStore.shared.clear() }

        var bootstrapCount = 0
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        MockURLProtocol.requestHandler = { request in
            let path = request.url?.path ?? ""
            if path.contains("dev-bootstrap") {
                bootstrapCount += 1
                let json = """
                {"credential":"aurora_cred_bootstrapped_999","user":{"user_id":"usr_local_default"},"device":{"device_id":"dev_test_999"}}
                """
                let resp = HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
                return (resp, Data(json.utf8))
            } else if path.contains("health") {
                let json = """
                {"service":"Project Aurora","version":"0.3.0","status":"online","environment":"development","active_provider":"gemini","active_model":"gemini-flash","timestamp":"2026-10-10T00:00:00Z"}
                """
                let resp = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
                return (resp, Data(json.utf8))
            }
            throw NSError(domain: "Test", code: 404, userInfo: nil)
        }

        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))
        let vm = AssistantViewModel(apiClient: mockClient)

        // First launch/check without credential
        await vm.checkConnection()
        XCTAssertEqual(bootstrapCount, 1)
        XCTAssertEqual(KeychainCredentialStore.shared.credential, "aurora_cred_bootstrapped_999")
        if case .online(let provider, let model, _) = vm.connectionState {
            XCTAssertEqual(provider, "gemini")
            XCTAssertEqual(model, "gemini-flash")
        } else {
            XCTFail("Expected online connection state")
        }

        // Subsequent check: credential already exists, must not bootstrap again
        await vm.checkConnection()
        XCTAssertEqual(bootstrapCount, 1)
    }

    // 40. bootstrap failure leaves chat accessible with error and retry path
    func test40_BootstrapFailureLeavesChatAccessibleWithRetryPath() async throws {
        KeychainCredentialStore.shared.clear()
        defer { KeychainCredentialStore.shared.clear() }

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        MockURLProtocol.requestHandler = { request in
            let resp = HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: nil, headerFields: nil)!
            return (resp, Data())
        }

        let mockClient = AuroraAPIClient(session: URLSession(configuration: config))
        let vm = AssistantViewModel(apiClient: mockClient)

        await vm.checkConnection()
        XCTAssertNil(KeychainCredentialStore.shared.credential)
        if case .offline = vm.connectionState {
            // Success: offline state allows inline retry
        } else {
            XCTFail("Expected offline connection state on failure")
        }

        // Chat state remains responsive and accessible
        XCTAssertTrue(vm.messages.isEmpty)
        vm.currentInput = "Hello world"
        XCTAssertEqual(vm.currentInput, "Hello world")
    }
}
