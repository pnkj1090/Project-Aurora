//
//  ScheduleDisruptionDiagnosticModels.swift
//  Aurora
//

import Foundation

public enum ScheduleDisruptionSeverity: String, Codable, Sendable {
    case none = "none"
    case minor = "minor"
    case moderate = "moderate"
    case critical = "critical"
}

public enum TaskJeopardyReason: String, Codable, Sendable {
    case overdue = "overdue"
    case deadlineDuringEvent = "deadline_during_event"
    case commitmentOverrunRisk = "commitment_overrun_risk"
    case workingHoursOverflow = "working_hours_overflow"
    case unscheduledWithTarget = "unscheduled_with_target"
}

public struct TaskJeopardyItem: Codable, Identifiable, Equatable, Sendable {
    public var id: String { taskId }
    public let taskId: String
    public let title: String
    public let currentDueAt: Date?
    public let linkedCommitmentId: String?
    public let commitmentTargetDate: String?
    public let jeopardyReasons: [TaskJeopardyReason]
    public let estimatedDurationMinutes: Int

    public init(
        taskId: String,
        title: String,
        currentDueAt: Date? = nil,
        linkedCommitmentId: String? = nil,
        commitmentTargetDate: String? = nil,
        jeopardyReasons: [TaskJeopardyReason],
        estimatedDurationMinutes: Int
    ) {
        self.taskId = taskId
        self.title = title
        self.currentDueAt = currentDueAt
        self.linkedCommitmentId = linkedCommitmentId
        self.commitmentTargetDate = commitmentTargetDate
        self.jeopardyReasons = jeopardyReasons
        self.estimatedDurationMinutes = estimatedDurationMinutes
    }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case title
        case currentDueAt = "current_due_at"
        case linkedCommitmentId = "linked_commitment_id"
        case commitmentTargetDate = "commitment_target_date"
        case jeopardyReasons = "jeopardy_reasons"
        case estimatedDurationMinutes = "estimated_duration_minutes"
    }
}

public struct ScheduleDisruptionDiagnostic: Codable, Identifiable, Equatable, Sendable {
    public var id: String { diagnosticId }
    public let diagnosticId: String
    public let computedAt: Date
    public let evaluationWindowStart: Date
    public let evaluationWindowEnd: Date
    public let timezone: String
    public let severity: ScheduleDisruptionSeverity
    public let totalWorkingMinutes: Int
    public let availableWorkingMinutes: Int
    public let requiredTaskMinutes: Int
    public let focusCapacityDeficitMinutes: Int
    public let overdueTaskCount: Int
    public let jeopardyTasks: [TaskJeopardyItem]
    public let recommendedRecoveryTaskIds: [String]
    public let recommendedRecoveryReminderIds: [String]
    public let omittedDomains: [String]

    public init(
        diagnosticId: String,
        computedAt: Date,
        evaluationWindowStart: Date,
        evaluationWindowEnd: Date,
        timezone: String,
        severity: ScheduleDisruptionSeverity,
        totalWorkingMinutes: Int,
        availableWorkingMinutes: Int,
        requiredTaskMinutes: Int,
        focusCapacityDeficitMinutes: Int,
        overdueTaskCount: Int,
        jeopardyTasks: [TaskJeopardyItem],
        recommendedRecoveryTaskIds: [String],
        recommendedRecoveryReminderIds: [String],
        omittedDomains: [String]
    ) {
        self.diagnosticId = diagnosticId
        self.computedAt = computedAt
        self.evaluationWindowStart = evaluationWindowStart
        self.evaluationWindowEnd = evaluationWindowEnd
        self.timezone = timezone
        self.severity = severity
        self.totalWorkingMinutes = totalWorkingMinutes
        self.availableWorkingMinutes = availableWorkingMinutes
        self.requiredTaskMinutes = requiredTaskMinutes
        self.focusCapacityDeficitMinutes = focusCapacityDeficitMinutes
        self.overdueTaskCount = overdueTaskCount
        self.jeopardyTasks = jeopardyTasks
        self.recommendedRecoveryTaskIds = recommendedRecoveryTaskIds
        self.recommendedRecoveryReminderIds = recommendedRecoveryReminderIds
        self.omittedDomains = omittedDomains
    }

    enum CodingKeys: String, CodingKey {
        case diagnosticId = "diagnostic_id"
        case computedAt = "computed_at"
        case evaluationWindowStart = "evaluation_window_start"
        case evaluationWindowEnd = "evaluation_window_end"
        case timezone
        case severity
        case totalWorkingMinutes = "total_working_minutes"
        case availableWorkingMinutes = "available_working_minutes"
        case requiredTaskMinutes = "required_task_minutes"
        case focusCapacityDeficitMinutes = "focus_capacity_deficit_minutes"
        case overdueTaskCount = "overdue_task_count"
        case jeopardyTasks = "jeopardy_tasks"
        case recommendedRecoveryTaskIds = "recommended_recovery_task_ids"
        case recommendedRecoveryReminderIds = "recommended_recovery_reminder_ids"
        case omittedDomains = "omitted_domains"
    }
}

public struct DiagnoseScheduleDisruptionPayload: Codable, Sendable {
    public let targetWindowStart: Date?
    public let targetWindowEnd: Date?
    public let clientPreferredTimezone: String?
    public let includeReminders: Bool

    public init(
        targetWindowStart: Date? = nil,
        targetWindowEnd: Date? = nil,
        clientPreferredTimezone: String? = nil,
        includeReminders: Bool = false
    ) {
        self.targetWindowStart = targetWindowStart
        self.targetWindowEnd = targetWindowEnd
        self.clientPreferredTimezone = clientPreferredTimezone
        self.includeReminders = includeReminders
    }

    enum CodingKeys: String, CodingKey {
        case targetWindowStart = "target_window_start"
        case targetWindowEnd = "target_window_end"
        case clientPreferredTimezone = "client_preferred_timezone"
        case includeReminders = "include_reminders"
    }
}
