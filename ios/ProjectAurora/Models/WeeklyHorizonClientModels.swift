import Foundation

// MARK: - Helper Extension for AnyCodableValue

extension AnyCodableValue {
    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }
}

// MARK: - Weekly Proposal Type

public enum WeeklyProposalType: String, Codable, CaseIterable, Sendable {
    case weeklyPlan = "weekly_plan"
    case weeklyRetrospective = "weekly_retrospective"
}

// MARK: - Weekly Reminder Link Type

public enum WeeklyReminderLinkType: String, Codable, CaseIterable, Sendable {
    case none = "none"
    case task = "task"
    case commitment = "commitment"
}

// MARK: - Weekly Apply Operation

public enum WeeklyApplyOperation: String, Codable, Equatable, Sendable {
    case applyPlan = "apply_plan"
    case applyRetrospective = "apply_retrospective"
}

// MARK: - Weekly Request Kind (Explicit Request Provenance)

public enum WeeklyRequestKind: String, Codable, Equatable, Sendable {
    case horizonSynthesis = "horizon_synthesis"
    case retrospectiveSynthesis = "retrospective_synthesis"
    case proposePlan = "propose_plan"
    case applyPlan = "apply_plan"
    case proposeRetrospective = "propose_retrospective"
    case applyRetrospective = "apply_retrospective"
    case recoverProposal = "recover_proposal"
    case abortProposal = "abort_proposal"
}

// MARK: - Typed Recovery-Integrity Contract Error

public enum WeeklyHorizonContractViolation: LocalizedError, Equatable, Sendable {
    case proposalIdentityMismatch(expectedId: String, actualId: String)
    case proposalTypeMismatch(expectedType: WeeklyProposalType, actualType: WeeklyProposalType)
    case proposalIntegrityMismatch(
        expectedId: String,
        expectedType: WeeklyProposalType,
        actualId: String,
        actualType: WeeklyProposalType
    )

    public var errorDescription: String? {
        switch self {
        case .proposalIdentityMismatch(let expected, let actual):
            return "Proposal identity mismatch: expected ID '\(expected)', received '\(actual)'."
        case .proposalTypeMismatch(let expected, let actual):
            return "Proposal type mismatch: expected type '\(expected.rawValue)', received '\(actual.rawValue)'."
        case .proposalIntegrityMismatch(let expectedId, let expectedType, let actualId, let actualType):
            return "Proposal integrity mismatch: expected \(expectedType.rawValue) [\(expectedId)], received \(actualType.rawValue) [\(actualId)]."
        }
    }
}

// MARK: - Strongly Typed Request Models

public struct WeeklyTaskAllocationPayload: Codable, Equatable, Sendable {
    public let taskId: String
    public let targetDate: String
    public let allocatedSlotStart: String
    public let allocatedSlotEnd: String
    public let effectiveDurationMinutes: Int

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case targetDate = "target_date"
        case allocatedSlotStart = "allocated_slot_start"
        case allocatedSlotEnd = "allocated_slot_end"
        case effectiveDurationMinutes = "effective_duration_minutes"
    }

    public init(
        taskId: String,
        targetDate: String,
        allocatedSlotStart: String,
        allocatedSlotEnd: String,
        effectiveDurationMinutes: Int
    ) {
        self.taskId = taskId
        self.targetDate = targetDate
        self.allocatedSlotStart = allocatedSlotStart
        self.allocatedSlotEnd = allocatedSlotEnd
        self.effectiveDurationMinutes = effectiveDurationMinutes
    }
}

public struct WeeklyMilestoneReminderPayload: Codable, Equatable, Sendable {
    public let exactTitle: String
    public let exactScheduledAt: String
    public let linkedEntityType: WeeklyReminderLinkType
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
        linkedEntityType: WeeklyReminderLinkType = .none,
        linkedEntityId: String? = nil
    ) {
        self.exactTitle = exactTitle
        self.exactScheduledAt = exactScheduledAt
        self.linkedEntityType = linkedEntityType
        self.linkedEntityId = linkedEntityId
    }
}

