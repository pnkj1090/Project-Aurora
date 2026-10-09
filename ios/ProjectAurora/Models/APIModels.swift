import Foundation

// MARK: - Health Check Models

public struct HealthResponse: Codable, Equatable {
    public let status: String
    public let service: String
    public let version: String
    public let environment: String
    public let activeProvider: String
    public let activeModel: String
    public let timestamp: String

    enum CodingKeys: String, CodingKey {
        case status
        case service
        case version
        case environment
        case activeProvider = "active_provider"
        case activeModel = "active_model"
        case timestamp
    }
}

// MARK: - Phase 5K.4 Citation Models

/// Server-authoritative citation record. The iOS client renders this metadata only.
/// It never validates, re-fetches, or modifies citation data.
/// All fields are populated exclusively by the backend CitationRegistry.
public struct CitationItem: Codable, Equatable, Identifiable {
    public let id: String
    public let url: String
    public let title: String?
    public let domain: String?
    public let publishedDate: String?

    enum CodingKeys: String, CodingKey {
        case id
        case url
        case title
        case domain
        case publishedDate = "published_date"
    }
}

// MARK: - Chat & Conversation Models

public enum MessageRole: String, Codable {
    case system
    case user
    case assistant
    case tool
}

public struct ChatMessageItem: Codable, Equatable, Identifiable {
    public var id = UUID()
    public let role: MessageRole
    public let content: String
    public var timestamp: Date = Date()
    public var stepActivities: [String]? = nil
    /// Server-authoritative citation sources for this message.
    /// Nil if the response had no validated web citations.
    public var citations: [CitationItem]? = nil

    enum CodingKeys: String, CodingKey {
        case role
        case content
        case stepActivities = "step_activities"
    }

    public init(id: UUID = UUID(), role: MessageRole, content: String, timestamp: Date = Date(), stepActivities: [String]? = nil, citations: [CitationItem]? = nil) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.stepActivities = stepActivities
        self.citations = citations
    }
}

public struct ChatRequestPayload: Codable {
    public let message: String
    public let conversationId: String?

    enum CodingKeys: String, CodingKey {
        case message
        case conversationId = "conversation_id"
    }

    public init(message: String, conversationId: String? = nil) {
        self.message = message
        self.conversationId = conversationId
    }
}

public struct MessageContentPayload: Codable, Equatable {
    public let role: String
    public let content: String
}

// MARK: - Tool Confirmation Models

public struct ToolConfirmationDetails: Codable, Equatable {
    public let name: String
    public let arguments: [String: AnyCodableValue]
    public let riskLevel: String
    public let description: String

    enum CodingKeys: String, CodingKey {
        case name
        case arguments
        case riskLevel = "risk_level"
        case description
    }
}

public struct ConfirmActionPayload: Codable {
    public let conversationId: String
    public let confirmationId: String
    public let executionId: String
    public let approved: Bool

    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
        case confirmationId = "confirmation_id"
        case executionId = "execution_id"
        case approved
    }

    public init(conversationId: String, confirmationId: String, executionId: String, approved: Bool) {
        self.conversationId = conversationId
        self.confirmationId = confirmationId
        self.executionId = executionId
        self.approved = approved
    }
}

public struct ProvisionRequest: Codable {
    public let provisioningSecret: String
    enum CodingKeys: String, CodingKey { case provisioningSecret = "provisioning_secret" }
    public init(provisioningSecret: String) { self.provisioningSecret = provisioningSecret }
}

public struct ProvisionResponse: Codable {
    public let credential: String
    public let user: ProvisionUser
    public let device: ProvisionDevice
}

public struct ProvisionUser: Codable { public let userId: String; enum CodingKeys: String, CodingKey { case userId = "user_id" } }
public struct ProvisionDevice: Codable { public let deviceId: String; enum CodingKeys: String, CodingKey { case deviceId = "device_id" } }

public struct ChatResponsePayload: Codable, Equatable {
    public let type: String  // "message" or "confirmation_required"
    public let conversationId: String
    public let message: MessageContentPayload?
    public let confirmationId: String?
    public let tool: ToolConfirmationDetails?
    public let provider: String
    public let model: String
    public let executionId: String?
    public let stepsCount: Int?
    public let stepActivities: [String]?
    /// Server-authoritative citations for the assistant response.
    /// Nil if the response had no validated web citations.
    public let citations: [CitationItem]?
    /// Phase 6B: Server-authoritative client notification actions.
    public let clientActions: [ClientActionPayload]?

    enum CodingKeys: String, CodingKey {
        case type
        case conversationId = "conversation_id"
        case message
        case confirmationId = "confirmation_id"
        case tool
        case provider
        case model
        case executionId = "execution_id"
        case stepsCount = "steps_count"
        case stepActivities = "step_activities"
        case citations
        case clientActions = "client_actions"
    }
}

// MARK: - Phase 6B Notification Sync Models

