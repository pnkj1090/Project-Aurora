import Foundation

// MARK: - Enums

public enum ReminderStatus: String, Codable, CaseIterable, Identifiable, Sendable {
    case scheduled
    case delivered
    case cancelled
    case failed

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .scheduled: return "Scheduled"
        case .delivered: return "Delivered"
        case .cancelled: return "Cancelled"
        case .failed: return "Failed"
        }
    }

    public var systemIcon: String {
        switch self {
        case .scheduled: return "bell"
        case .delivered: return "bell.badge.fill"
        case .cancelled: return "bell.slash"
        case .failed: return "exclamationmark.triangle"
        }
    }
}

// MARK: - Reminder Domain Model

public struct ReminderItem: Identifiable, Codable, Equatable, Sendable {
    public let id: String
    public let userId: String
    public var title: String
    public var body: String
    public var scheduledAt: Date
    public var status: ReminderStatus
    public let createdAt: Date
    public var updatedAt: Date
    public var taskId: String?
    public var commitmentId: String?
    public var notificationId: String?
    public var isActive: Bool
    public var notificationSync: NotificationSyncSignal?

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case title
        case body
        case scheduledAt = "scheduled_at"
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case taskId = "task_id"
        case commitmentId = "commitment_id"
        case notificationId = "notification_id"
        case isActive = "is_active"
        case notificationSync = "notification_sync"
    }

    public init(
        id: String = UUID().uuidString,
        userId: String = "local_user",
        title: String,
        body: String = "",
        scheduledAt: Date,
        status: ReminderStatus = .scheduled,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        taskId: String? = nil,
        commitmentId: String? = nil,
        notificationId: String? = nil,
        isActive: Bool = true,
        notificationSync: NotificationSyncSignal? = nil
    ) {
        self.id = id
        self.userId = userId
        self.title = title
        self.body = body
        self.scheduledAt = scheduledAt
        self.status = status
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.taskId = taskId
        self.commitmentId = commitmentId
        self.notificationId = notificationId
        self.isActive = isActive
        self.notificationSync = notificationSync
    }
}

// MARK: - API Payloads

public struct ReminderListResponse: Codable, Sendable {
    public let items: [ReminderItem]
    public let total: Int
    public let limit: Int
    public let offset: Int
}

public struct ReminderCreateRequest: Codable, Sendable {
    public let title: String
    public let scheduledAt: Date
    public let body: String?
    public let taskId: String?
    public let commitmentId: String?

    enum CodingKeys: String, CodingKey {
        case title
        case scheduledAt = "scheduled_at"
        case body
        case taskId = "task_id"
        case commitmentId = "commitment_id"
    }

    public init(
        title: String,
        scheduledAt: Date,
        body: String? = nil,
        taskId: String? = nil,
        commitmentId: String? = nil
    ) {
        self.title = title
        self.scheduledAt = scheduledAt
        self.body = body
        self.taskId = taskId
        self.commitmentId = commitmentId
    }
}

public struct ReminderUpdateRequest: Codable, Sendable {
    public let title: String?
    public let body: String?
    public let scheduledAt: Date?

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case scheduledAt = "scheduled_at"
    }

    public init(
        title: String? = nil,
        body: String? = nil,
        scheduledAt: Date? = nil
    ) {
        self.title = title
        self.body = body
        self.scheduledAt = scheduledAt
    }
}

public struct ReminderDeleteResponse: Codable, Sendable {
    public let id: String
    public let status: String
    public let deleted: Bool
}
