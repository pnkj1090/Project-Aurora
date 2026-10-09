import Foundation

public enum ContextSensitivity: String, Codable, CaseIterable, Sendable {
    case publicSensitivity = "public"
    case privateSensitivity = "private"
    case sensitive = "sensitive"

    public var displayName: String {
        switch self {
        case .publicSensitivity: return "Public"
        case .privateSensitivity: return "Private"
        case .sensitive: return "Sensitive"
        }
    }
}
