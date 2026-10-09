import Foundation

// MARK: - Strict Enums

public enum RecoveryProposalStatus: String, Codable, Sendable, Equatable {
    case draft = "DRAFT"
    case pendingConfirmation = "PENDING_CONFIRMATION"
    case applied = "APPLIED"
    case aborted = "ABORTED"
    case cancelled = "CANCELLED"
    case expired = "EXPIRED"
}

public enum RecoveryExecutionStatus: String, Codable, Sendable, Equatable {
    case executed = "EXECUTED"
    case partiallyExecuted = "PARTIALLY_EXECUTED"
    case failed = "FAILED"
    case aborted = "ABORTED"
}

public enum RecoveryOperationType: String, Codable, Sendable, Equatable {
    case rescheduleTask = "RESCHEDULE_TASK"
    case rescheduleReminder = "RESCHEDULE_REMINDER"
}

// MARK: - Strict Typed Operation Payloads

public struct RescheduleTaskPayload: Codable, Sendable, Equatable {
    public let taskId: String
    public let newDueAt: Date
    public let expectedVersion: Int

    public init(taskId: String, newDueAt: Date, expectedVersion: Int) {
        self.taskId = taskId
        self.newDueAt = newDueAt
        self.expectedVersion = expectedVersion
    }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case newDueAt = "new_due_at"
        case expectedVersion = "expected_version"
    }
}

public struct RescheduleReminderPayload: Codable, Sendable, Equatable {
    public let reminderId: String
    public let newScheduledAt: Date

    public init(reminderId: String, newScheduledAt: Date) {
        self.reminderId = reminderId
        self.newScheduledAt = newScheduledAt
    }

    enum CodingKeys: String, CodingKey {
        case reminderId = "reminder_id"
        case newScheduledAt = "new_scheduled_at"
    }
}

public enum RecoveryOperationPayload: Codable, Sendable, Equatable {
    case task(RescheduleTaskPayload)
    case reminder(RescheduleReminderPayload)

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let taskPayload = try? container.decode(RescheduleTaskPayload.self) {
            self = .task(taskPayload)
        } else if let reminderPayload = try? container.decode(RescheduleReminderPayload.self) {
            self = .reminder(reminderPayload)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Data matches neither RescheduleTaskPayload nor RescheduleReminderPayload"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .task(let payload):
            try container.encode(payload)
        case .reminder(let payload):
            try container.encode(payload)
        }
    }
}

// MARK: - Preconditions & External Dependencies

public struct RecoveryTaskPrecondition: Codable, Sendable, Equatable {
    public let taskId: String
    public let expectedVersion: Int
    public let expectedStatus: String
    public let expectedDueAt: Date?
    public let sourceCommitmentId: String?
    public let expectedStateHash: String

    public init(
        taskId: String,
        expectedVersion: Int,
        expectedStatus: String,
        expectedDueAt: Date? = nil,
        sourceCommitmentId: String? = nil,
        expectedStateHash: String
    ) {
        self.taskId = taskId
        self.expectedVersion = expectedVersion
        self.expectedStatus = expectedStatus
        self.expectedDueAt = expectedDueAt
        self.sourceCommitmentId = sourceCommitmentId
        self.expectedStateHash = expectedStateHash
    }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case expectedVersion = "expected_version"
        case expectedStatus = "expected_status"
        case expectedDueAt = "expected_due_at"
        case sourceCommitmentId = "source_commitment_id"
        case expectedStateHash = "expected_state_hash"
    }
}

public struct RecoveryReminderPrecondition: Codable, Sendable, Equatable {
    public let reminderId: String
    public let expectedScheduledAt: Date
    public let expectedStatus: String
    public let expectedStateHash: String

    public init(reminderId: String, expectedScheduledAt: Date, expectedStatus: String, expectedStateHash: String) {
        self.reminderId = reminderId
        self.expectedScheduledAt = expectedScheduledAt
        self.expectedStatus = expectedStatus
        self.expectedStateHash = expectedStateHash
    }

    enum CodingKeys: String, CodingKey {
        case reminderId = "reminder_id"
        case expectedScheduledAt = "expected_scheduled_at"
        case expectedStatus = "expected_status"
        case expectedStateHash = "expected_state_hash"
    }
}

