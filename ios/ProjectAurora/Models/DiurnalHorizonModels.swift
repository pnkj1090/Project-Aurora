import Foundation

// MARK: - Horizon Proposal Status

public enum HorizonProposalStatus: String, Codable, CaseIterable, Sendable {
    case draft = "draft"
    case approved = "approved"
    case executing = "executing"
    case partiallyExecuted = "partially_executed"
    case completed = "completed"
    case aborted = "aborted"
    case failed = "failed"
    case expired = "expired"
}

// MARK: - Horizon Operation Type

public enum HorizonOperationType: String, Codable, CaseIterable, Sendable {
    case createReminder = "create_reminder"
    case rolloverTask = "rollover_task"
    case createMemory = "create_memory"
    case createTask = "create_task"
    case updateTask = "update_task"
    case completeTask = "complete_task"
    case rescheduleTask = "reschedule_task"
    case updateReminder = "update_reminder"
    case cancelReminder = "cancel_reminder"
    case createCommitment = "create_commitment"
    case completeCommitment = "complete_commitment"
    case noop = "noop"
}

// MARK: - Horizon Operation Status

public enum HorizonOperationStatus: String, Codable, CaseIterable, Sendable {
    case pending = "pending"
    case applied = "applied"
    case failed = "failed"
    case skipped = "skipped"
}

// MARK: - Schedule Interval Item

public struct ScheduleIntervalItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(startAt)_\(endAt)_\(title)" }
    public let startAt: String
    public let endAt: String
    public let title: String
    public let sourceDomain: String
    public let isWorkingHour: Bool

    enum CodingKeys: String, CodingKey {
        case startAt = "start_at"
        case endAt = "end_at"
        case title
        case sourceDomain = "source_domain"
        case isWorkingHour = "is_working_hour"
    }

    public init(
        startAt: String,
        endAt: String,
        title: String,
        sourceDomain: String = "calendar",
        isWorkingHour: Bool = true
    ) {
        self.startAt = startAt
        self.endAt = endAt
        self.title = title
        self.sourceDomain = sourceDomain
        self.isWorkingHour = isWorkingHour
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        startAt = try container.decode(String.self, forKey: .startAt)
        endAt = try container.decode(String.self, forKey: .endAt)
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Scheduled Event"
        sourceDomain = (try container.decodeIfPresent(String.self, forKey: .sourceDomain)) ?? "calendar"
        isWorkingHour = (try container.decodeIfPresent(Bool.self, forKey: .isWorkingHour)) ?? true
    }
}

// MARK: - Horizon Free Slot Item

public struct HorizonFreeSlotItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(startAt)_\(endAt)" }
    public let startAt: String
    public let endAt: String
    public let durationMinutes: Int

    enum CodingKeys: String, CodingKey {
        case startAt = "start_at"
        case endAt = "end_at"
        case durationMinutes = "duration_minutes"
    }

    public init(startAt: String, endAt: String, durationMinutes: Int) {
        self.startAt = startAt
        self.endAt = endAt
        self.durationMinutes = durationMinutes
    }
}

// MARK: - Horizon Task Item

public struct HorizonTaskItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { taskId }
    public let taskId: String
    public let title: String
    public let priority: String?
    public let dueAt: String?
    public let estimatedDurationMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case idKey = "id"
        case title
        case priority
        case dueAt = "due_at"
        case estimatedDurationMinutes = "estimated_duration_minutes"
    }

    public init(
        taskId: String,
        title: String,
        priority: String? = nil,
        dueAt: String? = nil,
        estimatedDurationMinutes: Int? = nil
    ) {
        self.taskId = taskId
        self.title = title
        self.priority = priority
        self.dueAt = dueAt
        self.estimatedDurationMinutes = estimatedDurationMinutes
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let tid = try container.decodeIfPresent(String.self, forKey: .taskId) {
            taskId = tid
        } else if let tid = try container.decodeIfPresent(String.self, forKey: .idKey) {
            taskId = tid
        } else {
            taskId = UUID().uuidString
        }
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Untitled Task"
        priority = try container.decodeIfPresent(String.self, forKey: .priority)
        dueAt = try container.decodeIfPresent(String.self, forKey: .dueAt)
        estimatedDurationMinutes = try container.decodeIfPresent(Int.self, forKey: .estimatedDurationMinutes)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(taskId, forKey: .taskId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(priority, forKey: .priority)
        try container.encodeIfPresent(dueAt, forKey: .dueAt)
        try container.encodeIfPresent(estimatedDurationMinutes, forKey: .estimatedDurationMinutes)
    }
}

