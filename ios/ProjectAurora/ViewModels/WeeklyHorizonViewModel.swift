import Foundation
import SwiftUI

@MainActor
public final class WeeklyHorizonViewModel: ObservableObject {
    @Published public private(set) var horizon: WeeklyStrategicHorizon?
    @Published public private(set) var retrospective: WeeklyRetrospective?
    @Published public private(set) var activePlanProposal: WeeklyPlanProposalPayload?
    @Published public private(set) var activeRetrospectiveProposal: WeeklyRetrospectiveProposalPayload?
    @Published public var sheetState: WeeklyHorizonSheetState = .idle
    @Published public private(set) var isLoading: Bool = false

    // Active in-flight mutation context (cleared upon definitive response)
    public private(set) var activeMutationContext: ActiveMutationContext?

    // Retained context for mutationOutcomeUnknown recovery integrity verification
    public private(set) var retainedRecoveryContext: ActiveMutationContext?

    // Retained contract violation details for deterministic logging/presentation
    @Published public private(set) var lastContractViolation: WeeklyHorizonContractViolation?

    // Authoritative Recovery Synchronization & Ownership
    public private(set) var isRecoveryInFlight: Bool = false
    public private(set) var recoveryGeneration: UInt64 = 0
    public private(set) var activeRecoveryTask: Task<Void, Never>?

    private let apiClient: AuroraAPIClient
    private var activeMutationTask: Task<Void, Never>?

    public init(apiClient: AuroraAPIClient? = nil) {
        self.apiClient = apiClient ?? AuroraAPIClient()
    }

    // MARK: - Synthesis Loaders