public struct CommitmentExternalDependency: Codable, Sendable, Equatable {
    public let commitmentId: String
    public let type: String
    public let expectedTargetDate: Date
    public let linkedTaskId: String

    public init(commitmentId: String, type: String = "DATE_TARGET", expectedTargetDate: Date, linkedTaskId: String) {
        self.commitmentId = commitmentId
        self.type = type
        self.expectedTargetDate = expectedTargetDate
        self.linkedTaskId = linkedTaskId
    }

    enum CodingKeys: String, CodingKey {
        case commitmentId = "commitment_id"
        case type
        case expectedTargetDate = "expected_target_date"
        case linkedTaskId = "linked_task_id"
    }
}

public struct WorkingHoursExternalDependency: Codable, Sendable, Equatable {
    public let effectiveTimezone: String
    public let effectiveDates: [String]
    public let workingHoursFingerprint: String

    public init(effectiveTimezone: String, effectiveDates: [String] = [], workingHoursFingerprint: String) {
        self.effectiveTimezone = effectiveTimezone
        self.effectiveDates = effectiveDates
        self.workingHoursFingerprint = workingHoursFingerprint
    }

    enum CodingKeys: String, CodingKey {
        case effectiveTimezone = "effective_timezone"
        case effectiveDates = "effective_dates"
        case workingHoursFingerprint = "working_hours_fingerprint"
    }
}

public struct CalendarExclusivityPrecondition: Codable, Sendable, Equatable {
    public let windowStart: Date
    public let windowEnd: Date
    public let calendarEventFingerprint: String

    public init(windowStart: Date, windowEnd: Date, calendarEventFingerprint: String) {
        self.windowStart = windowStart
        self.windowEnd = windowEnd
        self.calendarEventFingerprint = calendarEventFingerprint
    }

    enum CodingKeys: String, CodingKey {
        case windowStart = "window_start"
        case windowEnd = "window_end"
        case calendarEventFingerprint = "calendar_event_fingerprint"
    }
}

// MARK: - Authoritative Operation Model

public struct ScheduleRecoveryOperation: Codable, Sendable, Equatable {
    public let operationId: String
    public let operationType: RecoveryOperationType
    public let targetEntityId: String
    public let newScheduledTime: Date
    public let payload: RecoveryOperationPayload
    public let taskPrecondition: RecoveryTaskPrecondition?
    public let reminderPrecondition: RecoveryReminderPrecondition?

    public init(
        operationId: String,
        operationType: RecoveryOperationType,
        targetEntityId: String,
        newScheduledTime: Date,
        payload: RecoveryOperationPayload,
        taskPrecondition: RecoveryTaskPrecondition? = nil,
        reminderPrecondition: RecoveryReminderPrecondition? = nil
    ) {
        self.operationId = operationId
        self.operationType = operationType
        self.targetEntityId = targetEntityId
        self.newScheduledTime = newScheduledTime
        self.payload = payload
        self.taskPrecondition = taskPrecondition
        self.reminderPrecondition = reminderPrecondition
    }

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case operationType = "operation_type"
        case targetEntityId = "target_entity_id"
        case newScheduledTime = "new_scheduled_time"
        case payload
        case taskPrecondition = "task_precondition"
        case reminderPrecondition = "reminder_precondition"
    }
}

// MARK: - Ephemeral Note Model

public struct EphemeralRecoveryNote: Codable, Sendable, Equatable {
    public let noteId: String
    public let title: String
    public let details: String
    public let affectedEntities: [String]
    public let generatedAt: Date

    public init(noteId: String, title: String, details: String, affectedEntities: [String] = [], generatedAt: Date) {
        self.noteId = noteId
        self.title = title
        self.details = details
        self.affectedEntities = affectedEntities
        self.generatedAt = generatedAt
    }

    enum CodingKeys: String, CodingKey {
        case noteId = "note_id"
        case title
        case details
        case affectedEntities = "affected_entities"
        case generatedAt = "generated_at"
    }
}

// MARK: - Authoritative Proposal Model