// MARK: - Horizon Commitment Item

public struct HorizonCommitmentItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { commitmentId }
    public let commitmentId: String
    public let title: String
    public let targetDate: String?
    public let status: String?

    enum CodingKeys: String, CodingKey {
        case commitmentId = "commitment_id"
        case idKey = "id"
        case title
        case targetDate = "target_date"
        case status
    }

    public init(
        commitmentId: String,
        title: String,
        targetDate: String? = nil,
        status: String? = nil
    ) {
        self.commitmentId = commitmentId
        self.title = title
        self.targetDate = targetDate
        self.status = status
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let cid = try container.decodeIfPresent(String.self, forKey: .commitmentId) {
            commitmentId = cid
        } else if let cid = try container.decodeIfPresent(String.self, forKey: .idKey) {
            commitmentId = cid
        } else {
            commitmentId = UUID().uuidString
        }
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Untitled Commitment"
        targetDate = try container.decodeIfPresent(String.self, forKey: .targetDate)
        status = try container.decodeIfPresent(String.self, forKey: .status)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(commitmentId, forKey: .commitmentId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(targetDate, forKey: .targetDate)
        try container.encodeIfPresent(status, forKey: .status)
    }
}

// MARK: - Horizon Reminder Item

public struct HorizonReminderItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { reminderId }
    public let reminderId: String
    public let title: String
    public let triggerAt: String?

    enum CodingKeys: String, CodingKey {
        case reminderId = "reminder_id"
        case idKey = "id"
        case title
        case triggerAt = "trigger_at"
        case scheduledAt = "scheduled_at"
    }

    public init(
        reminderId: String,
        title: String,
        triggerAt: String? = nil
    ) {
        self.reminderId = reminderId
        self.title = title
        self.triggerAt = triggerAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let rid = try container.decodeIfPresent(String.self, forKey: .reminderId) {
            reminderId = rid
        } else if let rid = try container.decodeIfPresent(String.self, forKey: .idKey) {
            reminderId = rid
        } else {
            reminderId = UUID().uuidString
        }
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Untitled Reminder"
        triggerAt = try (container.decodeIfPresent(String.self, forKey: .triggerAt) ?? container.decodeIfPresent(String.self, forKey: .scheduledAt))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(reminderId, forKey: .reminderId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(triggerAt, forKey: .triggerAt)
    }
}

// MARK: - Omitted Domain Item

public struct OmittedDomainItem: Codable, Equatable, Sendable {
    public let domain: String
    public let reason: String

    public init(domain: String, reason: String) {
        self.domain = domain
        self.reason = reason
    }
}

// MARK: - Evening Review Items

public struct CompletedTaskItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { taskId }
    public let taskId: String
    public let title: String
    public let completedAt: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case idKey = "id"
        case title
        case completedAt = "completed_at"
    }

    public init(taskId: String, title: String, completedAt: String? = nil) {
        self.taskId = taskId
        self.title = title
        self.completedAt = completedAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try (container.decodeIfPresent(String.self, forKey: .taskId) ?? container.decodeIfPresent(String.self, forKey: .idKey)) ?? UUID().uuidString
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Completed Task"
        completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(taskId, forKey: .taskId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(completedAt, forKey: .completedAt)
    }
}

