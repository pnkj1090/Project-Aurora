import Foundation
import SwiftUI

// MARK: - View States

public enum EventBriefingViewState: Equatable, Sendable {
    case idle
    case loadingBriefing
    case briefingLoaded(EventBriefing)
    case briefingFailed(String)
}

public enum DebriefViewState: Equatable, Sendable {
    case idle
    case draftingNotes
    case proposing
    case proposalReview(DebriefProposal)
    case applying
    case terminalCompleted(DebriefProposal)
    case terminalPartial(DebriefProposal)
    case terminalFailed(DebriefProposal)
    case terminalAborted(DebriefProposal)
    case terminalExpired(DebriefProposal)
    case mutationOutcomeUnknown(proposalId: String, reason: String)
    case genericError(String)
}

// MARK: - View Model

@MainActor
public final class EventBriefingViewModel: ObservableObject {
    @Published public var briefingState: EventBriefingViewState = .idle
    @Published public var debriefState: DebriefViewState = .idle
    @Published public private(set) var briefing: EventBriefing?
    @Published public private(set) var proposal: DebriefProposal?

    // Retained context for mutationOutcomeUnknown recovery integrity verification (proposalId)
    public private(set) var retainedRecoveryContext: String?

    // Authoritative Recovery Synchronization & Ownership
    public private(set) var isRecoveryInFlight: Bool = false
    public private(set) var recoveryGeneration: UInt64 = 0
    public private(set) var activeMutationTask: Task<Void, Never>?
    public private(set) var activeRecoveryTask: Task<Void, Never>?

    private let apiClient: AuroraAPIClient

    public init(apiClient: AuroraAPIClient? = nil) {
        self.apiClient = apiClient ?? AuroraAPIClient()
    }

    // MARK: - Event Briefing

    public func loadBriefing(eventId: String) async {
        briefingState = .loadingBriefing
        do {
            let result = try await apiClient.getEventBriefing(eventId: eventId)
            self.briefing = result
            self.briefingState = .briefingLoaded(result)
        } catch {
            self.briefingState = .briefingFailed(error.localizedDescription)
        }
    }

    // MARK: - Debrief Proposing

    public func startDraftingNotes() {
        debriefState = .draftingNotes
    }

    public func proposeDebrief(eventId: String, payload: ProposeDebriefRequestPayload) async {
        debriefState = .proposing
        do {
            let result = try await apiClient.proposeEventDebrief(eventId: eventId, payload: payload)
            self.proposal = result
            self.debriefState = .proposalReview(result)
        } catch {
            self.debriefState = .genericError(error.localizedDescription)
        }
    }

    // MARK: - Governed Proposal Apply

    @discardableResult
    public func applyProposal(proposalId: String, approved: Bool) -> Task<Void, Never>? {
        guard case .applying = debriefState else {
            debriefState = .applying
            recoveryGeneration &+= 1
            let currentGen = recoveryGeneration
            let targetProposalId = proposalId

            let task = Task { @MainActor [weak self] in
                guard let self = self else { return }
                defer {
                    if self.recoveryGeneration == currentGen {
                        self.activeMutationTask = nil
                    }
                }

                if Task.isCancelled { return }

                do {
                    let result = try await self.apiClient.applyDebriefProposal(
                        proposalId: targetProposalId,
                        approved: approved
                    )

                    guard !Task.isCancelled,
                          self.recoveryGeneration == currentGen else {
                        return
                    }

                    self.proposal = result
                    self.retainedRecoveryContext = nil
                    self.mapServerStatus(result)
                } catch let error as APIError {
                    guard !Task.isCancelled,
                          self.recoveryGeneration == currentGen else {
                        return
                    }

                    switch error {
                    case .networkError(let details):
                        self.retainedRecoveryContext = targetProposalId
                        self.debriefState = .mutationOutcomeUnknown(
                            proposalId: targetProposalId,
                            reason: "Network transport failed: \(details). The server execution state is unknown."
                        )
                    case .conflict(let code, let message):
                        self.debriefState = .genericError("Conflict (\(code)): \(message)")
                    case .preconditionFailed(let code, let message):
                        self.debriefState = .genericError("Precondition Failed (\(code)): \(message)")
                    case .serverError(let statusCode, let message):
                        self.debriefState = .genericError("Server Error (\(statusCode)): \(message)")
                    default:
                        self.debriefState = .genericError(error.localizedDescription)
                    }
                } catch {
                    guard !Task.isCancelled,
                          self.recoveryGeneration == currentGen else {
                        return
                    }

                    self.retainedRecoveryContext = targetProposalId
                    self.debriefState = .mutationOutcomeUnknown(
                        proposalId: targetProposalId,
                        reason: "Transport error: \(error.localizedDescription). The server execution state is unknown."
                    )
                }
            }

            self.activeMutationTask = task
            return task
        }
        return nil
    }

