import Foundation
import AppIntents

// MARK: - 1. OpenAuroraVoiceIntent

/// Foreground App Intent to launch Project Aurora directly into the voice session experience.
public struct OpenAuroraVoiceIntent: AppIntent {
    public static var title: LocalizedStringResource = "Open Voice in Aurora"
    public static var description: IntentDescription = IntentDescription("Opens Project Aurora and activates the voice conversation experience.")
    public static var openAppWhenRun: Bool = true

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult {
        DeepLinkNavigator.shared.handle(route: .voice)
        return .result()
    }
}

// MARK: - 2. OpenAgendaIntent

/// Foreground App Intent to launch Project Aurora directly to the daily agenda timeline.
public struct OpenAgendaIntent: AppIntent {
    public static var title: LocalizedStringResource = "Open Aurora Agenda"
    public static var description: IntentDescription = IntentDescription("Opens Project Aurora directly to your daily agenda timeline.")
    public static var openAppWhenRun: Bool = true

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult {
        DeepLinkNavigator.shared.handle(route: .agenda)
        return .result()
    }
}

// MARK: - 3. AskAuroraIntent

/// Background App Intent conforming to ForegroundContinuableIntent.
/// Allows natural language interaction via Siri/Shortcuts with foreground handoff for confirmations.
public struct AskAuroraIntent: AppIntent, ForegroundContinuableIntent {
    public static var title: LocalizedStringResource = "Ask Aurora"
    public static var description: IntentDescription = IntentDescription("Send a natural-language request to Project Aurora.")
    public static var openAppWhenRun: Bool = false

    /// The sole user-provided input. Untrusted natural language text.
    @Parameter(title: "Prompt")
    public var prompt: String

    // Internal seams for deterministic testing and dependency injection
    internal var apiClient: AuroraAPIClient
    internal var timeoutNanoseconds: UInt64
    internal var foregroundContinuationHandler: (@Sendable (IntentDialog, @escaping @MainActor () async throws -> Void) async throws -> Void)?

    public init() {
        self.apiClient = AuroraAPIClient()
        self.timeoutNanoseconds = 8_000_000_000 // Aurora-owned 8-second client timeout
        self.foregroundContinuationHandler = nil
        self.prompt = ""
    }

    public init(
        prompt: String,
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        timeoutNanoseconds: UInt64 = 8_000_000_000,
        continuationHandler: (@Sendable (IntentDialog, @escaping @MainActor () async throws -> Void) async throws -> Void)? = nil
    ) {
        self.apiClient = apiClient
        self.timeoutNanoseconds = timeoutNanoseconds
        self.foregroundContinuationHandler = continuationHandler
        self.prompt = prompt
    }

    public func perform() async throws -> some IntentResult & ProvidesDialog {
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            return .result(dialog: IntentDialog("Please provide a request for Aurora."))
        }

        // Authenticate using existing KeychainCredentialStore
        guard let credential = KeychainCredentialStore.shared.credential, !credential.isEmpty else {
            return .result(dialog: IntentDialog("Aurora authentication required. Please open the Aurora app to sign in."))
        }

        // Execute chat call bounded by Aurora's 8-second client-side timeout
        let response: ChatResponsePayload
        do {
            response = try await withThrowingTaskGroup(of: ChatResponsePayload.self) { group in
                group.addTask {
                    try await self.apiClient.sendChatMessage(message: cleanPrompt)
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: self.timeoutNanoseconds)
                    throw AskAuroraIntentError.clientTimeout
                }
                guard let firstResult = try await group.next() else {
                    throw AskAuroraIntentError.networkError("No response received")
                }
                group.cancelAll()
                return firstResult
            }
        } catch AskAuroraIntentError.clientTimeout {
            return .result(dialog: IntentDialog("Aurora request timed out. Please try again."))
        } catch APIError.authenticationRequired {
            return .result(dialog: IntentDialog("Aurora authentication required. Please open the Aurora app."))
        } catch {
            return .result(dialog: IntentDialog("Unable to complete request: \(error.localizedDescription)"))
        }

        // Check for tool confirmation requirement
        if response.type == "confirmation_required" || response.confirmationId != nil,
           let confId = response.confirmationId {
            let toolName = response.tool?.name ?? "an action"
            let dialog = IntentDialog("Aurora needs your authorization to execute \(toolName). Opening Aurora...")

            if let customHandler = foregroundContinuationHandler {
                try await customHandler(dialog) {
                    DeepLinkNavigator.shared.handle(route: .confirmation(id: confId))
                }
            } else {
                try await requestToContinueInForeground(dialog) {
                    DeepLinkNavigator.shared.handle(route: .confirmation(id: confId))
                }
            }
            return .result(dialog: dialog)
        }

        // Normal response: return sanitized assistant response
        if let msg = response.message?.content, !msg.isEmpty {
            let sanitized = sanitizeAssistantResponse(msg)
            return .result(dialog: IntentDialog(stringLiteral: sanitized))
        }

        return .result(dialog: IntentDialog("Aurora completed your request."))
    }
}

// MARK: - Sanitization & Errors

internal func sanitizeAssistantResponse(_ raw: String) -> String {
    var sanitized = raw.trimmingCharacters(in: .whitespacesAndNewlines)

    // Omit raw internal JSON payloads
    if sanitized.hasPrefix("{") && sanitized.hasSuffix("}") {
        return "Aurora completed your request."
    }

    // Strip credentials, bearer tokens, or internal correlation IDs
    sanitized = sanitized.replacingOccurrences(of: "Bearer [A-Za-z0-9_-]+", with: "[redacted]", options: .regularExpression)
    sanitized = sanitized.replacingOccurrences(of: "execution_[a-zA-Z0-9_-]+", with: "", options: .regularExpression)
    sanitized = sanitized.replacingOccurrences(of: "conf-[a-zA-Z0-9_-]+", with: "", options: .regularExpression)

    return sanitized.isEmpty ? "Aurora completed your request." : sanitized
}

public enum AskAuroraIntentError: Error, LocalizedError {
    case clientTimeout
    case missingPrompt
    case authenticationRequired
    case networkError(String)

    public var errorDescription: String? {
        switch self {
        case .clientTimeout:
            return "Aurora request timed out. Please try again."
        case .missingPrompt:
            return "Please provide a request for Aurora."
        case .authenticationRequired:
            return "Aurora authentication required. Please open the Aurora app to sign in."
        case .networkError(let msg):
            return "Unable to reach Aurora: \(msg)"
        }
    }
}
