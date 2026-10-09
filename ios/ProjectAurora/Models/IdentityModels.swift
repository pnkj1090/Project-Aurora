import Foundation

public struct UserIdentity: Codable, Equatable, Sendable {
    public let userId: String
    public let displayName: String?
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case displayName = "display_name"
        case createdAt = "created_at"
    }

    public init(
        userId: String = "usr_local_default",
        displayName: String? = "Aurora User",
        createdAt: Date = Date()
    ) {
        self.userId = userId
        self.displayName = displayName
        self.createdAt = createdAt
    }
}

public struct DeviceIdentity: Codable, Equatable, Sendable {
    public let deviceId: String
    public let userId: String
    public let platform: String
    public let createdAt: Date
    public let lastActiveAt: Date?

    enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case userId = "user_id"
        case platform
        case createdAt = "created_at"
        case lastActiveAt = "last_active_at"
    }

    public init(
        deviceId: String,
        userId: String = "usr_local_default",
        platform: String = "ios",
        createdAt: Date = Date(),
        lastActiveAt: Date? = nil
    ) {
        self.deviceId = deviceId
        self.userId = userId
        self.platform = platform
        self.createdAt = createdAt
        self.lastActiveAt = lastActiveAt
    }
}

/// Helper managing the persistent local installation identifier for this iOS app.
/// Uses an application-generated UUID stored in standard UserDefaults.
/// Does NOT access hardware serial numbers, IMEI, or private identifiers.
public final class InstallationIdentityManager: @unchecked Sendable {
    public static let shared = InstallationIdentityManager()
    private let installationKey = "aurora.installation.device_id"

    public var deviceId: String {
        if let existing = UserDefaults.standard.string(forKey: installationKey), !existing.isEmpty {
            return existing
        }
        let generated = UUID().uuidString
        UserDefaults.standard.set(generated, forKey: installationKey)
        return generated
    }

    public func getDeviceIdentity(userId: String = "usr_local_default") -> DeviceIdentity {
        return DeviceIdentity(
            deviceId: deviceId,
            userId: userId,
            platform: "ios",
            lastActiveAt: Date()
        )
    }
}