public enum NotificationSyncSignal: String, Codable, Sendable, Equatable {
    case NONE
    case SCHEDULE
    case RESCHEDULE
    case CANCEL
}

public struct ClientActionPayload: Codable, Equatable, Sendable {
    public let actionId: String
    public let action: String
    public let updatedAt: String
    public let entityType: String?
    public let entityId: String?
    public let userId: String?
    public let createdAt: String?

    // Phase 6B Reminder fields
    public let reminderId: String?
    public let notificationId: String?
    public let title: String?
    public let body: String?
    public let scheduledAt: String?

    // Phase 6C Task / Commitment fields
    public let taskId: String?
    public let commitmentId: String?
    public let description: String?
    public let status: String?
    public let priority: String?
    public let dueAt: String?
    public let targetDate: String?
    public let completedAt: String?
    public let tags: [String]?
    public let isActive: Bool?
    public let sourceCommitmentId: String?

    enum CodingKeys: String, CodingKey {
        case actionId = "action_id"
        case action
        case updatedAt = "updated_at"
        case entityType = "entity_type"
        case entityId = "entity_id"
        case userId = "user_id"
        case createdAt = "created_at"

        case reminderId = "reminder_id"
        case notificationId = "notification_id"
        case title
        case body
        case scheduledAt = "scheduled_at"

        case taskId = "task_id"
        case commitmentId = "commitment_id"
        case description
        case status
        case priority
        case dueAt = "due_at"
        case targetDate = "target_date"
        case completedAt = "completed_at"
        case tags
        case isActive = "is_active"
        case sourceCommitmentId = "source_commitment_id"
    }

    public init(
        actionId: String,
        action: String,
        updatedAt: String,
        entityType: String? = nil,
        entityId: String? = nil,
        userId: String? = nil,
        createdAt: String? = nil,
        reminderId: String? = nil,
        notificationId: String? = nil,
        title: String? = nil,
        body: String? = nil,
        scheduledAt: String? = nil,
        taskId: String? = nil,
        commitmentId: String? = nil,
        description: String? = nil,
        status: String? = nil,
        priority: String? = nil,
        dueAt: String? = nil,
        targetDate: String? = nil,
        completedAt: String? = nil,
        tags: [String]? = nil,
        isActive: Bool? = nil,
        sourceCommitmentId: String? = nil
    ) {
        self.actionId = actionId
        self.action = action
        self.updatedAt = updatedAt
        self.entityType = entityType
        self.entityId = entityId
        self.userId = userId
        self.createdAt = createdAt
        self.reminderId = reminderId
        self.notificationId = notificationId
        self.title = title
        self.body = body
        self.scheduledAt = scheduledAt
        self.taskId = taskId
        self.commitmentId = commitmentId
        self.description = description
        self.status = status
        self.priority = priority
        self.dueAt = dueAt
        self.targetDate = targetDate
        self.completedAt = completedAt
        self.tags = tags
        self.isActive = isActive
        self.sourceCommitmentId = sourceCommitmentId
    }

    /// Phase 6B backward-compatible convenience initializer
    public init(
        actionId: String,
        action: String,
        reminderId: String,
        notificationId: String,
        title: String,
        body: String,
        scheduledAt: String,
        updatedAt: String
    ) {
        self.actionId = actionId
        self.action = action
        self.updatedAt = updatedAt
        self.entityType = nil
        self.entityId = nil
        self.userId = nil
        self.createdAt = nil
        self.reminderId = reminderId
        self.notificationId = notificationId
        self.title = title
        self.body = body
        self.scheduledAt = scheduledAt
        self.taskId = nil
        self.commitmentId = nil
        self.description = nil
        self.status = nil
        self.priority = nil
        self.dueAt = nil
        self.targetDate = nil
        self.completedAt = nil
        self.tags = nil
        self.isActive = nil
        self.sourceCommitmentId = nil
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.actionId = try container.decode(String.self, forKey: .actionId)
        self.action = try container.decode(String.self, forKey: .action)
        self.updatedAt = try container.decode(String.self, forKey: .updatedAt)
        self.entityType = try container.decodeIfPresent(String.self, forKey: .entityType)
        self.entityId = try container.decodeIfPresent(String.self, forKey: .entityId)
        self.userId = try container.decodeIfPresent(String.self, forKey: .userId)
        self.createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)

        self.reminderId = try container.decodeIfPresent(String.self, forKey: .reminderId)
        self.notificationId = try container.decodeIfPresent(String.self, forKey: .notificationId)
        self.title = try container.decodeIfPresent(String.self, forKey: .title)
        self.body = try container.decodeIfPresent(String.self, forKey: .body)
        self.scheduledAt = try container.decodeIfPresent(String.self, forKey: .scheduledAt)

