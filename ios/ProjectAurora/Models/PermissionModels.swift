import Foundation

public enum PermissionType: String, Codable, CaseIterable, Identifiable, Sendable {
    case location
    case calendar
    case notifications
    case contacts
    case reminders
    case photosFiles = "photos_files"

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .location: return "Location"
        case .calendar: return "Calendar"
        case .notifications: return "Notifications"
        case .contacts: return "Contacts"
        case .reminders: return "Reminders"
        case .photosFiles: return "Photos & Files"
        }
    }
}

public enum PermissionStatus: String, Codable, CaseIterable, Sendable {
    case notDetermined = "not_determined"
    case denied
    case authorized
    case restricted
    case unavailable

    public var isAuthorized: Bool {
        return self == .authorized
    }
}

public struct PermissionRecord: Codable, Equatable, Sendable {
    public let permissionType: PermissionType
    public let status: PermissionStatus
    public let source: String
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case permissionType = "permission_type"
        case status
        case source
        case updatedAt = "updated_at"
    }

    public init(
        permissionType: PermissionType,
        status: PermissionStatus = .notDetermined,
        source: String = "system",
        updatedAt: Date = Date()
    ) {
        self.permissionType = permissionType
        self.status = status
        self.source = source
        self.updatedAt = updatedAt
    }
}
