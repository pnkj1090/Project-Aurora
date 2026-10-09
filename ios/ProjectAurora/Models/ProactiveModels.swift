import Foundation

// MARK: - Proactive Status

public enum ProactiveStatus: String, Codable, CaseIterable, Sendable {
    case pending = "pending"
    case eligible = "eligible"
    case suppressed = "suppressed"
    case delivered = "delivered"
    case dismissed = "dismissed"
    case expired = "expired"
    case cancelled = "cancelled"
    case accepted = "accepted"
    case executed = "executed"
    case failed = "failed"

    public var displayName: String {
        switch self {
        case .pending: return "Pending"
        case .eligible: return "Eligible"
        case .suppressed: return "Suppressed"
        case .delivered: return "Delivered"
        case .dismissed: return "Dismissed"
        case .expired: return "Expired"
        case .cancelled: return "Cancelled"
        case .accepted: return "Accepted"
        case .executed: return "Executed"
        case .failed: return "Failed"
        }
    }
}

// MARK: - Proactive Trigger

public enum ProactiveTrigger: String, Codable, CaseIterable, Sendable {
    case time = "time"
    case taskDue = "task_due"
    case commitmentDue = "commitment_due"
    case calendarEvent = "calendar_event"
    case locationContext = "location_context"

    public var displayName: String {
        switch self {
        case .time: return "Time"
        case .taskDue: return "Task Due"
        case .commitmentDue: return "Commitment Due"
        case .calendarEvent: return "Calendar Event"
        case .locationContext: return "Location Context"
        }
    }

    public var systemIcon: String {
        switch self {
        case .time: return "clock"
        case .taskDue: return "checklist"
        case .commitmentDue: return "hand.raised"
        case .calendarEvent: return "calendar"
        case .locationContext: return "location"
        }
    }
}

// MARK: - Proactive Action

public enum ProactiveAction: String, Codable, CaseIterable, Sendable {
    case notify = "notify"
    case openReminder = "open_reminder"
    case openTask = "open_task"
    case openCommitment = "open_commitment"
    case openCalendarEvent = "open_calendar_event"
    case completeTask = "complete_task"
    case completeCommitment = "complete_commitment"
    case cancelReminder = "cancel_reminder"

    public var displayName: String {
        switch self {
        case .notify: return "Notify"
        case .openReminder: return "Open Reminder"
        case .openTask: return "Open Task"
        case .openCommitment: return "Open Commitment"
        case .openCalendarEvent: return "Open Calendar"
        case .completeTask: return "Complete Task"
        case .completeCommitment: return "Complete Commitment"
        case .cancelReminder: return "Cancel Reminder"
        }
    }
}

// MARK: - Proactive Priority

public enum ProactivePriority: String, Codable, CaseIterable, Sendable {
    case low = "low"
    case medium = "medium"
    case high = "high"

    public var displayName: String {
        rawValue.capitalized
    }
}

// MARK: - Proactive Candidate Item (Safe Public Model)

/// Safe API representation of a Proactive Proposal.
/// CRITICAL: metadata and reference_id are strictly excluded.
public struct ProactiveCandidateItem: Codable, Identifiable, Equatable, Sendable {
    public let id: String           // maps to candidate_id
    public let trigger: ProactiveTrigger
    public let title: String
    public let message: String
    public let priority: ProactivePriority
    public let status: ProactiveStatus
    public let createdAt: String
    public let expiresAt: String
    public let source: String
    public let action: ProactiveAction

    enum CodingKeys: String, CodingKey {
        case id = "candidate_id"
        case trigger
        case title
        case message
        case priority
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case source
        case action
    }

    public init(
        id: String,
        trigger: ProactiveTrigger,
        title: String,
        message: String,
        priority: ProactivePriority,
        status: ProactiveStatus,
        createdAt: String,
        expiresAt: String,
        source: String,
        action: ProactiveAction
    ) {
        self.id = id
        self.trigger = trigger
        self.title = title
        self.message = message
        self.priority = priority
        self.status = status
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.source = source
        self.action = action
    }
}

// MARK: - API List Response Transport

public struct ProactiveCandidateListResponse: Codable, Sendable {
    public let status: String
    public let count: Int
    public let total: Int
    public let limit: Int
    public let offset: Int
    public let candidates: [ProactiveCandidateItem]
}

// MARK: - Navigation Target

public enum NavigationTargetType: String, Codable, Sendable {
    case task = "task"
    case commitment = "commitment"
    case calendarEvent = "calendar_event"
    case reminder = "reminder"
}

public struct NavigationTarget: Codable, Sendable, Equatable {
    public let type: NavigationTargetType
    public let id: String

    public init(type: NavigationTargetType, id: String) {
        self.type = type
        self.id = id
    }
}

// MARK: - Safe Pending Confirmation

public struct ProactivePendingConfirmationOut: Codable, Sendable, Equatable {
    public let confirmationId: String
    public let toolName: String
    public let riskLevel: String
    public let description: String

    enum CodingKeys: String, CodingKey {
        case confirmationId = "confirmation_id"
        case toolName = "tool_name"
        case riskLevel = "risk_level"
        case description
    }
}

// MARK: - Accept Request & Resolution Response

public struct AcceptProactiveRequest: Codable, Sendable {
    public let conversationId: String?

    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
    }

    public init(conversationId: String? = nil) {
        self.conversationId = conversationId
    }
}

public struct ProactiveResolutionResponse: Codable, Sendable, Equatable {
    public let candidateId: String
    public let status: String
    public let confirmationRequired: Bool
    public let conversationId: String?
    public let executionId: String?
    public let pendingConfirmation: ProactivePendingConfirmationOut?
    public let navigationTarget: NavigationTarget?
    public let message: String?
    public let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case candidateId = "candidate_id"
        case status
        case confirmationRequired = "confirmation_required"
        case conversationId = "conversation_id"
        case executionId = "execution_id"
        case pendingConfirmation = "pending_confirmation"
        case navigationTarget = "navigation_target"
        case message
        case errorMessage = "error_message"
    }
}
