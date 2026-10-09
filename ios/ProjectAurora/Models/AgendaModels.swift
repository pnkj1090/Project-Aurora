import Foundation

// MARK: - Agenda Temporal Kind

public enum AgendaTemporalKind: String, Codable, CaseIterable, Sendable {
    case interval = "interval"
    case instant = "instant"
    case dateTarget = "date_target"
}

// MARK: - Agenda Conflict Payload

public struct AgendaConflictPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { conflictId }

    public let conflictId: String
    public let conflictType: String
    public let severity: String
    public let description: String
    public let itemIds: [String]
    public let detectedAt: String
    public let suggestedAction: String?

    enum CodingKeys: String, CodingKey {
        case conflictId = "conflict_id"
        case conflictType = "conflict_type"
        case severity
        case description
        case itemIds = "item_ids"
        case detectedAt = "detected_at"
        case suggestedAction = "suggested_action"
    }

    public init(
        conflictId: String,
        conflictType: String,
        severity: String,
        description: String,
        itemIds: [String] = [],
        detectedAt: String = "",
        suggestedAction: String? = nil
    ) {
        self.conflictId = conflictId
        self.conflictType = conflictType
        self.severity = severity
        self.description = description
        self.itemIds = itemIds
        self.detectedAt = detectedAt
        self.suggestedAction = suggestedAction
    }
}

// MARK: - Free Slot Payload

public struct FreeSlotPayload: Codable, Equatable, Sendable, Identifiable {
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

// MARK: - Agenda Item Payload

public struct AgendaItemPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { itemId }

    public let itemId: String
    public let sourceDomain: String
    public let sourceEntityId: String
    public let displayTitle: String
    public let temporalKind: AgendaTemporalKind
    public let startAt: String?
    public let endAt: String?
    public let instantAt: String?
    public let targetDate: String?
    public let isAllDay: Bool
    public let status: String
    public let priority: String?
    public let sourceCommitmentId: String?
    public let conflictIds: [String]

    enum CodingKeys: String, CodingKey {
        case itemId = "item_id"
        case sourceDomain = "source_domain"
        case sourceEntityId = "source_entity_id"
        case displayTitle = "display_title"
        case temporalKind = "temporal_kind"
        case startAt = "start_at"
        case endAt = "end_at"
        case instantAt = "instant_at"
        case targetDate = "target_date"
        case isAllDay = "is_all_day"
        case status
        case priority
        case sourceCommitmentId = "source_commitment_id"
        case conflictIds = "conflict_ids"
    }

    public init(
        itemId: String,
        sourceDomain: String,
        sourceEntityId: String,
        displayTitle: String,
        temporalKind: AgendaTemporalKind,
        startAt: String? = nil,
        endAt: String? = nil,
        instantAt: String? = nil,
        targetDate: String? = nil,
        isAllDay: Bool = false,
        status: String = "",
        priority: String? = nil,
        sourceCommitmentId: String? = nil,
        conflictIds: [String] = []
    ) {
        self.itemId = itemId
        self.sourceDomain = sourceDomain
        self.sourceEntityId = sourceEntityId
        self.displayTitle = displayTitle
        self.temporalKind = temporalKind
        self.startAt = startAt
        self.endAt = endAt
        self.instantAt = instantAt
        self.targetDate = targetDate
        self.isAllDay = isAllDay
        self.status = status
        self.priority = priority
        self.sourceCommitmentId = sourceCommitmentId
        self.conflictIds = conflictIds
    }
}

// MARK: - Omitted Source Payload

public struct OmittedSourcePayload: Codable, Equatable, Sendable {
    public let domain: String
    public let reason: String

    public init(domain: String, reason: String) {
        self.domain = domain
        self.reason = reason
    }
}

// MARK: - Daily Agenda Response Payload

public struct DailyAgendaResponsePayload: Codable, Equatable, Sendable {
    public let targetDate: String
    public let timezone: String
    public let items: [AgendaItemPayload]
    public let conflicts: [AgendaConflictPayload]
    public let freeSlots: [FreeSlotPayload]
    public let omittedSources: [OmittedSourcePayload]

    enum CodingKeys: String, CodingKey {
        case targetDate = "target_date"
        case timezone
        case items
        case conflicts
        case freeSlots = "free_slots"
        case omittedSources = "omitted_sources"
    }

    public init(
        targetDate: String,
        timezone: String,
        items: [AgendaItemPayload] = [],
        conflicts: [AgendaConflictPayload] = [],
        freeSlots: [FreeSlotPayload] = [],
        omittedSources: [OmittedSourcePayload] = []
    ) {
        self.targetDate = targetDate
        self.timezone = timezone
        self.items = items
        self.conflicts = conflicts
        self.freeSlots = freeSlots
        self.omittedSources = omittedSources
    }
}

// MARK: - Proposed Slot Response Payload

public struct ProposedSlotResponsePayload: Codable, Equatable, Sendable {
    public let feasible: Bool
    public let taskId: String
    public let taskTitle: String
    public let deadlineUtc: String?
    public let proposedSlots: [FreeSlotPayload]
    public let warnings: [String]
    public let maxAvailableDurationMinutes: Int
    public let reason: String?

    enum CodingKeys: String, CodingKey {
        case feasible
        case taskId = "task_id"
        case taskTitle = "task_title"
        case deadlineUtc = "deadline_utc"
        case proposedSlots = "proposed_slots"
        case warnings
        case maxAvailableDurationMinutes = "max_available_duration_minutes"
        case reason
    }

    public init(
        feasible: Bool,
        taskId: String,
        taskTitle: String,
        deadlineUtc: String? = nil,
        proposedSlots: [FreeSlotPayload] = [],
        warnings: [String] = [],
        maxAvailableDurationMinutes: Int = 0,
        reason: String? = nil
    ) {
        self.feasible = feasible
        self.taskId = taskId
        self.taskTitle = taskTitle
        self.deadlineUtc = deadlineUtc
        self.proposedSlots = proposedSlots
        self.warnings = warnings
        self.maxAvailableDurationMinutes = maxAvailableDurationMinutes
        self.reason = reason
    }
}

// MARK: - Conflict Scan Response Payload

public struct ConflictScanResponsePayload: Codable, Equatable, Sendable {
    public let conflicts: [AgendaConflictPayload]
    public let totalConflicts: Int
    public let evaluatedWindowStart: String
    public let evaluatedWindowEnd: String
    public let omittedDomains: [String]

    enum CodingKeys: String, CodingKey {
        case conflicts
        case totalConflicts = "total_conflicts"
        case evaluatedWindowStart = "evaluated_window_start"
        case evaluatedWindowEnd = "evaluated_window_end"
        case omittedDomains = "omitted_domains"
    }

    public init(
        conflicts: [AgendaConflictPayload] = [],
        totalConflicts: Int = 0,
        evaluatedWindowStart: String = "",
        evaluatedWindowEnd: String = "",
        omittedDomains: [String] = []
    ) {
        self.conflicts = conflicts
        self.totalConflicts = totalConflicts
        self.evaluatedWindowStart = evaluatedWindowStart
        self.evaluatedWindowEnd = evaluatedWindowEnd
        self.omittedDomains = omittedDomains
    }
}