public struct ProposeWeeklyPlanRequestPayload: Codable, Equatable, Sendable {
    public let taskAllocations: [WeeklyTaskAllocationPayload]?
    public let milestoneReminders: [WeeklyMilestoneReminderPayload]?
    public let strategicIntentNotes: String?
    public let targetStartDate: String?
    public let explicitTz: String?
    public let workStartHour: Int?
    public let workEndHour: Int?

    enum CodingKeys: String, CodingKey {
        case taskAllocations = "task_allocations"
        case milestoneReminders = "milestone_reminders"
        case strategicIntentNotes = "strategic_intent_notes"
        case targetStartDate = "target_start_date"
        case explicitTz = "explicit_tz"
        case workStartHour = "work_start_hour"
        case workEndHour = "work_end_hour"
    }

    public init(
        taskAllocations: [WeeklyTaskAllocationPayload]? = nil,
        milestoneReminders: [WeeklyMilestoneReminderPayload]? = nil,
        strategicIntentNotes: String? = nil,
        targetStartDate: String? = nil,
        explicitTz: String? = nil,
        workStartHour: Int? = nil,
        workEndHour: Int? = nil
    ) {
        self.taskAllocations = taskAllocations
        self.milestoneReminders = milestoneReminders
        self.strategicIntentNotes = strategicIntentNotes
        self.targetStartDate = targetStartDate
        self.explicitTz = explicitTz
        self.workStartHour = workStartHour
        self.workEndHour = workEndHour
    }
}

public struct ApplyWeeklyPlanRequestPayload: Codable, Equatable, Sendable {
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

public struct ProposeWeeklyRetrospectiveRequestPayload: Codable, Equatable, Sendable {
    public let commitmentIds: [String]?
    public let retrospectiveNotes: String?
    public let lookbackEndDate: String?
    public let explicitTz: String?

    enum CodingKeys: String, CodingKey {
        case commitmentIds = "commitment_ids"
        case retrospectiveNotes = "retrospective_notes"
        case lookbackEndDate = "lookback_end_date"
        case explicitTz = "explicit_tz"
    }

    public init(
        commitmentIds: [String]? = nil,
        retrospectiveNotes: String? = nil,
        lookbackEndDate: String? = nil,
        explicitTz: String? = nil
    ) {
        self.commitmentIds = commitmentIds
        self.retrospectiveNotes = retrospectiveNotes
        self.lookbackEndDate = lookbackEndDate
        self.explicitTz = explicitTz
    }
}

public struct ApplyWeeklyRetrospectiveRequestPayload: Codable, Equatable, Sendable {
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

// MARK: - Strongly Typed Operation Models

public struct WeeklyOperationPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { operationId }
    public let operationId: String
    public let operationType: WeeklyOperationType
    public let executionOrder: Int
    public let status: WeeklyOperationStatus
    public let requiredPermissions: [String]
    public let errorMessage: String?
    public let createdEntityId: String?
    public let dependencyEntities: [[String: String]]
    public let targetEntityId: String?
    public let targetEntityType: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case operationType = "operation_type"
        case executionOrder = "execution_order"
        case status
        case requiredPermissions = "required_permissions"
        case errorMessage = "error_message"
        case createdEntityId = "created_entity_id"
        case dependencyEntities = "dependency_entities"
        case precondition
        case targetEntityId = "target_entity_id"
        case targetEntityType = "target_entity_type"
    }

