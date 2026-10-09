import Foundation

// MARK: - Strategy Category

public enum StrategyCategory: String, Codable, CaseIterable, Sendable {
    case resolution = "resolution"
    case mitigation = "mitigation"
    case information = "information"
}

// MARK: - Proposal Status

public enum ProposalStatus: String, Codable, CaseIterable, Sendable {
    case draft = "draft"
    case pendingConfirmation = "pending_confirmation"
    case approved = "approved"
    case executing = "executing"
    case completed = "completed"
    case partiallyExecuted = "partially_executed"
    case failed = "failed"
    case aborted = "aborted"
    case expired = "expired"
}

// MARK: - Proposal Failure Reason

public enum ProposalFailureReason: String, Codable, CaseIterable, Sendable {
    case permissionRevoked = "PERMISSION_REVOKED"
    case noCalendarSnapshot = "NO_CALENDAR_SNAPSHOT"
    case staleCalendarSnapshot = "STALE_CALENDAR_SNAPSHOT"
    case staleCalendarState = "STALE_CALENDAR_STATE"
    case staleStateDetected = "STALE_STATE_DETECTED"
    case operationPreconditionFailed = "OPERATION_PRECONDITION_FAILED"
    case operationFailed = "OPERATION_FAILED"
    case executionTimeout = "EXECUTION_TIMEOUT"
}

// MARK: - Coordination Operation Type

public enum CoordinationOperationType: String, Codable, CaseIterable, Sendable {
    case updateTaskDueDate = "update_task_due_date"
    case createReminder = "create_reminder"

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let rawString = try container.decode(String.self)
        if let match = CoordinationOperationType(rawValue: rawString.lowercased()) {
            self = match
        } else {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: decoder.codingPath,
                    debugDescription: "Unknown operation type: '\(rawString)'"
                )
            )
        }
    }
}

// MARK: - Strongly Typed Field Changes Payloads

public struct TaskDueDateFieldChangesPayload: Codable, Equatable, Sendable {
    public let dueAt: String
    public let effectiveDurationMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case dueAt = "due_at"
        case effectiveDurationMinutes = "effective_duration_minutes"
    }

    public init(dueAt: String, effectiveDurationMinutes: Int? = nil) {
        self.dueAt = dueAt
        self.effectiveDurationMinutes = effectiveDurationMinutes
    }
}

public struct ReminderCreationFieldChangesPayload: Codable, Equatable, Sendable {
    public let scheduledAt: String
    public let linkedTaskId: String

    enum CodingKeys: String, CodingKey {
        case scheduledAt = "scheduled_at"
        case linkedTaskId = "linked_task_id"
    }

    public init(scheduledAt: String, linkedTaskId: String) {
        self.scheduledAt = scheduledAt
        self.linkedTaskId = linkedTaskId
    }
}

public enum CoordinationFieldChanges: Codable, Equatable, Sendable {
    case updateTaskDueDate(TaskDueDateFieldChangesPayload)
    case createReminder(ReminderCreationFieldChangesPayload)

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .updateTaskDueDate(let payload):
            try payload.encode(to: encoder)
        case .createReminder(let payload):
            try payload.encode(to: encoder)
        }
    }
}

// MARK: - Operation Execution Status

public enum OperationExecutionStatus: String, Codable, CaseIterable, Sendable {
    case pending = "pending"
    case executing = "executing"
    case completed = "completed"
    case failed = "failed"
    case aborted = "aborted"
}

// MARK: - Display Operation Preview

public struct DisplayOperationPreviewPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(operationType.rawValue)_\(targetEntityId)" }

    public let operationType: CoordinationOperationType
    public let targetEntityId: String
    public let displaySummary: String
    public let fieldChanges: CoordinationFieldChanges

    enum CodingKeys: String, CodingKey {
        case operationType = "operation_type"
        case targetEntityId = "target_entity_id"
        case displaySummary = "display_summary"
        case fieldChanges = "field_changes"
    }

    public init(
        operationType: CoordinationOperationType,
        targetEntityId: String,
        displaySummary: String,
        fieldChanges: CoordinationFieldChanges
    ) {
        self.operationType = operationType
        self.targetEntityId = targetEntityId
        self.displaySummary = displaySummary
        self.fieldChanges = fieldChanges
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let opType = try container.decode(CoordinationOperationType.self, forKey: .operationType)
        self.operationType = opType
        self.targetEntityId = try container.decode(String.self, forKey: .targetEntityId)
        self.displaySummary = try container.decode(String.self, forKey: .displaySummary)

        // Strict discriminator-based decoding: missing or malformed required fields fail closed
        switch opType {
        case .updateTaskDueDate:
            let payload = try container.decode(TaskDueDateFieldChangesPayload.self, forKey: .fieldChanges)
            self.fieldChanges = .updateTaskDueDate(payload)
        case .createReminder:
            let payload = try container.decode(ReminderCreationFieldChangesPayload.self, forKey: .fieldChanges)
            self.fieldChanges = .createReminder(payload)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(operationType, forKey: .operationType)
        try container.encode(targetEntityId, forKey: .targetEntityId)
        try container.encode(displaySummary, forKey: .displaySummary)
        switch fieldChanges {
        case .updateTaskDueDate(let payload):
            try container.encode(payload, forKey: .fieldChanges)
        case .createReminder(let payload):
            try container.encode(payload, forKey: .fieldChanges)
        }
    }
}

// MARK: - Recovery Execution Payloads

public struct CoordinationOperationExecutionPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { operationId }
    public let operationId: String
    public let operationType: String
    public let status: String
    public let failureError: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case operationType = "operation_type"
        case status
        case failureError = "failure_error"
    }

    public init(
        operationId: String,
        operationType: String,
        status: String,
        failureError: String? = nil
    ) {
        self.operationId = operationId
        self.operationType = operationType
        self.status = status
        self.failureError = failureError
    }
}