public struct OverdueTaskItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { taskId }
    public let taskId: String
    public let title: String
    public let dueAt: String?
    public let priority: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case idKey = "id"
        case title
        case dueAt = "due_at"
        case priority
    }

    public init(taskId: String, title: String, dueAt: String? = nil, priority: String? = nil) {
        self.taskId = taskId
        self.title = title
        self.dueAt = dueAt
        self.priority = priority
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try (container.decodeIfPresent(String.self, forKey: .taskId) ?? container.decodeIfPresent(String.self, forKey: .idKey)) ?? UUID().uuidString
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Overdue Task"
        dueAt = try container.decodeIfPresent(String.self, forKey: .dueAt)
        priority = try container.decodeIfPresent(String.self, forKey: .priority)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(taskId, forKey: .taskId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(dueAt, forKey: .dueAt)
        try container.encodeIfPresent(priority, forKey: .priority)
    }
}

public struct CompletedCommitmentItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { commitmentId }
    public let commitmentId: String
    public let title: String
    public let completedAt: String?

    enum CodingKeys: String, CodingKey {
        case commitmentId = "commitment_id"
        case idKey = "id"
        case title
        case completedAt = "completed_at"
    }

    public init(commitmentId: String, title: String, completedAt: String? = nil) {
        self.commitmentId = commitmentId
        self.title = title
        self.completedAt = completedAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        commitmentId = try (container.decodeIfPresent(String.self, forKey: .commitmentId) ?? container.decodeIfPresent(String.self, forKey: .idKey)) ?? UUID().uuidString
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Completed Commitment"
        completedAt = try container.decodeIfPresent(String.self, forKey: .completedAt)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(commitmentId, forKey: .commitmentId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(completedAt, forKey: .completedAt)
    }
}

public struct RolloverCandidateItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { taskId }
    public let taskId: String
    public let title: String
    public let suggestedNewDate: String?
    public let reason: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case idKey = "id"
        case title
        case suggestedNewDate = "suggested_new_date"
        case reason
    }

    public init(taskId: String, title: String, suggestedNewDate: String? = nil, reason: String? = nil) {
        self.taskId = taskId
        self.title = title
        self.suggestedNewDate = suggestedNewDate
        self.reason = reason
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try (container.decodeIfPresent(String.self, forKey: .taskId) ?? container.decodeIfPresent(String.self, forKey: .idKey)) ?? UUID().uuidString
        title = (try container.decodeIfPresent(String.self, forKey: .title)) ?? "Rollover Task"
        suggestedNewDate = try container.decodeIfPresent(String.self, forKey: .suggestedNewDate)
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(taskId, forKey: .taskId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(suggestedNewDate, forKey: .suggestedNewDate)
        try container.encodeIfPresent(reason, forKey: .reason)
    }
}

// MARK: - Completion Stats Payload

public struct CompletionStatsPayload: Codable, Equatable, Sendable {
    public let completedTaskCount: Int
    public let overdueTaskCount: Int
    public let completedCommitmentCount: Int
    public let completionRatePercent: Double?

    enum CodingKeys: String, CodingKey {
        case completedTaskCount = "completed_task_count"
        case completedTasks = "completed_tasks"
        case overdueTaskCount = "overdue_task_count"
        case overdueTasks = "overdue_tasks"
        case completedCommitmentCount = "completed_commitment_count"
        case completedCommitments = "completed_commitments"
        case completionRatePercent = "completion_rate_percent"
    }

    public init(
        completedTaskCount: Int = 0,
        overdueTaskCount: Int = 0,
        completedCommitmentCount: Int = 0,
        completionRatePercent: Double? = nil
    ) {
        self.completedTaskCount = completedTaskCount
        self.overdueTaskCount = overdueTaskCount
        self.completedCommitmentCount = completedCommitmentCount
        self.completionRatePercent = completionRatePercent
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        completedTaskCount = (try? container.decodeIfPresent(Int.self, forKey: .completedTaskCount)) ?? (try? container.decodeIfPresent(Int.self, forKey: .completedTasks)) ?? 0
        overdueTaskCount = (try? container.decodeIfPresent(Int.self, forKey: .overdueTaskCount)) ?? (try? container.decodeIfPresent(Int.self, forKey: .overdueTasks)) ?? 0
        completedCommitmentCount = (try? container.decodeIfPresent(Int.self, forKey: .completedCommitmentCount)) ?? (try? container.decodeIfPresent(Int.self, forKey: .completedCommitments)) ?? 0
        completionRatePercent = try? container.decodeIfPresent(Double.self, forKey: .completionRatePercent)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(completedTaskCount, forKey: .completedTaskCount)
        try container.encode(overdueTaskCount, forKey: .overdueTaskCount)
        try container.encode(completedCommitmentCount, forKey: .completedCommitmentCount)
        try container.encodeIfPresent(completionRatePercent, forKey: .completionRatePercent)
    }
}

