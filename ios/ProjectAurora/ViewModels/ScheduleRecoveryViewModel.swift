//
//  ScheduleRecoveryViewModel.swift
//  ProjectAurora
//
//  Phase 6P: Schedule Recovery Proposal Lifecycle Hardening & Transport Uncertainty Orchestration.
//

import Foundation
import SwiftUI

public enum ScheduleRecoverySheetState: Equatable, Sendable {
    case idle
    case readyToApply(ScheduleRecoveryProposal)
    case applying(proposalId: String)
    case cancelling(proposalId: String)
    case lockConflict(message: String)
    case mutationOutcomeUnknown(proposalId: String, reason: String)
    case recovering(proposalId: String)
    case resolved(ScheduleRecoveryResolvedState)
    case errorPresentation(message: String)

    public var isTerminal: Bool {
        switch self {
        case .resolved(let resolvedState):
            return resolvedState.isTerminal
        default:
            return false
        }
    }
}

@MainActor
public final class ScheduleRecoveryViewModel: ObservableObject {
    @Published public private(set) var sheetState: ScheduleRecoverySheetState = .idle
    @Published public private(set) var activeProposal: ScheduleRecoveryProposal? = nil
    @Published public private(set) var isLoading: Bool = false
    @Published public private(set) var errorMessage: String? = nil

    // Authoritative Lifecycle Tasks & Generation Fencing (Phase 6N Parity)
    public private(set) var activeMutationTask: Task<Void, Never>? = nil
    public private(set) var activeRecoveryTask: Task<Void, Never>? = nil
    public private(set) var recoveryGeneration: UInt64 = 0

