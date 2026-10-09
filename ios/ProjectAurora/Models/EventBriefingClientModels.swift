import Foundation

// MARK: - Lifecycle Enums

public enum DebriefProposalStatus: String, Codable, CaseIterable, Sendable {
    case draft = "draft"
    case approved = "approved"
    case executing = "executing"
    case completed = "completed"
    case failed = "failed"
    case partiallyExecuted = "partially_executed"
    case aborted = "aborted"
    case expired = "expired"
}

public enum DebriefOperationType: String, Codable, CaseIterable, Sendable {
    case createTask = "create_task"
    case createReminder = "create_reminder"
    case createMemory = "create_memory"
}

public enum DebriefOperationStatus: String, Codable, CaseIterable, Sendable {
    case pending = "pending"
    case completed = "completed"
    case failed = "failed"
    case skipped = "skipped"
}

public enum DebriefFailureReason: String, Codable, CaseIterable, Sendable {
    case expired = "EXPIRED"
    case insufficientPermissions = "INSUFFICIENT_PERMISSIONS"
    case stateChanged = "STATE_CHANGED"
    case preconditionFailed = "PRECONDITION_FAILED"
    case operationFailed = "OPERATION_FAILED"
    case calendarNotFound = "CALENDAR_NOT_FOUND"
}

public enum ReminderLinkType: String, Codable, CaseIterable, Sendable {
    case none = "none"
    case task = "task"
    case commitment = "commitment"
}

// MARK: - Strongly Typed Closed Operation Payloads

public struct DebriefTaskPayload: Codable, Equatable, Sendable {
    public let exactTitle: String
    public let exactDueAt: String?
    public let exactDurationMinutes: Int?
    public let exactPriority: String
    public let exactCommitmentId: String?
    public let title: String?
    public let dueAt: String?
    public let durationMinutes: Int?
    public let priority: String?
    public let commitmentId: String?

    enum CodingKeys: String, CodingKey {
        case exactTitle = "exact_title"
        case exactDueAt = "exact_due_at"
        case exactDurationMinutes = "exact_duration_minutes"
        case exactPriority = "exact_priority"
        case exactCommitmentId = "exact_commitment_id"
        case title
        case dueAt = "due_at"
        case durationMinutes = "duration_minutes"
        case priority
        case commitmentId = "commitment_id"
    }

    public init(
        exactTitle: String,
        exactDueAt: String? = nil,
        exactDurationMinutes: Int? = 30,
        exactPriority: String = "medium",
        exactCommitmentId: String? = nil,
        title: String? = nil,
        dueAt: String? = nil,
        durationMinutes: Int? = nil,
        priority: String? = nil,
        commitmentId: String? = nil
    ) {
        self.exactTitle = exactTitle
        self.exactDueAt = exactDueAt
        self.exactDurationMinutes = exactDurationMinutes
        self.exactPriority = exactPriority
        self.exactCommitmentId = exactCommitmentId
        self.title = title ?? exactTitle
        self.dueAt = dueAt ?? exactDueAt
        self.durationMinutes = durationMinutes ?? exactDurationMinutes
        self.priority = priority ?? exactPriority
        self.commitmentId = commitmentId ?? exactCommitmentId
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.exactTitle = try container.decode(String.self, forKey: .exactTitle)
        self.exactDueAt = try container.decodeIfPresent(String.self, forKey: .exactDueAt)
        self.exactDurationMinutes = try container.decodeIfPresent(Int.self, forKey: .exactDurationMinutes)
        self.exactPriority = try container.decode(String.self, forKey: .exactPriority)
        self.exactCommitmentId = try container.decodeIfPresent(String.self, forKey: .exactCommitmentId)
        self.title = try container.decodeIfPresent(String.self, forKey: .title)
        self.dueAt = try container.decodeIfPresent(String.self, forKey: .dueAt)
        self.durationMinutes = try container.decodeIfPresent(Int.self, forKey: .durationMinutes)
        self.priority = try container.decodeIfPresent(String.self, forKey: .priority)
        self.commitmentId = try container.decodeIfPresent(String.self, forKey: .commitmentId)
    }
}

public struct DebriefReminderPayload: Codable, Equatable, Sendable {
    public let exactTitle: String
    public let exactScheduledAt: String
    public let linkedEntityType: ReminderLinkType
    public let linkedEntityId: String?
    public let title: String?
    public let scheduledAt: String?