// MARK: - Focus Recommendation Payload

public struct DayPlanFocusRecommendationPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { focusTitle }
    public let focusTitle: String
    public let rationale: String
    public let suggestedTaskIds: [String]
    public let allocatedMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case focusTitle = "focus_title"
        case taskTitle = "task_title"
        case taskId = "task_id"
        case rationale
        case suggestedTaskIds = "suggested_task_ids"
        case allocatedMinutes = "allocated_minutes"
        case durationMinutes = "duration_minutes"
    }

    public init(
        focusTitle: String,
        rationale: String,
        suggestedTaskIds: [String] = [],
        allocatedMinutes: Int? = nil
    ) {
        self.focusTitle = focusTitle
        self.rationale = rationale
        self.suggestedTaskIds = suggestedTaskIds
        self.allocatedMinutes = allocatedMinutes
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let ft = try container.decodeIfPresent(String.self, forKey: .focusTitle) {
            focusTitle = ft
        } else if let tt = try container.decodeIfPresent(String.self, forKey: .taskTitle) {
            focusTitle = tt
        } else {
            focusTitle = "Focus Task"
        }
        rationale = (try container.decodeIfPresent(String.self, forKey: .rationale)) ?? ""
        if let tids = try container.decodeIfPresent([String].self, forKey: .suggestedTaskIds) {
            suggestedTaskIds = tids
        } else if let tid = try container.decodeIfPresent(String.self, forKey: .taskId) {
            suggestedTaskIds = [tid]
        } else {
            suggestedTaskIds = []
        }
        allocatedMinutes = try (container.decodeIfPresent(Int.self, forKey: .allocatedMinutes) ?? container.decodeIfPresent(Int.self, forKey: .durationMinutes))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(focusTitle, forKey: .focusTitle)
        try container.encode(rationale, forKey: .rationale)
        try container.encode(suggestedTaskIds, forKey: .suggestedTaskIds)
        try container.encodeIfPresent(allocatedMinutes, forKey: .allocatedMinutes)
    }
}

// MARK: - Horizon Operation Payload

public struct HorizonOperationPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { operationId }
    public let operationId: String
    public let operationType: HorizonOperationType
    public let targetDomain: String?
    public let targetEntityId: String?
    public let proposedPayload: [String: AnyCodableValue]
    public let status: HorizonOperationStatus
    public let executedAt: String?
    public let error: String?
    public let failureError: String?
    public let createdEntityId: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case operationType = "operation_type"
        case targetDomain = "target_domain"
        case targetEntityId = "target_entity_id"
        case proposedPayload = "proposed_payload"
        case status
        case executedAt = "executed_at"
        case error
        case failureError = "failure_error"
        case createdEntityId = "created_entity_id"
    }

    public init(
        operationId: String,
        operationType: HorizonOperationType,
        targetDomain: String? = nil,
        targetEntityId: String? = nil,
        proposedPayload: [String: AnyCodableValue] = [:],
        status: HorizonOperationStatus = .pending,
        executedAt: String? = nil,
        error: String? = nil,
        failureError: String? = nil,
        createdEntityId: String? = nil
    ) {
        self.operationId = operationId
        self.operationType = operationType
        self.targetDomain = targetDomain
        self.targetEntityId = targetEntityId
        self.proposedPayload = proposedPayload
        self.status = status
        self.executedAt = executedAt
        self.error = error
        self.failureError = failureError
        self.createdEntityId = createdEntityId
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        operationId = try container.decode(String.self, forKey: .operationId)
        operationType = try container.decode(HorizonOperationType.self, forKey: .operationType)
        targetDomain = try container.decodeIfPresent(String.self, forKey: .targetDomain)
        targetEntityId = try container.decodeIfPresent(String.self, forKey: .targetEntityId)
        proposedPayload = (try container.decodeIfPresent([String: AnyCodableValue].self, forKey: .proposedPayload)) ?? [:]
        status = (try container.decodeIfPresent(HorizonOperationStatus.self, forKey: .status)) ?? .pending
        executedAt = try container.decodeIfPresent(String.self, forKey: .executedAt)
        error = try container.decodeIfPresent(String.self, forKey: .error)
        failureError = try container.decodeIfPresent(String.self, forKey: .failureError)
        createdEntityId = try container.decodeIfPresent(String.self, forKey: .createdEntityId)
    }
}