public struct ScheduleRecoveryProposal: Codable, Sendable, Equatable, Identifiable {
    public var id: String { proposalId }
    public let proposalId: String
    public let userId: String
    public let createdAt: Date
    public let expiresAt: Date
    public let status: RecoveryProposalStatus
    public let operations: [ScheduleRecoveryOperation]
    public let taskPreconditions: [RecoveryTaskPrecondition]
    public let reminderPreconditions: [RecoveryReminderPrecondition]
    public let commitmentDependencies: [CommitmentExternalDependency]
    public let workingHoursDependency: WorkingHoursExternalDependency
    public let calendarPrecondition: CalendarExclusivityPrecondition
    public let preconditionsHash: String
    public let ephemeralNotes: [EphemeralRecoveryNote]
    public let failureReason: String?
    public let appliedAt: Date?

    public init(
        proposalId: String,
        userId: String,
        createdAt: Date,
        expiresAt: Date,
        status: RecoveryProposalStatus = .draft,
        operations: [ScheduleRecoveryOperation] = [],
        taskPreconditions: [RecoveryTaskPrecondition] = [],
        reminderPreconditions: [RecoveryReminderPrecondition] = [],
        commitmentDependencies: [CommitmentExternalDependency] = [],
        workingHoursDependency: WorkingHoursExternalDependency,
        calendarPrecondition: CalendarExclusivityPrecondition,
        preconditionsHash: String,
        ephemeralNotes: [EphemeralRecoveryNote] = [],
        failureReason: String? = nil,
        appliedAt: Date? = nil
    ) {
        self.proposalId = proposalId
        self.userId = userId
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.status = status
        self.operations = operations
        self.taskPreconditions = taskPreconditions
        self.reminderPreconditions = reminderPreconditions
        self.commitmentDependencies = commitmentDependencies
        self.workingHoursDependency = workingHoursDependency
        self.calendarPrecondition = calendarPrecondition
        self.preconditionsHash = preconditionsHash
        self.ephemeralNotes = ephemeralNotes
        self.failureReason = failureReason
        self.appliedAt = appliedAt
    }

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case userId = "user_id"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case status
        case operations
        case taskPreconditions = "task_preconditions"
        case reminderPreconditions = "reminder_preconditions"
        case commitmentDependencies = "commitment_dependencies"
        case workingHoursDependency = "working_hours_dependency"
        case calendarPrecondition = "calendar_precondition"
        case preconditionsHash = "preconditions_hash"
        case ephemeralNotes = "ephemeral_notes"
        case failureReason = "failure_reason"
        case appliedAt = "applied_at"
    }
}

// MARK: - Diagnostics & Execution Results

public struct RecoveryExecutionDiagnostics: Codable, Sendable, Equatable {
    public let preflightCheckedAt: Date
    public let preflightDurationMs: Double
    public let executionDurationMs: Double
    public let totalDurationMs: Double
    public let presentationNotes: [EphemeralRecoveryNote]

    public init(
        preflightCheckedAt: Date,
        preflightDurationMs: Double,
        executionDurationMs: Double,
        totalDurationMs: Double,
        presentationNotes: [EphemeralRecoveryNote] = []
    ) {
        self.preflightCheckedAt = preflightCheckedAt
        self.preflightDurationMs = preflightDurationMs
        self.executionDurationMs = executionDurationMs
        self.totalDurationMs = totalDurationMs
        self.presentationNotes = presentationNotes
    }

    enum CodingKeys: String, CodingKey {
        case preflightCheckedAt = "preflight_checked_at"
        case preflightDurationMs = "preflight_duration_ms"
        case executionDurationMs = "execution_duration_ms"
        case totalDurationMs = "total_duration_ms"
        case presentationNotes = "presentation_notes"
    }
}

public struct RecoveryExecutionResult: Codable, Sendable, Equatable {
    public let executionId: String
    public let proposalId: String
    public let userId: String
    public let status: RecoveryExecutionStatus
    public let startedAt: Date
    public let completedAt: Date
    public let completedOperations: [String]
    public let failedOperationId: String?
    public let failureReason: String?
    public let executionTrace: [String]
    public let diagnostics: RecoveryExecutionDiagnostics