    enum CodingKeys: String, CodingKey {
        case exactTitle = "exact_title"
        case exactScheduledAt = "exact_scheduled_at"
        case linkedEntityType = "linked_entity_type"
        case linkedEntityId = "linked_entity_id"
        case title
        case scheduledAt = "scheduled_at"
    }

    public init(
        exactTitle: String,
        exactScheduledAt: String,
        linkedEntityType: ReminderLinkType = .none,
        linkedEntityId: String? = nil,
        title: String? = nil,
        scheduledAt: String? = nil
    ) {
        self.exactTitle = exactTitle
        self.exactScheduledAt = exactScheduledAt
        self.linkedEntityType = linkedEntityType
        self.linkedEntityId = linkedEntityId
        self.title = title ?? exactTitle
        self.scheduledAt = scheduledAt ?? exactScheduledAt
    }
}

public struct DebriefMemoryPayload: Codable, Equatable, Sendable {
    public let exactContent: String
    public let exactMemoryType: String
    public let exactConfidence: Double
    public let sourceEventId: String
    public let contextTags: [String]
    public let content: String?
    public let memoryType: String?
    public let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case exactContent = "exact_content"
        case exactMemoryType = "exact_memory_type"
        case exactConfidence = "exact_confidence"
        case sourceEventId = "source_event_id"
        case contextTags = "context_tags"
        case content
        case memoryType = "memory_type"
        case confidence
    }

    public init(
        exactContent: String,
        exactMemoryType: String = "episodic",
        exactConfidence: Double = 1.0,
        sourceEventId: String,
        contextTags: [String] = ["meeting", "debrief"],
        content: String? = nil,
        memoryType: String? = nil,
        confidence: Double? = nil
    ) {
        self.exactContent = exactContent
        self.exactMemoryType = exactMemoryType
        self.exactConfidence = exactConfidence
        self.sourceEventId = sourceEventId
        self.contextTags = contextTags
        self.content = content ?? exactContent
        self.memoryType = memoryType ?? exactMemoryType
        self.confidence = confidence ?? exactConfidence
    }
}

// MARK: - Closed Payload Enum

public enum DebriefOperationPresentationPayload: Codable, Equatable, Sendable {
    case createTask(DebriefTaskPayload)
    case createReminder(DebriefReminderPayload)
    case createMemory(DebriefMemoryPayload)
}

// MARK: - Discrete Debrief Operation

public struct DebriefOperation: Codable, Equatable, Sendable, Identifiable {
    public let operationId: String
    public let operationType: DebriefOperationType
    public let proposedPayload: DebriefOperationPresentationPayload
    public let status: DebriefOperationStatus
    public let failureError: String?
    public let createdEntityId: String?

    public var id: String { operationId }

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case operationType = "operation_type"
        case proposedPayload = "proposed_payload"
        case status
        case failureError = "failure_error"
        case createdEntityId = "created_entity_id"
    }

    public init(
        operationId: String,
        operationType: DebriefOperationType,
        proposedPayload: DebriefOperationPresentationPayload,
        status: DebriefOperationStatus = .pending,
        failureError: String? = nil,
        createdEntityId: String? = nil
    ) {
        self.operationId = operationId
        self.operationType = operationType
        self.proposedPayload = proposedPayload
        self.status = status
        self.failureError = failureError
        self.createdEntityId = createdEntityId
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.operationId = try container.decode(String.self, forKey: .operationId)
        self.operationType = try container.decode(DebriefOperationType.self, forKey: .operationType)
        self.status = try container.decode(DebriefOperationStatus.self, forKey: .status)
        self.failureError = try container.decodeIfPresent(String.self, forKey: .failureError)
        self.createdEntityId = try container.decodeIfPresent(String.self, forKey: .createdEntityId)

        switch self.operationType {
        case .createTask:
            let payload = try container.decode(DebriefTaskPayload.self, forKey: .proposedPayload)
            self.proposedPayload = .createTask(payload)
        case .createReminder:
            let payload = try container.decode(DebriefReminderPayload.self, forKey: .proposedPayload)
            self.proposedPayload = .createReminder(payload)
        case .createMemory:
            let payload = try container.decode(DebriefMemoryPayload.self, forKey: .proposedPayload)
            self.proposedPayload = .createMemory(payload)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(operationId, forKey: .operationId)
        try container.encode(operationType, forKey: .operationType)
        try container.encode(status, forKey: .status)
        try container.encodeIfPresent(failureError, forKey: .failureError)
        try container.encodeIfPresent(createdEntityId, forKey: .createdEntityId)

        switch proposedPayload {
        case .createTask(let payload):
            try container.encode(payload, forKey: .proposedPayload)
        case .createReminder(let payload):
            try container.encode(payload, forKey: .proposedPayload)
        case .createMemory(let payload):
            try container.encode(payload, forKey: .proposedPayload)
        }
    }
}