    public func loadWeeklyHorizon(startDate: Date? = nil) async {
        isLoading = true
        if case .idle = sheetState {
            sheetState = .loadingSynthesis
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current

        let dateStr = startDate.map { formatter.string(from: $0) }
        let tzStr = TimeZone.current.identifier

        do {
            let result = try await apiClient.getWeeklyStrategicHorizon(
                startDate: dateStr,
                timezone: tzStr
            )
            self.horizon = result
            self.sheetState = .synthesisLoaded
            self.isLoading = false
        } catch {
            self.isLoading = false
            self.handleTransportFailure(error, for: .horizonSynthesis)
        }
    }

    public func loadWeeklyRetrospective(endDate: Date? = nil) async {
        isLoading = true
        if case .idle = sheetState {
            sheetState = .loadingSynthesis
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current

        let dateStr = endDate.map { formatter.string(from: $0) }
        let tzStr = TimeZone.current.identifier

        do {
            let result = try await apiClient.getWeeklyRetrospective(
                endDate: dateStr,
                timezone: tzStr
            )
            self.retrospective = result
            self.sheetState = .synthesisLoaded
            self.isLoading = false
        } catch {
            self.isLoading = false
            self.handleTransportFailure(error, for: .retrospectiveSynthesis)
        }
    }

    // MARK: - Weekly Plan Proposal Generation

    public func createWeeklyPlanProposal(
        taskAllocations: [WeeklyTaskAllocationPayload]? = nil,
        milestoneReminders: [WeeklyMilestoneReminderPayload]? = nil,
        strategicIntentNotes: String? = nil
    ) async {
        guard activeMutationContext == nil else { return }

        sheetState = .generatingProposal
        isLoading = true

        let payload = ProposeWeeklyPlanRequestPayload(
            taskAllocations: taskAllocations,
            milestoneReminders: milestoneReminders,
            strategicIntentNotes: strategicIntentNotes,
            explicitTz: TimeZone.current.identifier
        )

        do {
            let result = try await apiClient.proposeWeeklyPlan(payload: payload)
            self.activePlanProposal = result
            self.sheetState = .proposalReady
            self.isLoading = false
        } catch {
            self.isLoading = false
            self.handleTransportFailure(error, for: .proposePlan)
        }
    }

    // MARK: - Governed Weekly Plan Apply / Discard

    @discardableResult
    public func applyActiveWeeklyPlan(approved: Bool) -> Task<Void, Never>? {
        guard activeMutationContext == nil else { return nil }
        guard let proposal = activePlanProposal else { return nil }

        let proposalId = proposal.proposalId
        if approved {
            sheetState = .applying(proposalId: proposalId)
        } else {
            sheetState = .aborting(proposalId: proposalId)
        }

        activeMutationContext = ActiveMutationContext(
            proposalId: proposalId,
            proposalType: .weeklyPlan,
            operation: .applyPlan,
            approved: approved
        )

        let task = Task { @MainActor [weak self] in
            guard let self = self else { return }
            do {
                let result = try await self.apiClient.applyWeeklyPlan(proposalId: proposalId, approved: approved)
                self.activeMutationContext = nil
                self.retainedRecoveryContext = nil
                self.activePlanProposal = result
                self.mapServerStatus(result.status, failureReason: result.failureReason, operations: result.operations)
            } catch {
                self.handleTransportFailure(error, for: .applyPlan)
            }
        }

        self.activeMutationTask = task
        return task
    }

    // MARK: - Weekly Retrospective Proposal Generation

    public func createWeeklyRetrospectiveProposal(
        commitmentIds: [String]? = nil,
        retrospectiveNotes: String? = nil
    ) async {
        guard activeMutationContext == nil else { return }

        sheetState = .generatingProposal
        isLoading = true

        let payload = ProposeWeeklyRetrospectiveRequestPayload(
            commitmentIds: commitmentIds,
            retrospectiveNotes: retrospectiveNotes,
            explicitTz: TimeZone.current.identifier
        )

        do {
            let result = try await apiClient.proposeWeeklyRetrospective(payload: payload)
            self.activeRetrospectiveProposal = result
            self.sheetState = .proposalReady
            self.isLoading = false
        } catch {
            self.isLoading = false
            self.handleTransportFailure(error, for: .proposeRetrospective)
        }
    }

    // MARK: - Governed Weekly Retrospective Apply / Discard

    @discardableResult
    public func applyActiveWeeklyRetrospective(approved: Bool) -> Task<Void, Never>? {
        guard activeMutationContext == nil else { return nil }
        guard let proposal = activeRetrospectiveProposal else { return nil }

        let proposalId = proposal.proposalId
        if approved {
            sheetState = .applying(proposalId: proposalId)
        } else {
            sheetState = .aborting(proposalId: proposalId)
        }

        activeMutationContext = ActiveMutationContext(
            proposalId: proposalId,
            proposalType: .weeklyRetrospective,
            operation: .applyRetrospective,
            approved: approved
        )

        let task = Task { @MainActor [weak self] in
            guard let self = self else { return }
            do {
                let result = try await self.apiClient.applyWeeklyRetrospective(proposalId: proposalId, approved: approved)
                self.activeMutationContext = nil
                self.retainedRecoveryContext = nil
                self.activeRetrospectiveProposal = result
                self.mapServerStatus(result.status, failureReason: result.failureReason, operations: result.operations)
            } catch {
                self.handleTransportFailure(error, for: .applyRetrospective)
            }
        }

        self.activeMutationTask = task
        return task
    }

    // MARK: - Explicit Task-Owned Serialized Recovery

    @discardableResult
    public func resolveUnknownOutcome(proposalId: String) -> Task<Void, Never>? {
        // 1. Serialization Gate: Reject concurrent recovery/abort attempts locally
        guard !isRecoveryInFlight else { return nil }

        guard let context = retainedRecoveryContext, context.proposalId == proposalId else {
            self.sheetState = .failedPresentation(message: "Missing or mismatched recovery context.")
            return nil
        }

        // 2. Lock Gate & Increment Generation Token
        isRecoveryInFlight = true
        recoveryGeneration &+= 1
        let currentGen = recoveryGeneration

        // 3. Create actual in-flight recovery Task and assign to activeRecoveryTask
        let task = Task { @MainActor [weak self] in
            guard let self = self else { return }
            defer {
                if self.recoveryGeneration == currentGen {
                    self.isRecoveryInFlight = false
                    self.activeRecoveryTask = nil
                }
            }

            // Cooperative cancellation check before dispatch
            if Task.isCancelled { return }

            do {
                let payload = try await self.apiClient.getWeeklyProposal(proposalId: proposalId)

                // 4. Stale-Response Protection & Cooperative Cancellation check
                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }

                // 5. Strict Client Integrity Verification
                guard payload.proposalId == context.proposalId else {
                    let violation = WeeklyHorizonContractViolation.proposalIdentityMismatch(
                        expectedId: context.proposalId,
                        actualId: payload.proposalId
                    )
                    self.lastContractViolation = violation
                    self.retainedRecoveryContext = nil
                    self.sheetState = .contractViolationPresentation(violation)
                    return
                }

                guard payload.proposalType == context.proposalType else {
                    let violation = WeeklyHorizonContractViolation.proposalTypeMismatch(
                        expectedType: context.proposalType,
                        actualType: payload.proposalType
                    )
                    self.lastContractViolation = violation
                    self.retainedRecoveryContext = nil
                    self.sheetState = .contractViolationPresentation(violation)
                    return
                }

                // 6. Handle Server Status = executing (Option B)
                if payload.status == .executing {
                    self.sheetState = .serverExecutionInProgress(proposalId: payload.proposalId)
                    return
                }

                // 7. Terminal Outcome Handled: Clear context
                self.retainedRecoveryContext = nil
                self.lastContractViolation = nil

                switch payload {
                case .plan(let plan):
                    self.activePlanProposal = plan
                    self.mapServerStatus(plan.status, failureReason: plan.failureReason, operations: plan.operations)
                case .retrospective(let retro):
                    self.activeRetrospectiveProposal = retro
                    self.mapServerStatus(retro.status, failureReason: retro.failureReason, operations: retro.operations)
                }
            } catch {
                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }
                self.sheetState = .failedPresentation(message: "Unable to retrieve status: \(error.localizedDescription). Please tap Check Status again.")
            }
        }

        self.activeRecoveryTask = task
        return task
    }