public struct CoordinationProposalExecutionOutputPayload: Codable, Equatable, Sendable {
    public let status: String
    public let proposalId: String?
    public let proposalStatus: String?
    public let failureReason: String?
    public let operations: [CoordinationOperationExecutionPayload]?
    public let executionId: String?

    enum CodingKeys: String, CodingKey {
        case status
        case proposalId = "proposal_id"
        case proposalStatus = "proposal_status"
        case failureReason = "failure_reason"
        case operations
        case executionId = "execution_id"
    }

    public init(
        status: String,
        proposalId: String? = nil,
        proposalStatus: String? = nil,
        failureReason: String? = nil,
        operations: [CoordinationOperationExecutionPayload]? = nil,
        executionId: String? = nil
    ) {
        self.status = status
        self.proposalId = proposalId
        self.proposalStatus = proposalStatus
        self.failureReason = failureReason
        self.operations = operations
        self.executionId = executionId
    }
}


// MARK: - Strategy Option

public struct StrategyOptionPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { strategyId }

    public let strategyId: String
    public let analysisId: String
    public let conflictId: String
    public let category: StrategyCategory
    public let name: String
    public let description: String
    public let effectiveDurationMinutes: Int
    public let isAvailable: Bool
    public let missingPermissions: [String]
    public let feasibilityProof: String?
    public let previewOperations: [DisplayOperationPreviewPayload]
    public let tradeoffs: String?

    enum CodingKeys: String, CodingKey {
        case strategyId = "strategy_id"
        case analysisId = "analysis_id"
        case conflictId = "conflict_id"
        case category
        case name
        case description
        case effectiveDurationMinutes = "effective_duration_minutes"
        case isAvailable = "is_available"
        case missingPermissions = "missing_permissions"
        case feasibilityProof = "feasibility_proof"
        case previewOperations = "preview_operations"
        case tradeoffs
    }

    public init(
        strategyId: String,
        analysisId: String,
        conflictId: String,
        category: StrategyCategory,
        name: String,
        description: String,
        effectiveDurationMinutes: Int,
        isAvailable: Bool = true,
        missingPermissions: [String] = [],
        feasibilityProof: String? = nil,
        previewOperations: [DisplayOperationPreviewPayload] = [],
        tradeoffs: String? = nil
    ) {
        self.strategyId = strategyId
        self.analysisId = analysisId
        self.conflictId = conflictId
        self.category = category
        self.name = name
        self.description = description
        self.effectiveDurationMinutes = effectiveDurationMinutes
        self.isAvailable = isAvailable
        self.missingPermissions = missingPermissions
        self.feasibilityProof = feasibilityProof
        self.previewOperations = previewOperations
        self.tradeoffs = tradeoffs
    }
}

// MARK: - Analysis Result

public struct AnalysisResultPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { analysisId }

    public let analysisId: String
    public let conversationId: String?
    public let executionId: String?
    public let userId: String
    public let targetDate: String
    public let timezone: String
    public let conflicts: [AgendaConflictPayload]
    public let strategyOptions: [StrategyOptionPayload]
    public let createdAt: String
    public let expiresAt: String
    public let isSuperseded: Bool

    enum CodingKeys: String, CodingKey {
        case analysisId = "analysis_id"
        case conversationId = "conversation_id"
        case executionId = "execution_id"
        case userId = "user_id"
        case targetDate = "target_date"
        case timezone
        case conflicts
        case strategyOptions = "strategy_options"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case isSuperseded = "is_superseded"
    }
}

// MARK: - Allocated Task Slot

public struct AllocatedTaskSlotPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(taskId)_\(allocatedStartAt)" }

    public let taskId: String
    public let taskTitle: String
    public let allocatedStartAt: String
    public let allocatedEndAt: String
    public let durationMinutes: Int
    public let dueAt: String?
    public let status: String

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case taskTitle = "task_title"
        case allocatedStartAt = "allocated_start_at"
        case allocatedEndAt = "allocated_end_at"
        case durationMinutes = "duration_minutes"
        case dueAt = "due_at"
        case status
    }
}

// MARK: - Unallocated Task Info

public struct UnallocatedTaskInfo: Codable, Equatable, Sendable, Identifiable {
    public var id: String { taskId }

    public let taskId: String
    public let taskTitle: String
    public let reason: String

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case taskTitle = "task_title"
        case reason
    }
}

// MARK: - Coordinate Schedule Response

public struct CoordinateScheduleResponsePayload: Codable, Equatable, Sendable {
    public let targetDate: String
    public let timezone: String
    public let allocations: [AllocatedTaskSlotPayload]
    public let unallocatedTasks: [UnallocatedTaskInfo]
    public let conflicts: [AgendaConflictPayload]
    public let notes: [String]

    enum CodingKeys: String, CodingKey {
        case targetDate = "target_date"
        case timezone
        case allocations
        case unallocatedTasks = "unallocated_tasks"
        case conflicts
        case notes
    }
}

// MARK: - Resolve Conflicts Response

public struct ResolveConflictsResponsePayload: Codable, Equatable, Sendable {
    public let analysisId: String
    public let targetDate: String
    public let timezone: String
    public let conflicts: [AgendaConflictPayload]
    public let strategyOptions: [StrategyOptionPayload]
    public let createdAt: String
    public let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case analysisId = "analysis_id"
        case targetDate = "target_date"
        case timezone
        case conflicts
        case strategyOptions = "strategy_options"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
    }
}