// MARK: - Daily Executive Plan Payload (Read-Only Synthesis)

public struct DailyExecutivePlanPayload: Codable, Equatable, Sendable {
    public let planId: String?
    public let targetDate: String
    public let timezone: String?
    public let generatedAt: String?
    public let executiveSummary: String
    public let scheduleIntervals: [ScheduleIntervalItem]
    public let freeSlots: [HorizonFreeSlotItem]
    public let actionableTasks: [HorizonTaskItem]
    public let activeCommitments: [HorizonCommitmentItem]
    public let scheduledReminders: [HorizonReminderItem]
    public let focusRecommendations: [DayPlanFocusRecommendationPayload]
    public let omittedDomains: [OmittedDomainItem]

    enum CodingKeys: String, CodingKey {
        case planId = "plan_id"
        case targetDate = "target_date"
        case timezone
        case generatedAt = "generated_at"
        case executiveSummary = "executive_summary"
        case summary
        case scheduleIntervals = "schedule_intervals"
        case freeSlots = "free_slots"
        case actionableTasks = "actionable_tasks"
        case openTasks = "open_tasks"
        case activeCommitments = "active_commitments"
        case scheduledReminders = "scheduled_reminders"
        case focusRecommendations = "focus_recommendations"
        case omittedDomains = "omitted_domains"
    }

    public init(
        planId: String? = nil,
        targetDate: String,
        timezone: String? = nil,
        generatedAt: String? = nil,
        executiveSummary: String,
        scheduleIntervals: [ScheduleIntervalItem] = [],
        freeSlots: [HorizonFreeSlotItem] = [],
        actionableTasks: [HorizonTaskItem] = [],
        activeCommitments: [HorizonCommitmentItem] = [],
        scheduledReminders: [HorizonReminderItem] = [],
        focusRecommendations: [DayPlanFocusRecommendationPayload] = [],
        omittedDomains: [OmittedDomainItem] = []
    ) {
        self.planId = planId
        self.targetDate = targetDate
        self.timezone = timezone
        self.generatedAt = generatedAt
        self.executiveSummary = executiveSummary
        self.scheduleIntervals = scheduleIntervals
        self.freeSlots = freeSlots
        self.actionableTasks = actionableTasks
        self.activeCommitments = activeCommitments
        self.scheduledReminders = scheduledReminders
        self.focusRecommendations = focusRecommendations
        self.omittedDomains = omittedDomains
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        planId = try container.decodeIfPresent(String.self, forKey: .planId)
        targetDate = try container.decode(String.self, forKey: .targetDate)
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        if let s = try container.decodeIfPresent(String.self, forKey: .executiveSummary) {
            executiveSummary = s
        } else if let s = try container.decodeIfPresent(String.self, forKey: .summary) {
            executiveSummary = s
        } else {
            executiveSummary = ""
        }
        scheduleIntervals = try container.decodeIfPresent([ScheduleIntervalItem].self, forKey: .scheduleIntervals) ?? []
        freeSlots = try container.decodeIfPresent([HorizonFreeSlotItem].self, forKey: .freeSlots) ?? []
        if let tasks = try container.decodeIfPresent([HorizonTaskItem].self, forKey: .actionableTasks) {
            actionableTasks = tasks
        } else if let tasks = try container.decodeIfPresent([HorizonTaskItem].self, forKey: .openTasks) {
            actionableTasks = tasks
        } else {
            actionableTasks = []
        }
        activeCommitments = try container.decodeIfPresent([HorizonCommitmentItem].self, forKey: .activeCommitments) ?? []
        scheduledReminders = try container.decodeIfPresent([HorizonReminderItem].self, forKey: .scheduledReminders) ?? []
        focusRecommendations = try container.decodeIfPresent([DayPlanFocusRecommendationPayload].self, forKey: .focusRecommendations) ?? []
        omittedDomains = try container.decodeIfPresent([OmittedDomainItem].self, forKey: .omittedDomains) ?? []
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(planId, forKey: .planId)
        try container.encode(targetDate, forKey: .targetDate)
        try container.encodeIfPresent(timezone, forKey: .timezone)
        try container.encodeIfPresent(generatedAt, forKey: .generatedAt)
        try container.encode(executiveSummary, forKey: .executiveSummary)
        try container.encode(scheduleIntervals, forKey: .scheduleIntervals)
        try container.encode(freeSlots, forKey: .freeSlots)
        try container.encode(actionableTasks, forKey: .actionableTasks)
        try container.encode(activeCommitments, forKey: .activeCommitments)
        try container.encode(scheduledReminders, forKey: .scheduledReminders)
        try container.encode(focusRecommendations, forKey: .focusRecommendations)
        try container.encode(omittedDomains, forKey: .omittedDomains)
    }
}

