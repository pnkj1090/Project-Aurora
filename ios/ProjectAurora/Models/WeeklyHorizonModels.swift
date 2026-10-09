import Foundation

// MARK: - Weekly Horizon Congestion Level

public enum WeeklyHorizonCongestionLevel: String, Codable, CaseIterable, Sendable {
    case light = "light"
    case balanced = "balanced"
    case congested = "congested"
}

// MARK: - Weekly Proposal Status

public enum WeeklyProposalStatus: String, Codable, CaseIterable, Sendable {
    case draft = "draft"
    case approved = "approved"
    case executing = "executing"
    case executed = "executed"
    case partiallyExecuted = "partially_executed"
    case failed = "failed"
    case aborted = "aborted"
    case expired = "expired"
}

// MARK: - Weekly Operation Type

public enum WeeklyOperationType: String, Codable, CaseIterable, Sendable {
    case allocateTaskDueDate = "allocate_task_due_date"
    case createMilestoneReminder = "create_milestone_reminder"
    case createStrategicMemory = "create_strategic_memory"
    case completeHonoredCommitment = "complete_honored_commitment"
}

// MARK: - Weekly Operation Status

public enum WeeklyOperationStatus: String, Codable, CaseIterable, Sendable {
    case pending = "pending"
    case completed = "completed"
    case failed = "failed"
    case skipped = "skipped"
}

// MARK: - Day Capacity Summary

public struct DayCapacitySummary: Codable, Equatable, Sendable, Identifiable {
    public var id: String { dateStr }

    public let dateStr: String
    public let dayOfWeek: String
    public let totalFocusMinutes: Int
    public let allocatedFocusMinutes: Int
    public let freeFocusMinutes: Int
    public let eventsCount: Int
    public let tasksCount: Int
    public let congestionLevel: WeeklyHorizonCongestionLevel
    public let isOvercommitted: Bool

    enum CodingKeys: String, CodingKey {
        case dateStr = "date_str"
        case dayOfWeek = "day_of_week"
        case totalFocusMinutes = "total_focus_minutes"
        case allocatedFocusMinutes = "allocated_focus_minutes"
        case freeFocusMinutes = "free_focus_minutes"
        case eventsCount = "events_count"
        case tasksCount = "tasks_count"
        case congestionLevel = "congestion_level"
        case isOvercommitted = "is_overcommitted"
    }

    public init(
        dateStr: String,
        dayOfWeek: String,
        totalFocusMinutes: Int,
        allocatedFocusMinutes: Int,
        freeFocusMinutes: Int,
        eventsCount: Int,
        tasksCount: Int,
        congestionLevel: WeeklyHorizonCongestionLevel,
        isOvercommitted: Bool = false
    ) {
        self.dateStr = dateStr
        self.dayOfWeek = dayOfWeek
        self.totalFocusMinutes = totalFocusMinutes
        self.allocatedFocusMinutes = allocatedFocusMinutes
        self.freeFocusMinutes = freeFocusMinutes
        self.eventsCount = eventsCount
        self.tasksCount = tasksCount
        self.congestionLevel = congestionLevel
        self.isOvercommitted = isOvercommitted
    }
}

// MARK: - Commitment Health Item

public struct CommitmentHealthItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { commitmentId }

    public let commitmentId: String
    public let title: String
    public let targetDate: String?
    public let linkedTasksCount: Int
    public let completedTasksCount: Int
    public let openTasksCount: Int
    public let isOrphan: Bool
    public let hasOverrunRisk: Bool
    public let overrunWarning: String?

    enum CodingKeys: String, CodingKey {
        case commitmentId = "commitment_id"
        case title
        case targetDate = "target_date"
        case linkedTasksCount = "linked_tasks_count"
        case completedTasksCount = "completed_tasks_count"
        case openTasksCount = "open_tasks_count"
        case isOrphan = "is_orphan"
        case hasOverrunRisk = "has_overrun_risk"
        case overrunWarning = "overrun_warning"
    }

    public init(
        commitmentId: String,
        title: String,
        targetDate: String? = nil,
        linkedTasksCount: Int = 0,
        completedTasksCount: Int = 0,
        openTasksCount: Int = 0,
        isOrphan: Bool = false,
        hasOverrunRisk: Bool = false,
        overrunWarning: String? = nil
    ) {
        self.commitmentId = commitmentId
        self.title = title
        self.targetDate = targetDate
        self.linkedTasksCount = linkedTasksCount
        self.completedTasksCount = completedTasksCount
        self.openTasksCount = openTasksCount
        self.isOrphan = isOrphan
        self.hasOverrunRisk = hasOverrunRisk
        self.overrunWarning = overrunWarning
    }
}

// MARK: - Weekly Strategic Horizon