        self.taskId = try container.decodeIfPresent(String.self, forKey: .taskId)
        self.commitmentId = try container.decodeIfPresent(String.self, forKey: .commitmentId)
        self.description = try container.decodeIfPresent(String.self, forKey: .description)
        self.status = try container.decodeIfPresent(String.self, forKey: .status)
        self.priority = try container.decodeIfPresent(String.self, forKey: .priority)
        self.dueAt = try container.decodeIfPresent(String.self, forKey: .dueAt)
        self.targetDate = try container.decodeIfPresent(String.self, forKey: .targetDate)
        self.completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
        self.tags = try container.decodeIfPresent([String].self, forKey: .tags)
        self.isActive = try container.decodeIfPresent(Bool.self, forKey: .isActive)
        self.sourceCommitmentId = try container.decodeIfPresent(String.self, forKey: .sourceCommitmentId)
    }
}

public typealias ReminderResponse = ReminderItem


// MARK: - Flexible AnyCodable Helper

public enum AnyCodableValue: Codable, Equatable, Sendable, CustomStringConvertible {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    public var description: String {
        return displayString
    }

    public var displayString: String {
        switch self {
        case .string(let v): return v
        case .int(let v): return String(v)
        case .double(let v): return String(v)
        case .bool(let v): return String(v)
        }
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let val = try? container.decode(String.self) {
            self = .string(val)
        } else if let val = try? container.decode(Int.self) {
            self = .int(val)
        } else if let val = try? container.decode(Double.self) {
            self = .double(val)
        } else if let val = try? container.decode(Bool.self) {
            self = .bool(val)
        } else {
            self = .string("unknown")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let v): try container.encode(v)
        case .int(let v): try container.encode(v)
        case .double(let v): try container.encode(v)
        case .bool(let v): try container.encode(v)
        }
    }
}

// MARK: - Phase 5L.2 Memory Candidate Review Models

public struct ConflictTargetItem: Codable, Equatable {
    public let memoryId: String
    public let existingContent: String
    public let type: String

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case existingContent = "existing_content"
        case type
    }

    public init(memoryId: String, existingContent: String, type: String) {
        self.memoryId = memoryId
        self.existingContent = existingContent
        self.type = type
    }
}

public struct CandidateReviewItem: Codable, Equatable, Identifiable {
    public let id: String
    public let memoryType: String
    public let content: String
    public let confidence: Double
    public let importance: Int
    public let tags: [String]
    public let evidenceType: String
    public let reasoning: String?
    public let sensitivity: String
    public let createdAt: String
    public let expiresAt: String?
    public let hasConflict: Bool
    public let conflictTarget: ConflictTargetItem?

    enum CodingKeys: String, CodingKey {
        case id
        case memoryType = "memory_type"
        case content
        case confidence
        case importance
        case tags
        case evidenceType = "evidence_type"
        case reasoning
        case sensitivity
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case hasConflict = "has_conflict"
        case conflictTarget = "conflict_target"
    }

    public init(
        id: String,
        memoryType: String,
        content: String,
        confidence: Double,
        importance: Int,
        tags: [String],
        evidenceType: String,
        reasoning: String? = nil,
        sensitivity: String,
        createdAt: String,
        expiresAt: String? = nil,
        hasConflict: Bool = false,
        conflictTarget: ConflictTargetItem? = nil
    ) {
        self.id = id
        self.memoryType = memoryType
        self.content = content
        self.confidence = confidence
        self.importance = importance
        self.tags = tags
        self.evidenceType = evidenceType
        self.reasoning = reasoning
        self.sensitivity = sensitivity
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.hasConflict = hasConflict
        self.conflictTarget = conflictTarget
    }
}

public struct CandidateReviewListResponse: Codable, Equatable {
    public let items: [CandidateReviewItem]
    public let total: Int
    public let limit: Int
    public let offset: Int
}

public struct CandidateApprovePayload: Codable {
    public let editedContent: String?
    public let overrideTags: [String]?
    public let conflictAction: String?

    enum CodingKeys: String, CodingKey {
        case editedContent = "edited_content"
        case overrideTags = "override_tags"
        case conflictAction = "conflict_action"
    }

    public init(
        editedContent: String? = nil,
        overrideTags: [String]? = nil,
        conflictAction: String? = nil
    ) {
        self.editedContent = editedContent
        self.overrideTags = overrideTags
        self.conflictAction = conflictAction
    }
}

public struct CandidateApproveResponse: Codable, Equatable {
    public let status: String
    public let memory: MemoryItem
    public let candidateId: String
    public let conflictActionTaken: String?

    enum CodingKeys: String, CodingKey {
        case status
        case memory
        case candidateId = "candidate_id"
        case conflictActionTaken = "conflict_action_taken"
    }
}

public struct CandidateRejectResponse: Codable, Equatable {
    public let status: String
    public let candidateId: String

    enum CodingKeys: String, CodingKey {
        case status
        case candidateId = "candidate_id"
    }
}