// MARK: - Debrief Proposal

public struct DebriefProposal: Codable, Equatable, Sendable, Identifiable {
    public let proposalId: String
    public let eventId: String
    public let userId: String
    public let createdAt: String
    public let expiresAt: String
    public let status: DebriefProposalStatus
    public let stateHash: String
    public let orderedOperations: [DebriefOperation]
    public let failureReason: DebriefFailureReason?
    public let appliedAt: String?

    public var id: String { proposalId }

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case eventId = "event_id"
        case userId = "user_id"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case status
        case stateHash = "state_hash"
        case orderedOperations = "ordered_operations"
        case failureReason = "failure_reason"
        case appliedAt = "applied_at"
    }

    public init(
        proposalId: String,
        eventId: String,
        userId: String,
        createdAt: String,
        expiresAt: String,
        status: DebriefProposalStatus = .draft,
        stateHash: String,
        orderedOperations: [DebriefOperation] = [],
        failureReason: DebriefFailureReason? = nil,
        appliedAt: String? = nil
    ) {
        self.proposalId = proposalId
        self.eventId = eventId
        self.userId = userId
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.status = status
        self.stateHash = stateHash
        self.orderedOperations = orderedOperations
        self.failureReason = failureReason
        self.appliedAt = appliedAt
    }
}

// MARK: - Event Briefing & Summaries

public struct BriefingAttendee: Codable, Equatable, Sendable, Identifiable {
    public let email: String?
    public let name: String?
    public let status: String?

    public var id: String { email ?? name ?? UUID().uuidString }

    public init(email: String? = nil, name: String? = nil, status: String? = nil) {
        self.email = email
        self.name = name
        self.status = status
    }
}

public struct TaskItemSummary: Codable, Equatable, Sendable, Identifiable {
    public let taskId: String
    public let title: String
    public let status: String
    public let dueAt: String?
    public let priority: String?

    public var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case title
        case status
        case dueAt = "due_at"
        case priority
    }

    public init(taskId: String, title: String, status: String, dueAt: String? = nil, priority: String? = nil) {
        self.taskId = taskId
        self.title = title
        self.status = status
        self.dueAt = dueAt
        self.priority = priority
    }
}

public struct CommitmentItemSummary: Codable, Equatable, Sendable, Identifiable {
    public let commitmentId: String
    public let title: String
    public let status: String
    public let targetDate: String?

    public var id: String { commitmentId }

    enum CodingKeys: String, CodingKey {
        case commitmentId = "commitment_id"
        case title
        case status
        case targetDate = "target_date"
    }

    public init(commitmentId: String, title: String, status: String, targetDate: String? = nil) {
        self.commitmentId = commitmentId
        self.title = title
        self.status = status
        self.targetDate = targetDate
    }
}

public struct MemoryItemSummary: Codable, Equatable, Sendable, Identifiable {
    public let memoryId: String
    public let content: String
    public let type: String

    public var id: String { memoryId }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case content
        case type
    }

    public init(memoryId: String, content: String, type: String) {
        self.memoryId = memoryId
        self.content = content
        self.type = type
    }
}

public struct EventBriefing: Codable, Equatable, Sendable, Identifiable {
    public let eventId: String
    public let userId: String
    public let title: String
    public let startTime: String
    public let endTime: String
    public let location: String?
    public let attendees: [BriefingAttendee]
    public let relatedTasks: [TaskItemSummary]
    public let activeCommitments: [CommitmentItemSummary]
    public let relevantMemories: [MemoryItemSummary]
    public let summary: String

    public var id: String { eventId }

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case userId = "user_id"
        case title
        case startTime = "start_time"
        case endTime = "end_time"
        case location
        case attendees
        case relatedTasks = "related_tasks"
        case activeCommitments = "active_commitments"
        case relevantMemories = "relevant_memories"
        case summary
    }

    public init(
        eventId: String,
        userId: String,
        title: String,
        startTime: String,
        endTime: String,
        location: String? = nil,
        attendees: [BriefingAttendee] = [],
        relatedTasks: [TaskItemSummary] = [],
        activeCommitments: [CommitmentItemSummary] = [],
        relevantMemories: [MemoryItemSummary] = [],
        summary: String = ""
    ) {
        self.eventId = eventId
        self.userId = userId
        self.title = title
        self.startTime = startTime
        self.endTime = endTime
        self.location = location
        self.attendees = attendees
        self.relatedTasks = relatedTasks
        self.activeCommitments = activeCommitments
        self.relevantMemories = relevantMemories
        self.summary = summary
    }
}