    @discardableResult
    public func abortUnknownProposal(proposalId: String) -> Task<Void, Never>? {
        guard !isRecoveryInFlight else { return nil }

        guard let context = retainedRecoveryContext, context.proposalId == proposalId else {
            self.sheetState = .failedPresentation(message: "Missing or mismatched recovery context.")
            return nil
        }

        isRecoveryInFlight = true
        recoveryGeneration &+= 1
        let currentGen = recoveryGeneration

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
                let payload = try await self.apiClient.abortWeeklyProposal(proposalId: proposalId)

                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }

                guard payload.proposalId == context.proposalId, payload.proposalType == context.proposalType else {
                    let violation = WeeklyHorizonContractViolation.proposalIntegrityMismatch(
                        expectedId: context.proposalId,
                        expectedType: context.proposalType,
                        actualId: payload.proposalId,
                        actualType: payload.proposalType
                    )
                    self.lastContractViolation = violation
                    self.retainedRecoveryContext = nil
                    self.sheetState = .contractViolationPresentation(violation)
                    return
                }

                self.retainedRecoveryContext = nil
                self.lastContractViolation = nil
                self.sheetState = .abortedPresentation
            } catch {
                guard !Task.isCancelled,
                      currentGen == self.recoveryGeneration,
                      self.retainedRecoveryContext != nil else {
                    return
                }
                self.sheetState = .failedPresentation(message: "Unable to abort proposal: \(error.localizedDescription). Please try again.")
            }
        }

        self.activeRecoveryTask = task
        return task
    }

    // MARK: - State Reset & Cooperative Cancellation

    public func startFresh() {
        activeRecoveryTask?.cancel()
        activeRecoveryTask = nil
        recoveryGeneration &+= 1
        isRecoveryInFlight = false
        activeMutationContext = nil
        retainedRecoveryContext = nil
        lastContractViolation = nil
        sheetState = .idle
    }

    // MARK: - Internal Helper & State Mapping

    private func mapServerStatus(
        _ status: WeeklyProposalStatus,
        failureReason: String?,
        operations: [WeeklyOperationPayload]
    ) {
        switch status {
        case .executed:
            sheetState = .executedPresentation
        case .partiallyExecuted:
            sheetState = .partiallyExecutedPresentation
        case .failed:
            sheetState = .proposalFailedPresentation(failureReason: failureReason, operations: operations)
        case .aborted:
            sheetState = .abortedPresentation
        case .expired:
            sheetState = .expiredPresentation
        case .executing:
            sheetState = .idle
        case .draft, .approved:
            sheetState = .proposalReady
        }
    }

    private func handleTransportFailure(_ error: Error, for requestKind: WeeklyRequestKind) {
        switch requestKind {
        case .applyPlan, .applyRetrospective:
            if let context = self.activeMutationContext {
                self.retainedRecoveryContext = context
                self.activeMutationContext = nil
                self.sheetState = .mutationOutcomeUnknown(proposalId: context.proposalId)
            } else {
                self.sheetState = .failedPresentation(message: error.localizedDescription)
            }
        case .horizonSynthesis, .retrospectiveSynthesis, .proposePlan, .proposeRetrospective:
            self.sheetState = .failedPresentation(message: error.localizedDescription)
        case .recoverProposal:
            self.isRecoveryInFlight = false
            self.activeRecoveryTask = nil
            self.sheetState = .failedPresentation(message: "Unable to retrieve proposal status: \(error.localizedDescription). Please tap Check Status again.")
        case .abortProposal:
            self.isRecoveryInFlight = false
            self.activeRecoveryTask = nil
            self.sheetState = .failedPresentation(message: "Unable to abort proposal: \(error.localizedDescription). Please try again.")
        }
    }

    // MARK: - Test Support

    #if DEBUG
    public func setTestPlanProposal(_ proposal: WeeklyPlanProposalPayload?) {
        self.activePlanProposal = proposal
    }

    public func setTestRetrospectiveProposal(_ proposal: WeeklyRetrospectiveProposalPayload?) {
        self.activeRetrospectiveProposal = proposal
    }

    public func setTestRecoveryContext(_ context: ActiveMutationContext?) {
        self.retainedRecoveryContext = context
    }
    #endif
}

