import Foundation

// MARK: - Enums

public enum CommitmentStatus: String, Codable, CaseIterable, Identifiable {
    case active
    case completed
    case cancelled

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .active: return "Active"
        case .completed: return "Completed"
        case .cancelled: return "Cancelled"
        }
    }

    public var systemIcon: String {
        switch self {
        case .active: return "hourglass"
        case .completed: return "checkmark.seal.fill"
        case .cancelled: return "xmark.seal"
        }
    }
}

public enum CommitmentPriority: String, Codable, CaseIterable, Identifiable {
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

// MARK: - Commitment Domain Model

public struct CommitmentItem: Identifiable, Codable, Equatable {
    public let id: String
    public let userId: String
    public var title: String
    public var description: String
    public var status: CommitmentStatus
    public var priority: CommitmentPriority
    public let createdAt: Date
    public var updatedAt: Date
    public var targetDate: Date?
    public var completedAt: Date?
    public var tags: [String]
    public var isActive: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case title
        case description
        case status
        case priority
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case targetDate = "target_date"
        case completedAt = "completed_at"
        case tags
        case isActive = "is_active"
    }

    public init(
        id: String = UUID().uuidString,
        userId: String = "local_user",
        title: String,
        description: String = "",
        status: CommitmentStatus = .active,
        priority: CommitmentPriority = .medium,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        targetDate: Date? = nil,
        completedAt: Date? = nil,
        tags: [String] = [],
        isActive: Bool = true
    ) {
        self.id = id
        self.userId = userId
        self.title = title
        self.description = description
        self.status = status
        self.priority = priority
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.targetDate = targetDate
        self.completedAt = completedAt
        self.tags = tags
        self.isActive = isActive
    }
}

// MARK: - API Payloads

public struct CommitmentListResponse: Codable {
    public let items: [CommitmentItem]
    public let total: Int
    public let limit: Int
    public let offset: Int
}

public struct CommitmentCreateRequest: Codable {
    public let title: String
    public let description: String?
    public let priority: CommitmentPriority
    public let targetDate: Date?
    public let tags: [String]

    enum CodingKeys: String, CodingKey {
        case title
        case description
        case priority
        case targetDate = "target_date"
        case tags
    }

    public init(
        title: String,
        description: String? = nil,
        priority: CommitmentPriority = .medium,
        targetDate: Date? = nil,
        tags: [String] = []
    ) {
        self.title = title
        self.description = description
        self.priority = priority
        self.targetDate = targetDate
        self.tags = tags
    }
}

public struct CommitmentUpdateRequest: Codable {
    public let title: String?
    public let description: String?
    public let priority: CommitmentPriority?
    public let targetDate: Date?
    public let tags: [String]?

    enum CodingKeys: String, CodingKey {
        case title
        case description
        case priority
        case targetDate = "target_date"
        case tags
    }

    public init(
        title: String? = nil,
        description: String? = nil,
        priority: CommitmentPriority? = nil,
        targetDate: Date? = nil,
        tags: [String]? = nil
    ) {
        self.title = title
        self.description = description
        self.priority = priority
        self.targetDate = targetDate
        self.tags = tags
    }
}

public struct CommitmentDeleteResponse: Codable {
    public let id: String
    public let status: String
    public let deleted: Bool
}
