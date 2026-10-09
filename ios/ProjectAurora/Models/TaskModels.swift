import Foundation

// MARK: - Enums

public enum TaskStatus: String, Codable, CaseIterable, Identifiable {
    case pending
    case inProgress = "in_progress"
    case completed
    case cancelled

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .pending: return "Pending"
        case .inProgress: return "In Progress"
        case .completed: return "Completed"
        case .cancelled: return "Cancelled"
        }
    }

    public var systemIcon: String {
        switch self {
        case .pending: return "circle"
        case .inProgress: return "circle.dashed"
        case .completed: return "checkmark.circle.fill"
        case .cancelled: return "xmark.circle"
        }
    }
}

public enum TaskPriority: String, Codable, CaseIterable, Identifiable {
    case low
    case medium
    case high

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .low: return "Low"
        case .medium: return "Medium"
        case .high: return "High"
        }
    }

    public var systemIcon: String {
        switch self {
        case .low: return "arrow.down"
        case .medium: return "minus"
        case .high: return "exclamationmark"
        }
    }
}

// MARK: - Task Domain Model

public struct TaskItem: Identifiable, Codable, Equatable {
    public let id: String
    public let userId: String
    public var title: String
    public var description: String
    public var status: TaskStatus
    public var priority: TaskPriority
    public let createdAt: Date
    public var updatedAt: Date
    public var dueAt: Date?
    public var completedAt: Date?
    public var tags: [String]
    public var isActive: Bool
    public var sourceCommitmentId: String?

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case title
        case description
        case status
        case priority
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case dueAt = "due_at"
        case completedAt = "completed_at"
        case tags
        case isActive = "is_active"
        case sourceCommitmentId = "source_commitment_id"
    }

    public init(
        id: String = UUID().uuidString,
        userId: String = "local_user",
        title: String,
        description: String = "",
        status: TaskStatus = .pending,
        priority: TaskPriority = .medium,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        dueAt: Date? = nil,
        completedAt: Date? = nil,
        tags: [String] = [],
        isActive: Bool = true,
        sourceCommitmentId: String? = nil
    ) {
        self.id = id
        self.userId = userId
        self.title = title
        self.description = description
        self.status = status
        self.priority = priority
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.dueAt = dueAt
        self.completedAt = completedAt
        self.tags = tags
        self.isActive = isActive
        self.sourceCommitmentId = sourceCommitmentId
    }
}

// MARK: - API Payloads

public struct TaskListResponse: Codable {
    public let items: [TaskItem]
    public let total: Int
    public let limit: Int
    public let offset: Int
}

public struct TaskCreateRequest: Codable {
    public let title: String
    public let description: String?
    public let priority: TaskPriority
    public let dueAt: Date?
    public let tags: [String]
    public let sourceCommitmentId: String?

    enum CodingKeys: String, CodingKey {
        case title
        case description
        case priority
        case dueAt = "due_at"
        case tags
        case sourceCommitmentId = "source_commitment_id"
    }

    public init(
        title: String,
        description: String? = nil,
        priority: TaskPriority = .medium,
        dueAt: Date? = nil,
        tags: [String] = [],
        sourceCommitmentId: String? = nil
    ) {
        self.title = title
        self.description = description
        self.priority = priority
        self.dueAt = dueAt
        self.tags = tags
        self.sourceCommitmentId = sourceCommitmentId
    }
}

public struct TaskUpdateRequest: Codable {
    public let title: String?
    public let description: String?
    public let priority: TaskPriority?
    public let dueAt: Date?
    public let tags: [String]?
    public let sourceCommitmentId: String?
    public let clearCommitment: Bool

    enum CodingKeys: String, CodingKey {
        case title
        case description
        case priority
        case dueAt = "due_at"
        case tags
        case sourceCommitmentId = "source_commitment_id"
        case clearCommitment = "clear_commitment"
    }

    public init(
        title: String? = nil,
        description: String? = nil,
        priority: TaskPriority? = nil,
        dueAt: Date? = nil,
        tags: [String]? = nil,
        sourceCommitmentId: String? = nil,
        clearCommitment: Bool = false
    ) {
        self.title = title
        self.description = description
        self.priority = priority
        self.dueAt = dueAt
        self.tags = tags
        self.sourceCommitmentId = sourceCommitmentId
        self.clearCommitment = clearCommitment
    }
}

public struct TaskDeleteResponse: Codable {
    public let id: String
    public let status: String
    public let deleted: Bool
}