    public init(
        operationId: String,
        operationType: WeeklyOperationType,
        executionOrder: Int = 0,
        status: WeeklyOperationStatus,
        requiredPermissions: [String] = [],
        errorMessage: String? = nil,
        createdEntityId: String? = nil,
        dependencyEntities: [[String: String]] = [],
        targetEntityId: String? = nil,
        targetEntityType: String? = nil
    ) {
        self.operationId = operationId
        self.operationType = operationType
        self.executionOrder = executionOrder
        self.status = status
        self.requiredPermissions = requiredPermissions
        self.errorMessage = errorMessage
        self.createdEntityId = createdEntityId
        self.dependencyEntities = dependencyEntities
        self.targetEntityId = targetEntityId
        self.targetEntityType = targetEntityType
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        operationId = try container.decode(String.self, forKey: .operationId)
        operationType = try container.decode(WeeklyOperationType.self, forKey: .operationType)
        executionOrder = try container.decodeIfPresent(Int.self, forKey: .executionOrder) ?? 0
        status = try container.decode(WeeklyOperationStatus.self, forKey: .status)
        requiredPermissions = try container.decodeIfPresent([String].self, forKey: .requiredPermissions) ?? []
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        createdEntityId = try container.decodeIfPresent(String.self, forKey: .createdEntityId)
        dependencyEntities = try container.decodeIfPresent([[String: String]].self, forKey: .dependencyEntities) ?? []

        if let rawPrecondition = try? container.decode([String: AnyCodableValue].self, forKey: .precondition) {
            if let taskId = rawPrecondition["task_id"]?.stringValue {
                targetEntityId = taskId
                targetEntityType = "task"
            } else if let cmtId = rawPrecondition["commitment_id"]?.stringValue {
                targetEntityId = cmtId
                targetEntityType = "commitment"
            } else if let linkedId = rawPrecondition["linked_entity_id"]?.stringValue {
                targetEntityId = linkedId
                targetEntityType = rawPrecondition["linked_entity_type"]?.stringValue ?? "entity"
            } else {
                targetEntityId = try? container.decodeIfPresent(String.self, forKey: .targetEntityId)
                targetEntityType = try? container.decodeIfPresent(String.self, forKey: .targetEntityType)
            }
        } else {
            targetEntityId = try? container.decodeIfPresent(String.self, forKey: .targetEntityId)
            targetEntityType = try? container.decodeIfPresent(String.self, forKey: .targetEntityType)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(operationId, forKey: .operationId)
        try container.encode(operationType, forKey: .operationType)
        try container.encode(executionOrder, forKey: .executionOrder)
        try container.encode(status, forKey: .status)
        try container.encode(requiredPermissions, forKey: .requiredPermissions)
        try container.encodeIfPresent(errorMessage, forKey: .errorMessage)
        try container.encodeIfPresent(createdEntityId, forKey: .createdEntityId)
        try container.encode(dependencyEntities, forKey: .dependencyEntities)
        try container.encodeIfPresent(targetEntityId, forKey: .targetEntityId)
        try container.encodeIfPresent(targetEntityType, forKey: .targetEntityType)
    }
}

// MARK: - Client Proposal Payloads & Server Discriminator

public struct WeeklyPlanProposalPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }
    public let proposalId: String
    public let userId: String
    public let proposalType: WeeklyProposalType
    public let status: WeeklyProposalStatus
    public let createdAt: String
    public let expiresAt: String
    public let stateHash: String
    public let requiredPermissions: [String]
    public let operations: [WeeklyOperationPayload]
    public let omittedDomains: [[String: String]]
    public let metadata: [String: AnyCodableValue]
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case userId = "user_id"
        case proposalType = "proposal_type"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case stateHash = "state_hash"
        case requiredPermissions = "required_permissions"
        case operations
        case omittedDomains = "omitted_domains"
        case metadata
    }

    public init(
        proposalId: String,
        userId: String,
        proposalType: WeeklyProposalType = .weeklyPlan,
        status: WeeklyProposalStatus = .draft,
        createdAt: String,
        expiresAt: String,
        stateHash: String,
        requiredPermissions: [String] = [],
        operations: [WeeklyOperationPayload] = [],
        omittedDomains: [[String: String]] = [],
        metadata: [String: AnyCodableValue] = [:],
        failureReason: String? = nil
    ) {
        self.proposalId = proposalId
        self.userId = userId
        self.proposalType = proposalType
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.stateHash = stateHash
        self.requiredPermissions = requiredPermissions
        self.operations = operations
        self.omittedDomains = omittedDomains
        self.metadata = metadata
        self.failureReason = failureReason ?? operations.first(where: { $0.status == .failed })?.errorMessage
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decode(String.self, forKey: .proposalId)
        userId = try container.decode(String.self, forKey: .userId)
        proposalType = try container.decode(WeeklyProposalType.self, forKey: .proposalType)
        status = try container.decode(WeeklyProposalStatus.self, forKey: .status)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        expiresAt = try container.decode(String.self, forKey: .expiresAt)
        stateHash = try container.decode(String.self, forKey: .stateHash)
        requiredPermissions = try container.decodeIfPresent([String].self, forKey: .requiredPermissions) ?? []
        operations = try container.decodeIfPresent([WeeklyOperationPayload].self, forKey: .operations) ?? []
        omittedDomains = try container.decodeIfPresent([[String: String]].self, forKey: .omittedDomains) ?? []
        metadata = try container.decodeIfPresent([String: AnyCodableValue].self, forKey: .metadata) ?? [:]
        failureReason = operations.first(where: { $0.status == .failed })?.errorMessage
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(proposalId, forKey: .proposalId)
        try container.encode(userId, forKey: .userId)
        try container.encode(proposalType, forKey: .proposalType)
        try container.encode(status, forKey: .status)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(expiresAt, forKey: .expiresAt)
        try container.encode(stateHash, forKey: .stateHash)
        try container.encode(requiredPermissions, forKey: .requiredPermissions)
        try container.encode(operations, forKey: .operations)
        try container.encode(omittedDomains, forKey: .omittedDomains)
        try container.encode(metadata, forKey: .metadata)
    }
}