    public init(
        executionId: String,
        proposalId: String,
        userId: String,
        status: RecoveryExecutionStatus,
        startedAt: Date,
        completedAt: Date,
        completedOperations: [String] = [],
        failedOperationId: String? = nil,
        failureReason: String? = nil,
        executionTrace: [String] = [],
        diagnostics: RecoveryExecutionDiagnostics
    ) {
        self.executionId = executionId
        self.proposalId = proposalId
        self.userId = userId
        self.status = status
        self.startedAt = startedAt
        self.completedAt = completedAt
        self.completedOperations = completedOperations
        self.failedOperationId = failedOperationId
        self.failureReason = failureReason
        self.executionTrace = executionTrace
        self.diagnostics = diagnostics
    }

    enum CodingKeys: String, CodingKey {
        case executionId = "execution_id"
        case proposalId = "proposal_id"
        case userId = "user_id"
        case status
        case startedAt = "started_at"
        case completedAt = "completed_at"
        case completedOperations = "completed_operations"
        case failedOperationId = "failed_operation_id"
        case failureReason = "failure_reason"
        case executionTrace = "execution_trace"
        case diagnostics
    }
}

// MARK: - Evaluation Models

public struct PreconditionMismatch: Codable, Sendable, Equatable {
    public let category: String
    public let entityId: String?
    public let field: String?
    public let expectedValue: String?
    public let actualValue: String?
    public let reason: String
    public let details: String

    public init(
        category: String,
        entityId: String? = nil,
        field: String? = nil,
        expectedValue: String? = nil,
        actualValue: String? = nil,
        reason: String,
        details: String
    ) {
        self.category = category
        self.entityId = entityId
        self.field = field
        self.expectedValue = expectedValue
        self.actualValue = actualValue
        self.reason = reason
        self.details = details
    }

    enum CodingKeys: String, CodingKey {
        case category
        case entityId = "entity_id"
        case field
        case expectedValue = "expected_value"
        case actualValue = "actual_value"
        case reason
        case details
    }
}

public struct RecoveryEvaluationResult: Codable, Sendable, Equatable {
    public let valid: Bool
    public let checkedAt: Date
    public let mismatches: [PreconditionMismatch]

    public init(valid: Bool, checkedAt: Date, mismatches: [PreconditionMismatch] = []) {
        self.valid = valid
        self.checkedAt = checkedAt
        self.mismatches = mismatches
    }

    enum CodingKeys: String, CodingKey {
        case valid
        case checkedAt = "checked_at"
        case mismatches
    }
}

// MARK: - Request Payloads

public struct ProposeScheduleRecoveryPayload: Codable, Sendable, Equatable {
    public let taskIds: [String]
    public let reminderIds: [String]?
    public let targetWindowStart: Date
    public let targetWindowEnd: Date
    public let userIntent: String?
    public let clientPreferredTimezone: String?

    public init(
        taskIds: [String],
        reminderIds: [String]? = nil,
        targetWindowStart: Date,
        targetWindowEnd: Date,
        userIntent: String? = nil,
        clientPreferredTimezone: String? = nil
    ) {
        self.taskIds = taskIds
        self.reminderIds = reminderIds
        self.targetWindowStart = targetWindowStart
        self.targetWindowEnd = targetWindowEnd
        self.userIntent = userIntent
        self.clientPreferredTimezone = clientPreferredTimezone
    }

    enum CodingKeys: String, CodingKey {
        case taskIds = "task_ids"
        case reminderIds = "reminder_ids"
        case targetWindowStart = "target_window_start"
        case targetWindowEnd = "target_window_end"
        case userIntent = "user_intent"
        case clientPreferredTimezone = "client_preferred_timezone"
    }
}

public struct CancelRecoveryProposalPayload: Codable, Sendable, Equatable {
    public let reason: String?

    public init(reason: String? = nil) {
        self.reason = reason
    }
}

public struct ApplyRecoveryProposalPayload: Codable, Sendable, Equatable {
    public let confirmed: Bool
    public let clientContext: [String: String]?

    public init(confirmed: Bool = true, clientContext: [String: String]? = nil) {
        self.confirmed = confirmed
        self.clientContext = clientContext
    }

    enum CodingKeys: String, CodingKey {
        case confirmed
        case clientContext = "client_context"
    }
}

public struct EvaluateRecoveryProposalPayload: Codable, Sendable, Equatable {
    public let proposalId: String

    public init(proposalId: String) {
        self.proposalId = proposalId
    }

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
    }
}

