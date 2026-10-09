//
//  ScheduleDisruptionViewModel.swift
//  ProjectAurora
//
//  Created for Phase 6K: iOS Schedule Disruption Integration & Recovery Presentation.
//

import Foundation
import Combine

public enum DisruptionViewState: Equatable {
    case idle
    case loading
    case loaded(ScheduleDisruptionDiagnostic)
    case empty
    case taskDomainUnavailable
    case calendarUnavailable
    case staleCalendar
    case universeSaturated
    case failed(String)
}

@MainActor
public final class ScheduleDisruptionViewModel: ObservableObject {
    @Published public private(set) var state: DisruptionViewState = .idle

    // MARK: - Candidate Selection State (Restricted to Diagnostic Universe)
    @Published public var selectedTaskIds: Set<String> = []
    @Published public var selectedReminderIds: Set<String> = []

    // MARK: - Phase 6I Proposal Transition State
    @Published public private(set) var proposedProposal: ScheduleRecoveryProposal? = nil
    @Published public private(set) var isProposingRecovery: Bool = false
    @Published public private(set) var proposalErrorMessage: String? = nil

    private let apiClient: AuroraAPIClient
    public var targetWindowStart: Date?
    public var targetWindowEnd: Date?
    public var includeReminders: Bool

    public init(
        apiClient: AuroraAPIClient = AuroraAPIClient(),
        targetWindowStart: Date? = nil,
        targetWindowEnd: Date? = nil,
        includeReminders: Bool = true,
        initialDiagnostic: ScheduleDisruptionDiagnostic? = nil
    ) {
        self.apiClient = apiClient
        self.targetWindowStart = targetWindowStart
        self.targetWindowEnd = targetWindowEnd
        self.includeReminders = includeReminders

        if let diagnostic = initialDiagnostic {
            if diagnostic.jeopardyTasks.isEmpty && diagnostic.focusCapacityDeficitMinutes == 0 && diagnostic.severity == .none {
                self.state = .empty
            } else {
                self.state = .loaded(diagnostic)
            }
        } else {
            self.state = .idle
        }
    }

    // MARK: - Selection Security Boundary

    /// Server-recommended IDs from latest diagnostic (Advisory Only)
    public var recommendedTaskIds: Set<String> {
        guard case .loaded(let diagnostic) = state else { return [] }
        return Set(diagnostic.recommendedRecoveryTaskIds)
    }

    /// Allowable Task Selection Universe: strictly tasks in jeopardy_tasks
    public var allowableTaskIds: Set<String> {
        guard case .loaded(let diagnostic) = state else { return [] }
        return Set(diagnostic.jeopardyTasks.map(\.taskId))
    }

    /// Allowable Reminder Selection Universe: strictly reminders in recommendedRecoveryReminderIds
    public var allowableReminderIds: Set<String> {
        guard case .loaded(let diagnostic) = state else { return [] }
        return Set(diagnostic.recommendedRecoveryReminderIds)
    }

    /// Phase 6I Action Gate: Enabled IFF at least one task is selected.
    /// Reminder-only proposals are strictly forbidden per Phase 6I backend contract.
    public var isRecoveryActionEnabled: Bool {
        return !selectedTaskIds.isEmpty
    }

    /// Toggles task selection strictly within allowable universe.
    /// No-op if taskId is not in allowableTaskIds.
    public func toggleTaskSelection(_ taskId: String) {
        guard allowableTaskIds.contains(taskId) else { return }
        if selectedTaskIds.contains(taskId) {
            selectedTaskIds.remove(taskId)
        } else {
            selectedTaskIds.insert(taskId)
        }
    }

    /// Toggles reminder selection strictly within allowable universe.
    /// No-op if reminderId is not in allowableReminderIds.
    public func toggleReminderSelection(_ reminderId: String) {
        guard allowableReminderIds.contains(reminderId) else { return }
        if selectedReminderIds.contains(reminderId) {
            selectedReminderIds.remove(reminderId)
        } else {
            selectedReminderIds.insert(reminderId)
        }
    }

    /// Selects advisory recommended items, filtered strictly by allowable universe.
    public func selectAllRecommended() {
        selectedTaskIds = recommendedTaskIds.intersection(allowableTaskIds)
        if case .loaded(let diagnostic) = state {
            selectedReminderIds = Set(diagnostic.recommendedRecoveryReminderIds).intersection(allowableReminderIds)
        }
    }

    /// Clears all candidate selections.
    public func clearSelection() {
        selectedTaskIds.removeAll()
        selectedReminderIds.removeAll()
    }

    // MARK: - Diagnostic Actions

