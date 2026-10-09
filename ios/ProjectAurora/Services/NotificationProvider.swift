import Foundation
import UserNotifications

// MARK: - Notification Provider Protocol

public protocol NotificationProviderProtocol: Sendable {
    func checkAuthorizationStatus() async -> PermissionStatus
    func requestAuthorization() async throws -> PermissionStatus
    func schedule(id: String, title: String, body: String, scheduledAt: Date) async throws
    func cancel(ids: [String]) async
    func removeDelivered(ids: [String]) async
}

// MARK: - iOS UNUserNotificationCenter Implementation

public final class UNNotificationProvider: NotificationProviderProtocol, @unchecked Sendable {
    private let center: UNUserNotificationCenter

    public init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return .authorized
        case .denied:
            return .denied
        case .notDetermined:
            return .notDetermined
        @unknown default:
            return .unavailable
        }
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        let granted = try await center.requestAuthorization(options: [.alert, .sound, .badge])
        return granted ? .authorized : .denied
    }

    public func schedule(id: String, title: String, body: String, scheduledAt: Date) async throws {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default

        let calendar = Calendar.current
        let components = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: scheduledAt
        )

        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
        let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)

        try await center.add(request)
    }

    public func cancel(ids: [String]) async {
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    public func removeDelivered(ids: [String]) async {
        center.removeDeliveredNotifications(withIdentifiers: ids)
    }
}

// MARK: - Mock Notification Provider (for Previews & Offline Tests)

public final class MockNotificationProvider: NotificationProviderProtocol, @unchecked Sendable {
    public var currentStatus: PermissionStatus
    public var scheduledNotifications: [String: (title: String, body: String, date: Date)] = [:]

    public init(initialStatus: PermissionStatus = .notDetermined) {
        self.currentStatus = initialStatus
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        return currentStatus
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        currentStatus = .authorized
        return .authorized
    }

    public func schedule(id: String, title: String, body: String, scheduledAt: Date) async throws {
        guard currentStatus == .authorized else {
            throw NSError(
                domain: "NotificationProvider",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Notifications are not authorized"]
            )
        }
        scheduledNotifications[id] = (title, body, scheduledAt)
    }

    public func cancel(ids: [String]) async {
        for id in ids {
            scheduledNotifications.removeValue(forKey: id)
        }
    }

    public func removeDelivered(ids: [String]) async {
        for id in ids {
            scheduledNotifications.removeValue(forKey: id)
        }
    }
}