// MARK: - Candidate DTOs (Request Construction)

public struct DebriefTaskCandidate: Codable, Equatable, Sendable {
    public let exactTitle: String
    public let exactDueAt: String?
    public let exactDurationMinutes: Int?
    public let exactPriority: String?
    public let exactCommitmentId: String?

    enum CodingKeys: String, CodingKey {
        case exactTitle = "exact_title"
        case exactDueAt = "exact_due_at"
        case exactDurationMinutes = "exact_duration_minutes"
        case exactPriority = "exact_priority"
        case exactCommitmentId = "exact_commitment_id"
    }

    public init(
        exactTitle: String,
        exactDueAt: String? = nil,
        exactDurationMinutes: Int? = 30,
        exactPriority: String? = "medium",
        exactCommitmentId: String? = nil
    ) {
        self.exactTitle = exactTitle
        self.exactDueAt = exactDueAt
        self.exactDurationMinutes = exactDurationMinutes
        self.exactPriority = exactPriority
        self.exactCommitmentId = exactCommitmentId
    }
}

public struct DebriefReminderCandidate: Codable, Equatable, Sendable {
    public let exactTitle: String
    public let exactScheduledAt: String
    public let linkedEntityType: ReminderLinkType
    public let linkedEntityId: String?

    enum CodingKeys: String, CodingKey {
        case exactTitle = "exact_title"
        case exactScheduledAt = "exact_scheduled_at"
        case linkedEntityType = "linked_entity_type"
        case linkedEntityId = "linked_entity_id"
    }

    public init(
        exactTitle: String,
        exactScheduledAt: String,
        linkedEntityType: ReminderLinkType = .none,
        linkedEntityId: String? = nil
    ) {
        self.exactTitle = exactTitle
        self.exactScheduledAt = exactScheduledAt
        self.linkedEntityType = linkedEntityType
        self.linkedEntityId = linkedEntityId
    }
}

public struct DebriefMemoryCandidate: Codable, Equatable, Sendable {
    public let exactContent: String
    public let exactMemoryType: String
    public let exactConfidence: Double
    public let contextTags: [String]

    enum CodingKeys: String, CodingKey {
        case exactContent = "exact_content"
        case exactMemoryType = "exact_memory_type"
        case exactConfidence = "exact_confidence"
        case contextTags = "context_tags"
    }

    public init(
        exactContent: String,
        exactMemoryType: String = "episodic",
        exactConfidence: Double = 1.0,
        contextTags: [String] = ["meeting", "debrief"]
    ) {
        self.exactContent = exactContent
        self.exactMemoryType = exactMemoryType
        self.exactConfidence = exactConfidence
        self.contextTags = contextTags
    }
}

// MARK: - Client Request Payloads

public struct ProposeDebriefRequestPayload: Codable, Equatable, Sendable {
    public let rawNotes: String
    public let taskCandidates: [DebriefTaskCandidate]?
    public let reminderCandidates: [DebriefReminderCandidate]?
    public let memoryCandidates: [DebriefMemoryCandidate]?

    enum CodingKeys: String, CodingKey {
        case rawNotes = "raw_notes"
        case taskCandidates = "task_candidates"
        case reminderCandidates = "reminder_candidates"
        case memoryCandidates = "memory_candidates"
    }

    public init(
        rawNotes: String = "",
        taskCandidates: [DebriefTaskCandidate]? = nil,
        reminderCandidates: [DebriefReminderCandidate]? = nil,
        memoryCandidates: [DebriefMemoryCandidate]? = nil
    ) {
        self.rawNotes = rawNotes
        self.taskCandidates = taskCandidates
        self.reminderCandidates = reminderCandidates
        self.memoryCandidates = memoryCandidates
    }
}

public struct ApplyDebriefRequestPayload: Codable, Equatable, Sendable {
    public let approved: Bool

    enum CodingKeys: String, CodingKey {
        case approved
    }

    public init(approved: Bool = true) {
        self.approved = approved
    }
}