public struct WeeklyRetrospectiveProposalPayload: Codable, Equatable, Sendable, Identifiable {
    public var id: String { proposalId }
    public let proposalId: String
    public let userId: String
    public let proposalType: WeeklyProposalType
    public let status: WeeklyProposalStatus
    public let createdAt: String
    public let expiresAt: String
    public let stateHash: String
    public let requiredPermissions: [String]
    public let operations: [WeeklyOperationPayload]
    public let omittedDomains: [[String: String]]
    public let metadata: [String: AnyCodableValue]
    public let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case userId = "user_id"
        case proposalType = "proposal_type"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case stateHash = "state_hash"
        case requiredPermissions = "required_permissions"
        case operations
        case omittedDomains = "omitted_domains"
        case metadata
    }

    public init(
        proposalId: String,
        userId: String,
        proposalType: WeeklyProposalType = .weeklyRetrospective,
        status: WeeklyProposalStatus = .draft,
        createdAt: String,
        expiresAt: String,
        stateHash: String,
        requiredPermissions: [String] = [],
        operations: [WeeklyOperationPayload] = [],
        omittedDomains: [[String: String]] = [],
        metadata: [String: AnyCodableValue] = [:],
        failureReason: String? = nil
    ) {
        self.proposalId = proposalId
        self.userId = userId
        self.proposalType = proposalType
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.stateHash = stateHash
        self.requiredPermissions = requiredPermissions
        self.operations = operations
        self.omittedDomains = omittedDomains
        self.metadata = metadata
        self.failureReason = failureReason ?? operations.first(where: { $0.status == .failed })?.errorMessage
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try container.decode(String.self, forKey: .proposalId)
        userId = try container.decode(String.self, forKey: .userId)
        proposalType = try container.decode(WeeklyProposalType.self, forKey: .proposalType)
        status = try container.decode(WeeklyProposalStatus.self, forKey: .status)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        expiresAt = try container.decode(String.self, forKey: .expiresAt)
        stateHash = try container.decode(String.self, forKey: .stateHash)
        requiredPermissions = try container.decodeIfPresent([String].self, forKey: .requiredPermissions) ?? []
        operations = try container.decodeIfPresent([WeeklyOperationPayload].self, forKey: .operations) ?? []
        omittedDomains = try container.decodeIfPresent([[String: String]].self, forKey: .omittedDomains) ?? []
        metadata = try container.decodeIfPresent([String: AnyCodableValue].self, forKey: .metadata) ?? [:]
        failureReason = operations.first(where: { $0.status == .failed })?.errorMessage
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(proposalId, forKey: .proposalId)
        try container.encode(userId, forKey: .userId)
        try container.encode(proposalType, forKey: .proposalType)
        try container.encode(status, forKey: .status)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(expiresAt, forKey: .expiresAt)
        try container.encode(stateHash, forKey: .stateHash)
        try container.encode(requiredPermissions, forKey: .requiredPermissions)
        try container.encode(operations, forKey: .operations)
        try container.encode(omittedDomains, forKey: .omittedDomains)
        try container.encode(metadata, forKey: .metadata)
    }
}

