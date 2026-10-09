import Foundation

// MARK: - Enums

public enum MemoryType: String, Codable, CaseIterable, Identifiable {
    case explicit
    case preference
    case fact
    case commitment
    case task
    case episodic

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .explicit: return "Explicit"
        case .preference: return "Preference"
        case .fact: return "Fact"
        case .commitment: return "Commitment"
        case .task: return "Task"
        case .episodic: return "Episodic"
        }
    }

    public var systemIcon: String {
        switch self {
        case .explicit: return "pin.fill"
        case .preference: return "heart.fill"
        case .fact: return "lightbulb.fill"
        case .commitment: return "hand.raised.fill"
        case .task: return "checklist"
        case .episodic: return "clock.arrow.circlepath"
        }
    }
}

public enum MemorySource: String, Codable {
    case userExplicit = "user_explicit"
    case inferred
    case system

    public var displayName: String {
        switch self {
        case .userExplicit: return "User Explicit"
        case .inferred: return "Inferred"
        case .system: return "System"
        }
    }
}

public enum MemoryStatus: String, Codable {
    case active
    case pendingReview = "pending_review"
    case archived

    public var displayName: String {
        switch self {
        case .active: return "Active"
        case .pendingReview: return "Pending Review"
        case .archived: return "Archived"
        }
    }
}

// MARK: - Core Item Model

public struct MemoryItem: Codable, Identifiable, Equatable {
    public let id: String
    public var type: MemoryType
    public var content: String
    public var importance: Int
    public let confidence: Double
    public let source: MemorySource
    public var status: MemoryStatus
    public let createdAt: Date
    public var updatedAt: Date
    public var expiresAt: Date?
    public var tags: [String]
    public var isActive: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case type
        case content
        case importance
        case confidence
        case source
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case expiresAt = "expires_at"
        case tags
        case isActive = "is_active"
    }

    public init(
        id: String,
        type: MemoryType,
        content: String,
        importance: Int = 3,
        confidence: Double = 1.0,
        source: MemorySource = .userExplicit,
        status: MemoryStatus = .active,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        expiresAt: Date? = nil,
        tags: [String] = [],
        isActive: Bool = true
    ) {
        self.id = id
        self.type = type
        self.content = content
        self.importance = importance
        self.confidence = confidence
        self.source = source
        self.status = status
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.expiresAt = expiresAt
        self.tags = tags
        self.isActive = isActive
    }

    public var formattedCreatedDate: String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: createdAt)
    }

    public var relativeCreatedDate: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: createdAt, relativeTo: Date())
    }

    // MARK: - Presentation & Lifecycle Display

    public var isExpired: Bool {
        guard let exp = expiresAt else { return false }
        return Date() > exp
    }

    public var lifecycleDisplayState: String {
        if !isActive || status == .archived {
            return "Archived"
        }
        if isExpired {
            return "Expired"
        }
        return "Active"
    }
}

// MARK: - Payload & Response Models

public struct MemoryListResponse: Codable, Equatable {
    public let items: [MemoryItem]
    public let total: Int
    public let limit: Int
    public let offset: Int
}

public struct MemoryCreatePayload: Codable {
    public let content: String
    public let type: MemoryType
    public let importance: Int
    public let tags: [String]
    public let expiresAt: Date?

    enum CodingKeys: String, CodingKey {
        case content
        case type
        case importance
        case tags
        case expiresAt = "expires_at"
    }

    public init(
        content: String,
        type: MemoryType = .explicit,
        importance: Int = 3,
        tags: [String] = [],
        expiresAt: Date? = nil
    ) {
        self.content = content
        self.type = type
        self.importance = importance
        self.tags = tags
        self.expiresAt = expiresAt
    }
}

public struct MemoryUpdatePayload: Codable {
    public let content: String?
    public let importance: Int?
    public let tags: [String]?
    public let expiresAt: Date?

    enum CodingKeys: String, CodingKey {
        case content
        case importance
        case tags
        case expiresAt = "expires_at"
    }

    public init(
        content: String? = nil,
        importance: Int? = nil,
        tags: [String]? = nil,
        expiresAt: Date? = nil
    ) {
        self.content = content
        self.importance = importance
        self.tags = tags
        self.expiresAt = expiresAt
    }
}

public struct MemoryDeleteResponse: Codable, Equatable {
    public let id: String
    public let status: String
    public let deleted: Bool
}