// MARK: - Evening Horizon Review Payload (Read-Only Synthesis)

public struct EveningHorizonReviewPayload: Codable, Equatable, Sendable {
    public let reviewId: String?
    public let targetDate: String
    public let timezone: String?
    public let generatedAt: String?
    public let executiveSummary: String
    public let accomplishments: [String]
    public let completedTasks: [CompletedTaskItem]
    public let overdueTasks: [OverdueTaskItem]
    public let completedCommitments: [CompletedCommitmentItem]
    public let rolloverCandidates: [RolloverCandidateItem]
    public let completionStats: CompletionStatsPayload
    public let omittedDomains: [OmittedDomainItem]

    enum CodingKeys: String, CodingKey {
        case reviewId = "review_id"
        case targetDate = "target_date"
        case timezone
        case generatedAt = "generated_at"
        case executiveSummary = "executive_summary"
        case summary
        case accomplishments
        case completedTasks = "completed_tasks"
        case overdueTasks = "overdue_tasks"
        case completedCommitments = "completed_commitments"
        case rolloverCandidates = "rollover_candidates"
        case completionStats = "completion_stats"
        case omittedDomains = "omitted_domains"
    }

    public init(
        reviewId: String? = nil,
        targetDate: String,
        timezone: String? = nil,
        generatedAt: String? = nil,
        executiveSummary: String,
        accomplishments: [String] = [],
        completedTasks: [CompletedTaskItem] = [],
        overdueTasks: [OverdueTaskItem] = [],
        completedCommitments: [CompletedCommitmentItem] = [],
        rolloverCandidates: [RolloverCandidateItem] = [],
        completionStats: CompletionStatsPayload = CompletionStatsPayload(),
        omittedDomains: [OmittedDomainItem] = []
    ) {
        self.reviewId = reviewId
        self.targetDate = targetDate
        self.timezone = timezone
        self.generatedAt = generatedAt
        self.executiveSummary = executiveSummary
        self.accomplishments = accomplishments
        self.completedTasks = completedTasks
        self.overdueTasks = overdueTasks
        self.completedCommitments = completedCommitments
        self.rolloverCandidates = rolloverCandidates
        self.completionStats = completionStats
        self.omittedDomains = omittedDomains
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        reviewId = try container.decodeIfPresent(String.self, forKey: .reviewId)
        targetDate = try container.decode(String.self, forKey: .targetDate)
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        if let s = try container.decodeIfPresent(String.self, forKey: .executiveSummary) {
            executiveSummary = s
        } else if let s = try container.decodeIfPresent(String.self, forKey: .summary) {
            executiveSummary = s
        } else {
            executiveSummary = ""
        }
        accomplishments = try container.decodeIfPresent([String].self, forKey: .accomplishments) ?? []
        completedTasks = try container.decodeIfPresent([CompletedTaskItem].self, forKey: .completedTasks) ?? []
        overdueTasks = try container.decodeIfPresent([OverdueTaskItem].self, forKey: .overdueTasks) ?? []
        completedCommitments = try container.decodeIfPresent([CompletedCommitmentItem].self, forKey: .completedCommitments) ?? []
        rolloverCandidates = try container.decodeIfPresent([RolloverCandidateItem].self, forKey: .rolloverCandidates) ?? []
        completionStats = try container.decodeIfPresent(CompletionStatsPayload.self, forKey: .completionStats) ?? CompletionStatsPayload()
        omittedDomains = try container.decodeIfPresent([OmittedDomainItem].self, forKey: .omittedDomains) ?? []
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(reviewId, forKey: .reviewId)
        try container.encode(targetDate, forKey: .targetDate)
        try container.encodeIfPresent(timezone, forKey: .timezone)
        try container.encodeIfPresent(generatedAt, forKey: .generatedAt)
        try container.encode(executiveSummary, forKey: .executiveSummary)
        try container.encode(accomplishments, forKey: .accomplishments)
        try container.encode(completedTasks, forKey: .completedTasks)
        try container.encode(overdueTasks, forKey: .overdueTasks)
        try container.encode(completedCommitments, forKey: .completedCommitments)
        try container.encode(rolloverCandidates, forKey: .rolloverCandidates)
        try container.encode(completionStats, forKey: .completionStats)
        try container.encode(omittedDomains, forKey: .omittedDomains)
    }
}