// MARK: - Discriminated Proposal Envelope

public enum WeeklyProposalPayload: Codable, Equatable, Sendable {
    case plan(WeeklyPlanProposalPayload)
    case retrospective(WeeklyRetrospectiveProposalPayload)

    private enum DiscriminatorKeys: String, CodingKey {
        case proposalType = "proposal_type"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DiscriminatorKeys.self)
        let typeRaw = try container.decode(String.self, forKey: .proposalType)
        guard let pType = WeeklyProposalType(rawValue: typeRaw) else {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: container.codingPath,
                    debugDescription: "Unknown proposal_type: '\(typeRaw)'. Must fail closed."
                )
            )
        }
        switch pType {
        case .weeklyPlan:
            let plan = try WeeklyPlanProposalPayload(from: decoder)
            self = .plan(plan)
        case .weeklyRetrospective:
            let retro = try WeeklyRetrospectiveProposalPayload(from: decoder)
            self = .retrospective(retro)
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .plan(let p): try p.encode(to: encoder)
        case .retrospective(let r): try r.encode(to: encoder)
        }
    }

    public var proposalId: String {
        switch self {
        case .plan(let p): return p.proposalId
        case .retrospective(let r): return r.proposalId
        }
    }

    public var proposalType: WeeklyProposalType {
        switch self {
        case .plan(let p): return p.proposalType
        case .retrospective(let r): return r.proposalType
        }
    }

    public var status: WeeklyProposalStatus {
        switch self {
        case .plan(let p): return p.status
        case .retrospective(let r): return r.status
        }
    }
}

// MARK: - Active Mutation Context

public struct ActiveMutationContext: Equatable, Sendable {
    public let proposalId: String
    public let proposalType: WeeklyProposalType
    public let operation: WeeklyApplyOperation
    public let approved: Bool
    public let startedAt: Date

    public init(
        proposalId: String,
        proposalType: WeeklyProposalType,
        operation: WeeklyApplyOperation,
        approved: Bool,
        startedAt: Date = Date()
    ) {
        self.proposalId = proposalId
        self.proposalType = proposalType
        self.operation = operation
        self.approved = approved
        self.startedAt = startedAt
    }
}

// MARK: - Presentation State Machine

public enum WeeklyHorizonSheetState: Equatable, Sendable {
    case idle
    case loadingSynthesis
    case synthesisLoaded
    case generatingProposal
    case proposalReady
    case applying(proposalId: String)
    case aborting(proposalId: String)
    case executedPresentation
    case partiallyExecutedPresentation
    case proposalFailedPresentation(failureReason: String?, operations: [WeeklyOperationPayload])
    case abortedPresentation
    case expiredPresentation
    case failedPresentation(message: String)
    case contractViolationPresentation(WeeklyHorizonContractViolation)
    case serverExecutionInProgress(proposalId: String)
    case mutationOutcomeUnknown(proposalId: String)
}
