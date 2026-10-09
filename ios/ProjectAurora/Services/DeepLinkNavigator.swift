import Foundation

/// Strongly typed, deterministic route representation for Project Aurora.
public enum AuroraRoute: Equatable, Sendable {
    case voice
    case agenda
    case tasks
    case commitments
    case reminders
    case chat(prompt: String)
    case confirmation(id: String)
}

/// Single navigation state authority for Project Aurora deep-links.
@MainActor
public final class DeepLinkNavigator: ObservableObject {
    public static let shared = DeepLinkNavigator()

    @Published public var activeRoute: AuroraRoute?

    private static let supportedHosts: Set<String> = [
        "voice",
        "agenda",
        "tasks",
        "commitments",
        "reminders",
        "chat",
        "confirm"
    ]

    private static let forbiddenQueryKeys: Set<String> = [
        "user_id", "userid", "user", "identity",
        "token", "auth", "credential", "bearer", "key", "password", "secret",
        "tool", "tool_name", "tools",
        "args", "arguments", "params",
        "execute", "run", "action", "send", "eval", "dispatch", "directive"
    ]

    public init() {}

    /// Directly transitions active navigation to a strongly typed route.
    public func handle(route: AuroraRoute) {
        self.activeRoute = route
    }

    /// Clears the active navigation route once consumed.
    public func clearRoute() {
        self.activeRoute = nil
    }

    /// Validates and parses an incoming deep link URL according to security and routing policies.
    ///
    /// - Parameter url: The incoming URL.
    /// - Returns: `true` if the URL is authorized, well-formed, and mapped to an active route; `false` otherwise.
    @discardableResult
    public func handle(url: URL) -> Bool {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return false
        }

        // 1. Accept only scheme == "aurora"
        guard let scheme = components.scheme?.lowercased(), scheme == "aurora" else {
            return false
        }

        // 2. Reject unsupported paths
        let path = components.path
        guard path.isEmpty || path == "/" else {
            return false
        }

        // 3. Enforce host existence and route whitelist
        guard let host = components.host?.lowercased(), Self.supportedHosts.contains(host) else {
            return false
        }

        let queryItems = components.queryItems ?? []

        // 4. Reject identity injection, credentials, tool names, tool arguments, execution directives
        for item in queryItems {
            let key = item.name.lowercased()
            if Self.forbiddenQueryKeys.contains(key) {
                return false
            }
        }

        // 5. Route-specific parsing and parameter whitelisting
        switch host {
        case "voice":
            guard queryItems.isEmpty else { return false }
            self.activeRoute = .voice
            return true

        case "agenda":
            guard queryItems.isEmpty else { return false }
            self.activeRoute = .agenda
            return true

        case "tasks":
            guard queryItems.isEmpty else { return false }
            self.activeRoute = .tasks
            return true

        case "commitments":
            guard queryItems.isEmpty else { return false }
            self.activeRoute = .commitments
            return true

        case "reminders":
            guard queryItems.isEmpty else { return false }
            self.activeRoute = .reminders
            return true

        case "chat":
            guard queryItems.count == 1,
                  let promptItem = queryItems.first(where: { $0.name == "prompt" }),
                  let prompt = promptItem.value,
                  !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return false
            }
            self.activeRoute = .chat(prompt: prompt)
            return true

        case "confirm":
            guard queryItems.count == 1,
                  let idItem = queryItems.first(where: { $0.name == "id" }),
                  let id = idItem.value,
                  !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return false
            }
            self.activeRoute = .confirmation(id: id)
            return true

        default:
            return false
        }
    }
}