// MARK: - Day Plan Proposal Payload (Server-Held Governed Proposal)

public struct DayPlanProposalPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }
    public let proposalId: String
    public let proposalType: String
    public let targetDate: String
    public let status: HorizonProposalStatus
    public let stateHash: String
    public let expiresAt: String
    public let orderedOperations: [HorizonOperationPayload]
    public let appliedAt: String?
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case proposalType = "proposal_type"
        case targetDate = "target_date"
        case status
        case stateHash = "state_hash"
        case expiresAt = "expires_at"
        case orderedOperations = "ordered_operations"
        case appliedAt = "applied_at"
        case failureReason = "failure_reason"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decode(String.self, forKey: .proposalId)
        proposalType = (try container.decodeIfPresent(String.self, forKey: .proposalType)) ?? "day_plan"
        targetDate = try container.decode(String.self, forKey: .targetDate)
        status = try container.decode(HorizonProposalStatus.self, forKey: .status)
        stateHash = try container.decode(String.self, forKey: .stateHash)
        expiresAt = try container.decode(String.self, forKey: .expiresAt)
        orderedOperations = (try container.decodeIfPresent([HorizonOperationPayload].self, forKey: .orderedOperations)) ?? []
        appliedAt = try container.decodeIfPresent(String.self, forKey: .appliedAt)
        failureReason = try container.decodeIfPresent(String.self, forKey: .failureReason)
    }

    public init(
        proposalId: String,
        proposalType: String = "day_plan",
        targetDate: String,
        status: HorizonProposalStatus,
        stateHash: String,
        expiresAt: String,
        orderedOperations: [HorizonOperationPayload] = [],
        appliedAt: String? = nil,
        failureReason: String? = nil
    ) {
        self.proposalId = proposalId
        self.proposalType = proposalType
        self.targetDate = targetDate
        self.status = status
        self.stateHash = stateHash
        self.expiresAt = expiresAt
        self.orderedOperations = orderedOperations
        self.appliedAt = appliedAt
        self.failureReason = failureReason
    }
}

// MARK: - Evening Review Proposal Payload (Server-Held Governed Proposal)