public struct WeeklyStrategicHorizon: Codable, Equatable, Sendable {
    public let userId: String
    public let timezone: String
    public let horizonStartDate: String
    public let horizonEndDate: String
    public let daySummaries: [DayCapacitySummary]
    public let commitmentHealth: [CommitmentHealthItem]
    public let orphanCommitmentsCount: Int
    public let overcommittedDaysCount: Int
    public let totalFreeFocusMinutes: Int
    public let strategicRecommendations: [String]

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case timezone
        case horizonStartDate = "horizon_start_date"
        case horizonEndDate = "horizon_end_date"
        case daySummaries = "day_summaries"
        case commitmentHealth = "commitment_health"
        case orphanCommitmentsCount = "orphan_commitments_count"
        case overcommittedDaysCount = "overcommitted_days_count"
        case totalFreeFocusMinutes = "total_free_focus_minutes"
        case strategicRecommendations = "strategic_recommendations"
    }

    public init(
        userId: String,
        timezone: String,
        horizonStartDate: String,
        horizonEndDate: String,
        daySummaries: [DayCapacitySummary] = [],
        commitmentHealth: [CommitmentHealthItem] = [],
        orphanCommitmentsCount: Int = 0,
        overcommittedDaysCount: Int = 0,
        totalFreeFocusMinutes: Int = 0,
        strategicRecommendations: [String] = []
    ) {
        self.userId = userId
        self.timezone = timezone
        self.horizonStartDate = horizonStartDate
        self.horizonEndDate = horizonEndDate
        self.daySummaries = daySummaries
        self.commitmentHealth = commitmentHealth
        self.orphanCommitmentsCount = orphanCommitmentsCount
        self.overcommittedDaysCount = overcommittedDaysCount
        self.totalFreeFocusMinutes = totalFreeFocusMinutes
        self.strategicRecommendations = strategicRecommendations
    }
}

// MARK: - Weekly Plan Proposal

public struct WeeklyPlanProposal: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }

    public let proposalId: String
    public let userId: String
    public let proposalType: String
    public let status: WeeklyProposalStatus
    public let createdAt: String
    public let expiresAt: String
    public let stateHash: String
    public let requiredPermissions: [String]

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case userId = "user_id"
        case proposalType = "proposal_type"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case stateHash = "state_hash"
        case requiredPermissions = "required_permissions"
    }

    public init(
        proposalId: String,
        userId: String,
        proposalType: String = "weekly_plan",
        status: WeeklyProposalStatus = .draft,
        createdAt: String,
        expiresAt: String,
        stateHash: String,
        requiredPermissions: [String] = []
    ) {
        self.proposalId = proposalId
        self.userId = userId
        self.proposalType = proposalType
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.stateHash = stateHash
        self.requiredPermissions = requiredPermissions
    }
}

// MARK: - Weekly Retrospective

public struct WeeklyRetrospective: Codable, Equatable, Sendable {
    public let userId: String
    public let timezone: String
    public let lookbackStartDate: String
    public let lookbackEndDate: String
    public let completedTasksCount: Int
    public let honoredCommitmentsCount: Int
    public let unfulfilledCommitmentsCount: Int
    public let velocityScore: Double

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case timezone
        case lookbackStartDate = "lookback_start_date"
        case lookbackEndDate = "lookback_end_date"
        case completedTasksCount = "completed_tasks_count"
        case honoredCommitmentsCount = "honored_commitments_count"
        case unfulfilledCommitmentsCount = "unfulfilled_commitments_count"
        case velocityScore = "velocity_score"
    }

    public init(
        userId: String,
        timezone: String,
        lookbackStartDate: String,
        lookbackEndDate: String,
        completedTasksCount: Int = 0,
        honoredCommitmentsCount: Int = 0,
        unfulfilledCommitmentsCount: Int = 0,
        velocityScore: Double = 0.0
    ) {
        self.userId = userId
        self.timezone = timezone
        self.lookbackStartDate = lookbackStartDate
        self.lookbackEndDate = lookbackEndDate
        self.completedTasksCount = completedTasksCount
        self.honoredCommitmentsCount = honoredCommitmentsCount
        self.unfulfilledCommitmentsCount = unfulfilledCommitmentsCount
        self.velocityScore = velocityScore
    }
}

// MARK: - Weekly Retrospective Proposal

public struct WeeklyRetrospectiveProposal: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }

    public let proposalId: String
    public let userId: String
    public let proposalType: String
    public let status: WeeklyProposalStatus
    public let createdAt: String
    public let expiresAt: String
    public let stateHash: String
    public let requiredPermissions: [String]

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case userId = "user_id"
        case proposalType = "proposal_type"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case stateHash = "state_hash"
        case requiredPermissions = "required_permissions"
    }

    public init(
        proposalId: String,
        userId: String,
        proposalType: String = "weekly_retrospective",
        status: WeeklyProposalStatus = .draft,
        createdAt: String,
        expiresAt: String,
        stateHash: String,
        requiredPermissions: [String] = []
    ) {
        self.proposalId = proposalId
        self.userId = userId
        self.proposalType = proposalType
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.stateHash = stateHash
        self.requiredPermissions = requiredPermissions
    }
}