    /// Diagnoses schedule disruption on explicit user invocation.
    /// Does not auto-trigger on init or view appearance.
    public func diagnose(windowStart: Date? = nil, windowEnd: Date? = nil) async {
        state = .loading
        clearSelection()
        proposedProposal = nil
        proposalErrorMessage = nil

        let start = windowStart ?? targetWindowStart
        let end = windowEnd ?? targetWindowEnd
        let payload = DiagnoseScheduleDisruptionPayload(
            targetWindowStart: start,
            targetWindowEnd: end,
            clientPreferredTimezone: TimeZone.current.identifier,
            includeReminders: includeReminders
        )

        do {
            let diagnostic = try await apiClient.diagnoseScheduleDisruption(payload: payload)
            if diagnostic.jeopardyTasks.isEmpty && diagnostic.focusCapacityDeficitMinutes == 0 && diagnostic.severity == .none {
                state = .empty
            } else {
                state = .loaded(diagnostic)
            }
        } catch {
            mapErrorToState(error)
        }
    }

    /// Explicit user retry action.
    public func retry() async {
        await diagnose()
    }

    /// Explicit user refresh action.
    public func refresh() async {
        await diagnose()
    }

    // MARK: - Error Mapping (Section 16 Table)

    private func mapErrorToState(_ error: Error) {
        if let apiError = error as? APIError {
            switch apiError {
            case .preconditionFailed(let code, let message):
                switch code {
                case "TASK_DOMAIN_UNAVAILABLE":
                    state = .taskDomainUnavailable
                case "NO_CALENDAR_SNAPSHOT", "CALENDAR_SNAPSHOT_SATURATED":
                    state = .calendarUnavailable
                case "STALE_CALENDAR_SNAPSHOT":
                    state = .staleCalendar
                case "TASK_UNIVERSE_SATURATED":
                    state = .universeSaturated
                default:
                    state = .failed("Precondition failed (\(code)): \(message)")
                }
                return
            case .serverError(let code, let message):
                if code == 412 {
                    if message.contains("TASK_DOMAIN_UNAVAILABLE") {
                        state = .taskDomainUnavailable
                    } else if message.contains("NO_CALENDAR_SNAPSHOT") || message.contains("CALENDAR_SNAPSHOT_SATURATED") {
                        state = .calendarUnavailable
                    } else if message.contains("STALE_CALENDAR_SNAPSHOT") {
                        state = .staleCalendar
                    } else if message.contains("TASK_UNIVERSE_SATURATED") {
                        state = .universeSaturated
                    } else {
                        state = .failed(message)
                    }
                    return
                } else if code == 400 || code == 500 {
                    state = .failed(message)
                    return
                }
            case .networkError(let details):
                state = .failed("Network unavailable: \(details)")
                return
            case .decodingError(let details):
                state = .failed("Decoding error: \(details)")
                return
            case .authenticationRequired, .unauthorized:
                state = .failed("Authentication required. Please log in.")
                return
            default:
                break
            }
        }
        state = .failed(error.localizedDescription)
    }

    // MARK: - Phase 6I Recovery Transition

    /// Bridges explicit user-selected candidates into a Phase 6I ScheduleRecoveryProposal.
    /// Diagnostic evaluation window and timezone are passed as CLIENT INITIAL PREFERENCES only.
    /// Phase 6I server independently validates all live state, versions, and working hours.
    public func proposeRecovery() async throws -> ScheduleRecoveryProposal {
        guard case .loaded(let diagnostic) = state else {
            throw APIError.networkError("No diagnostic available to propose recovery")
        }
        guard isRecoveryActionEnabled else {
            throw APIError.networkError("At least one task must be selected for recovery")
        }

        isProposingRecovery = true
        proposalErrorMessage = nil

        let payload = ProposeScheduleRecoveryPayload(
            taskIds: Array(selectedTaskIds),
            reminderIds: selectedReminderIds.isEmpty ? nil : Array(selectedReminderIds),
            targetWindowStart: diagnostic.evaluationWindowStart,
            targetWindowEnd: diagnostic.evaluationWindowEnd,
            userIntent: "Schedule Disruption Recovery",
            clientPreferredTimezone: diagnostic.timezone
        )

        do {
            let proposal = try await apiClient.proposeScheduleRecovery(payload: payload)
            self.proposedProposal = proposal
            self.isProposingRecovery = false
            return proposal
        } catch {
            self.isProposingRecovery = false
            self.proposalErrorMessage = error.localizedDescription
            throw error
        }
    }

    /// Resets proposal transition state.
    public func dismissProposal() {
        proposedProposal = nil
        proposalErrorMessage = nil
    }
}