    // MARK: - Proposal Abort

    @discardableResult
    public func abortProposal(proposalId: String) -> Task<Void, Never>? {
        recoveryGeneration &+= 1
        let currentGen = recoveryGeneration
        let targetProposalId = proposalId

        let task = Task { @MainActor [weak self] in
            guard let self = self else { return }
            defer {
                if self.recoveryGeneration == currentGen {
                    self.activeMutationTask = nil
                }
            }

            if Task.isCancelled { return }

            do {
                let result = try await self.apiClient.abortDebriefProposal(proposalId: targetProposalId)
                guard !Task.isCancelled, self.recoveryGeneration == currentGen else { return }
                self.proposal = result
                self.retainedRecoveryContext = nil
                self.mapServerStatus(result)
            } catch {
                guard !Task.isCancelled, self.recoveryGeneration == currentGen else { return }
                self.debriefState = .genericError("Failed to abort proposal: \(error.localizedDescription)")
            }
        }

        self.activeMutationTask = task
        return task
    }

    // MARK: - Authoritative Recovery

    @discardableResult
    public func resolveUnknownOutcome(proposalId: String) -> Task<Void, Never>? {
        guard !isRecoveryInFlight else { return nil }

        guard let context = retainedRecoveryContext, context == proposalId else {
            self.debriefState = .genericError("Missing or mismatched recovery context for proposal \(proposalId).")
            return nil
        }

        isRecoveryInFlight = true
        recoveryGeneration &+= 1
        let currentGen = recoveryGeneration
        let targetProposalId = proposalId

        let task = Task { @MainActor [weak self] in
            guard let self = self else { return }
            defer {
                if self.recoveryGeneration == currentGen {
                    self.isRecoveryInFlight = false
                    self.activeRecoveryTask = nil
                }
            }

            if Task.isCancelled { return }

            do {
                let result = try await self.apiClient.getDebriefProposal(proposalId: targetProposalId)

                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }

                guard result.proposalId == targetProposalId else {
                    self.debriefState = .genericError("Proposal ID mismatch during recovery.")
                    return
                }

                if result.status == .executing {
                    self.debriefState = .applying
                    return
                }

                self.retainedRecoveryContext = nil
                self.proposal = result
                self.mapServerStatus(result)
            } catch {
                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }
                self.debriefState = .genericError("Unable to retrieve proposal status: \(error.localizedDescription). Please retry.")
            }
        }

        self.activeRecoveryTask = task
        return task
    }

    // MARK: - State Reset & Cooperative Cancellation

    public func startFresh() {
        activeMutationTask?.cancel()
        activeMutationTask = nil
        activeRecoveryTask?.cancel()
        activeRecoveryTask = nil
        recoveryGeneration &+= 1
        isRecoveryInFlight = false
        retainedRecoveryContext = nil
        briefingState = .idle
        debriefState = .idle
        proposal = nil
    }

    // MARK: - Status Mapping

    private func mapServerStatus(_ proposal: DebriefProposal) {
        switch proposal.status {
        case .completed:
            debriefState = .terminalCompleted(proposal)
        case .partiallyExecuted:
            debriefState = .terminalPartial(proposal)
        case .failed:
            debriefState = .terminalFailed(proposal)
        case .aborted:
            debriefState = .terminalAborted(proposal)
        case .expired:
            debriefState = .terminalExpired(proposal)
        case .executing:
            debriefState = .applying
        case .draft, .approved:
            debriefState = .proposalReview(proposal)
        }
    }

    // MARK: - Debug Support

    #if DEBUG
    public func setTestProposal(_ proposal: DebriefProposal?) {
        self.proposal = proposal
    }

    public func setTestRecoveryContext(_ context: String?) {
        self.retainedRecoveryContext = context
    }

    public func setTestBriefingState(_ state: EventBriefingViewState) {
        self.briefingState = state
    }

    public func setTestDebriefState(_ state: DebriefViewState) {
        self.debriefState = state
    }

    public func setTestRecoveryGeneration(_ gen: UInt64) {
        self.recoveryGeneration = gen
    }
    #endif
}