    private let apiClient: AuroraAPIClient

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient
    }

    // MARK: - Proposal Binding

    public func setProposal(_ proposal: ScheduleRecoveryProposal) {
        guard !sheetState.isTerminal else { return }
        self.activeProposal = proposal
        if case .idle = sheetState {
            self.sheetState = .readyToApply(proposal)
        }
    }

    // MARK: - Lifecycle Management

    /// Explicit local reset. Cancels local tasks and increments recoveryGeneration to fence stale completions.
    public func startFresh() {
        activeMutationTask?.cancel()
        activeMutationTask = nil
        activeRecoveryTask?.cancel()
        activeRecoveryTask = nil
        recoveryGeneration &+= 1
        activeProposal = nil
        sheetState = .idle
        isLoading = false
        errorMessage = nil
    }

    // MARK: - Proposal Application

    /// Apply proposal. Never allows re-applying a known terminal state.
    @discardableResult
    public func applyProposal(proposalId: String, confirmed: Bool = true) -> Task<Void, Never>? {
        guard !sheetState.isTerminal else {
            return nil
        }
        guard activeMutationTask == nil else {
            return activeMutationTask
        }

        let currentGen = recoveryGeneration
        sheetState = .applying(proposalId: proposalId)
        isLoading = true
        errorMessage = nil

        let task = Task {
            do {
                let result = try await apiClient.applyScheduleRecoveryProposal(
                    proposalId: proposalId,
                    payload: ApplyRecoveryProposalPayload(confirmed: confirmed)
                )
                guard self.recoveryGeneration == currentGen else { return }
                self.sheetState = .resolved(.applied(result))
                self.isLoading = false
                self.activeMutationTask = nil
            } catch let error as APIError {
                guard self.recoveryGeneration == currentGen else { return }
                self.isLoading = false
                self.activeMutationTask = nil

                switch error {
                case .scheduleRecoveryConflict(let detail):
                    if detail.error == "PARTIAL_EXECUTION_FAILURE" {
                        if detail.completedOperations.isEmpty {
                            self.sheetState = .resolved(.failed(detail))
                        } else {
                            self.sheetState = .resolved(.partiallyExecuted(detail))
                        }
                    } else if detail.error == "PROPOSAL_EXPIRED" {
                        self.sheetState = .resolved(.expired)
                    } else if detail.error == "ACTIVE_EXECUTION_LOCK_CONFLICT" {
                        self.sheetState = .lockConflict(message: detail.message)
                    } else if detail.error == "PROPOSAL_ALREADY_TERMINAL" {
                        self.sheetState = .mutationOutcomeUnknown(
                            proposalId: proposalId,
                            reason: "Proposal is already in a terminal state on the server."
                        )
                    } else {
                        self.sheetState = .mutationOutcomeUnknown(
                            proposalId: proposalId,
                            reason: detail.message
                        )
                    }

                case .preconditionFailed(let code, let message):
                    self.sheetState = .resolved(.preconditionFailed(reason: "\(code): \(message)"))

                case .conflict(_, let message):
                    self.sheetState = .mutationOutcomeUnknown(
                        proposalId: proposalId,
                        reason: "Conflict: \(message)"
                    )

                case .serverError(let code, let message):
                    // HTTP 500 / 507: Mutations may have committed prior to error (Option B)
                    self.sheetState = .mutationOutcomeUnknown(
                        proposalId: proposalId,
                        reason: "Server error (\(code)): \(message)"
                    )

                case .networkError(let message):
                    self.sheetState = .mutationOutcomeUnknown(
                        proposalId: proposalId,
                        reason: "Network error: \(message)"
                    )

                default:
                    self.sheetState = .mutationOutcomeUnknown(
                        proposalId: proposalId,
                        reason: error.localizedDescription
                    )
                }
            } catch {
                guard self.recoveryGeneration == currentGen else { return }
                self.isLoading = false
                self.activeMutationTask = nil
                self.sheetState = .mutationOutcomeUnknown(
                    proposalId: proposalId,
                    reason: error.localizedDescription
                )
            }
        }

        self.activeMutationTask = task
        return task
    }

    // MARK: - Authoritative Recovery

    /// Authoritative server reconciliation. Never blindly reapplies.
    @discardableResult
    public func recoverProposal(proposalId: String) -> Task<Void, Never>? {
        guard activeRecoveryTask == nil else {
            return activeRecoveryTask
        }

        let currentGen = recoveryGeneration
        sheetState = .recovering(proposalId: proposalId)
        isLoading = true
        errorMessage = nil

        let task = Task {
            let proposal: ScheduleRecoveryProposal
            do {
                proposal = try await apiClient.getScheduleRecoveryProposal(proposalId: proposalId)
            } catch {
                guard self.recoveryGeneration == currentGen else { return }
                self.activeRecoveryTask = nil
                self.isLoading = false
                self.sheetState = .mutationOutcomeUnknown(
                    proposalId: proposalId,
                    reason: "Failed to verify server status: \(error.localizedDescription)"
                )
                return
            }

            guard self.recoveryGeneration == currentGen else { return }
            self.activeProposal = proposal

            // Handle non-terminal states
            switch proposal.status {
            case .pendingConfirmation:
                self.sheetState = .resolved(.readyToApply)
                self.activeRecoveryTask = nil
                self.isLoading = false
                return
            case .draft:
                self.sheetState = .resolved(.draft)
                self.activeRecoveryTask = nil
                self.isLoading = false
                return
            case .cancelled:
                self.sheetState = .resolved(.cancelled)
                self.activeRecoveryTask = nil
                self.isLoading = false
                return
            case .expired:
                self.sheetState = .resolved(.expired)
                self.activeRecoveryTask = nil
                self.isLoading = false
                return
            case .applied, .aborted:
                break
            }

            // Terminal status (APPLIED or ABORTED) -> Fetch execution result
            var execResult: RecoveryExecutionResult? = nil
            var isResultNotFound = false
            var resultFetchError: Error? = nil

            do {
                execResult = try await apiClient.getScheduleRecoveryResult(proposalId: proposalId)
            } catch let error as APIError {
                if case .serverError(let code, _) = error, code == 404 {
                    isResultNotFound = true
                } else {
                    resultFetchError = error
                }
            } catch {
                resultFetchError = error
            }

            guard self.recoveryGeneration == currentGen else { return }
            self.activeRecoveryTask = nil
            self.isLoading = false

            if let result = execResult {
                let resolved = ScheduleRecoveryStateResolver.resolve(proposal: proposal, result: result)
                self.sheetState = .resolved(resolved)
            } else if isResultNotFound {
                // Legitimate degraded terminal state (e.g. 507 or unpersisted result)
                if proposal.status == .applied {
                    self.sheetState = .resolved(.appliedWithoutExecutionResult(proposal))
                } else {
                    self.sheetState = .resolved(.abortedWithoutExecutionResult(proposal))
                }
            } else if let fetchErr = resultFetchError {
                // Terminal proposal lifecycle established, but result fetch had network/server failure
                self.sheetState = .resolved(.terminalWithResultFetchError(
                    proposal: proposal,
                    error: fetchErr.localizedDescription
                ))
            }
        }

        self.activeRecoveryTask = task
        return task
    }

    /// Explicit user action to retry fetching execution details when terminal lifecycle is already known.
    @discardableResult
    public func retryFetchExecutionDetails(proposalId: String) -> Task<Void, Never>? {
        return recoverProposal(proposalId: proposalId)
    }

    // MARK: - Server Cancellation

    /// Explicit server-side business cancellation (POST /proposals/{id}/cancel).
    @discardableResult
    public func cancelProposal(proposalId: String, reason: String? = nil) -> Task<Void, Never>? {
        let currentGen = recoveryGeneration
        sheetState = .cancelling(proposalId: proposalId)
        isLoading = true

        let task = Task {
            do {
                let cancelled = try await apiClient.cancelScheduleRecoveryProposal(
                    proposalId: proposalId,
                    reason: reason ?? "User cancelled recovery proposal"
                )
                guard self.recoveryGeneration == currentGen else { return }
                self.activeProposal = cancelled
                self.sheetState = .resolved(.cancelled)
                self.isLoading = false
            } catch {
                guard self.recoveryGeneration == currentGen else { return }
                self.isLoading = false
                self.errorMessage = "Failed to cancel proposal: \(error.localizedDescription)"
                self.sheetState = .errorPresentation(message: self.errorMessage ?? "Cancellation failed")
            }
        }

        return task
    }
}