// MARK: - Strongly Typed Transport & Error Containers

public struct FastAPIErrorContainer<T: Codable & Sendable>: Codable, Sendable {
    public let detail: T

    public init(detail: T) {
        self.detail = detail
    }
}

public struct ScheduleRecoveryErrorDetail: Codable, Sendable, Equatable {
    public let error: String
    public let message: String
    public let failedOperationId: String?
    public let completedOperations: [String]

    enum CodingKeys: String, CodingKey {
        case error
        case message
        case failedOperationId = "failed_operation_id"
        case completedOperations = "completed_operations"
    }

    public init(
        error: String,
        message: String,
        failedOperationId: String? = nil,
        completedOperations: [String] = []
    ) {
        self.error = error
        self.message = message
        self.failedOperationId = failedOperationId
        self.completedOperations = completedOperations
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.error = try container.decode(String.self, forKey: .error)
        self.message = try container.decode(String.self, forKey: .message)
        self.failedOperationId = try container.decodeIfPresent(String.self, forKey: .failedOperationId)
        self.completedOperations = try container.decodeIfPresent([String].self, forKey: .completedOperations) ?? []
    }
}

// MARK: - Authoritative Resolved Lifecycle States

public enum ScheduleRecoveryResolvedState: Equatable, Sendable {
    case readyToApply
    case draft
    case applied(RecoveryExecutionResult)
    case appliedWithoutExecutionResult(ScheduleRecoveryProposal)
    case partiallyExecuted(result: RecoveryExecutionResult?, errorDetail: ScheduleRecoveryErrorDetail?)
    case failed(result: RecoveryExecutionResult?, errorDetail: ScheduleRecoveryErrorDetail?)
    case preconditionFailed(reason: String)
    case abortedWithoutExecutionResult(ScheduleRecoveryProposal)
    case cancelled
    case expired
    case terminalWithResultFetchError(proposal: ScheduleRecoveryProposal, error: String)

    public static func partiallyExecuted(_ result: RecoveryExecutionResult) -> ScheduleRecoveryResolvedState {
        .partiallyExecuted(result: result, errorDetail: nil)
    }

    public static func partiallyExecuted(_ detail: ScheduleRecoveryErrorDetail) -> ScheduleRecoveryResolvedState {
        .partiallyExecuted(result: nil, errorDetail: detail)
    }

    public static func failed(_ result: RecoveryExecutionResult) -> ScheduleRecoveryResolvedState {
        .failed(result: result, errorDetail: nil)
    }

    public static func failed(_ detail: ScheduleRecoveryErrorDetail) -> ScheduleRecoveryResolvedState {
        .failed(result: nil, errorDetail: detail)
    }

    public var isTerminal: Bool {
        switch self {
        case .readyToApply, .draft:
            return false
        case .applied, .appliedWithoutExecutionResult, .partiallyExecuted, .failed,
             .preconditionFailed, .abortedWithoutExecutionResult, .cancelled, .expired,
             .terminalWithResultFetchError:
            return true
        }
    }
}

// MARK: - Authoritative State Resolver

public struct ScheduleRecoveryStateResolver: Sendable {
    public static func resolve(
        proposal: ScheduleRecoveryProposal,
        result: RecoveryExecutionResult?
    ) -> ScheduleRecoveryResolvedState {
        switch proposal.status {
        case .pendingConfirmation:
            return .readyToApply
        case .draft:
            return .draft
        case .applied:
            if let result = result {
                return .applied(result)
            } else {
                return .appliedWithoutExecutionResult(proposal)
            }
        case .aborted:
            if let result = result {
                switch result.status {
                case .partiallyExecuted:
                    return .partiallyExecuted(result)
                case .failed:
                    if result.failedOperationId != nil {
                        return .failed(result)
                    } else {
                        let reason = result.failureReason ?? proposal.failureReason ?? "Precondition failed"
                        return .preconditionFailed(reason: reason)
                    }
                case .executed, .aborted:
                    let reason = result.failureReason ?? proposal.failureReason ?? "Proposal aborted"
                    return .preconditionFailed(reason: reason)
                }
            } else {
                return .abortedWithoutExecutionResult(proposal)
            }
        case .cancelled:
            return .cancelled
        case .expired:
            return .expired
        }
    }
}