public struct EveningReviewProposalPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }
    public let proposalId: String
    public let proposalType: String
    public let targetDate: String
    public let status: HorizonProposalStatus
    public let stateHash: String
    public let expiresAt: String
    public let orderedOperations: [HorizonOperationPayload]
    public let appliedAt: String?
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case proposalType = "proposal_type"
        case targetDate = "target_date"
        case status
        case stateHash = "state_hash"
        case expiresAt = "expires_at"
        case orderedOperations = "ordered_operations"
        case appliedAt = "applied_at"
        case failureReason = "failure_reason"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decode(String.self, forKey: .proposalId)
        proposalType = (try container.decodeIfPresent(String.self, forKey: .proposalType)) ?? "evening_review"
        targetDate = try container.decode(String.self, forKey: .targetDate)
        status = try container.decode(HorizonProposalStatus.self, forKey: .status)
        stateHash = try container.decode(String.self, forKey: .stateHash)
        expiresAt = try container.decode(String.self, forKey: .expiresAt)
        orderedOperations = (try container.decodeIfPresent([HorizonOperationPayload].self, forKey: .orderedOperations)) ?? []
        appliedAt = try container.decodeIfPresent(String.self, forKey: .appliedAt)
        failureReason = try container.decodeIfPresent(String.self, forKey: .failureReason)
    }

    public init(
        proposalId: String,
        proposalType: String = "evening_review",
        targetDate: String,
        status: HorizonProposalStatus,
        stateHash: String,
        expiresAt: String,
        orderedOperations: [HorizonOperationPayload] = [],
        appliedAt: String? = nil,
        failureReason: String? = nil
    ) {
        self.proposalId = proposalId
        self.proposalType = proposalType
        self.targetDate = targetDate
        self.status = status
        self.stateHash = stateHash
        self.expiresAt = expiresAt
        self.orderedOperations = orderedOperations
        self.appliedAt = appliedAt
        self.failureReason = failureReason
    }
}

// MARK: - Request Payloads

public struct ProposeDayPlanRequestPayload: Codable, Equatable, Sendable {
    public let targetDate: String?
    public let timezone: String?
    public let selectedFocusTitle: String?
    public let userNotes: String?

    enum CodingKeys: String, CodingKey {
        case targetDate = "target_date"
        case timezone = "explicit_tz"
        case selectedFocusTitle = "selected_focus_title"
        case userNotes = "user_notes"
    }

    public init(
        targetDate: String? = nil,
        timezone: String? = nil,
        selectedFocusTitle: String? = nil,
        userNotes: String? = nil
    ) {
        self.targetDate = targetDate
        self.timezone = timezone
        self.selectedFocusTitle = selectedFocusTitle
        self.userNotes = userNotes
    }
}

public struct ApplyDayPlanRequestPayload: Codable, Equatable, Sendable {
    public let proposalId: String
    public let approved: Bool

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case approved
    }

    public init(proposalId: String, approved: Bool = true) {
        self.proposalId = proposalId
        self.approved = approved
    }
}

public struct ProposeEveningReviewRequestPayload: Codable, Equatable, Sendable {
    public let targetDate: String?
    public let timezone: String?
    public let rolloverTaskIds: [String]?
    public let reflectionNotes: String?

    enum CodingKeys: String, CodingKey {
        case targetDate = "target_date"
        case timezone = "explicit_tz"
        case rolloverTaskIds = "rollover_task_ids"
        case reflectionNotes = "reflection_content"
    }

    public init(
        targetDate: String? = nil,
        timezone: String? = nil,
        rolloverTaskIds: [String]? = nil,
        reflectionNotes: String? = nil
    ) {
        self.targetDate = targetDate
        self.timezone = timezone
        self.rolloverTaskIds = rolloverTaskIds
        self.reflectionNotes = reflectionNotes
    }
}

public struct ApplyEveningReviewRequestPayload: Codable, Equatable, Sendable {
    public let proposalId: String
    public let approved: Bool

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case approved
    }

    public init(proposalId: String, approved: Bool = true) {
        self.proposalId = proposalId
        self.approved = approved
    }
}

// MARK: - Client Presentation State (Not an authoritative proposal state machine)

public enum DiurnalHorizonSheetState: Equatable, Sendable {
    case idle
    case loadingSynthesis
    case synthesisLoaded
    case generatingProposal
    case proposalReady
    case applying(proposalId: String)
    case aborting(proposalId: String)
    case completedPresentation
    case partiallyExecutedPresentation
    case proposalFailedPresentation(failureReason: String?, operations: [HorizonOperationPayload])
    case abortedPresentation
    case failedPresentation(message: String)
    case mutationOutcomeUnknown(proposalId: String)
}
