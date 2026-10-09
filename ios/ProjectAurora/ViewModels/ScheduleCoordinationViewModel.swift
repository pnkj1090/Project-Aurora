//
//  ScheduleCoordinationViewModel.swift
//  ProjectAurora
//
//  Phase 6Q: Schedule Coordination Lifecycle Hardening & Parent-Owned Orchestration.
//

import Foundation
import SwiftUI

// MARK: - State Machine Enums

public enum RecoveryUncertainReason: Equatable, Sendable {
    case sessionNotFound
    case missingExecutionRecord
    case ambiguousExecutionRecord
    case mismatchedIdentity(expected: String, actual: String)
    case stalePendingConfirmation
    case partialExecution(completedOps: Int, totalOps: Int)
    case terminalStateMismatch
    case malformedResponse(String)

    public var localizedDescription: String {
        switch self {
        case .sessionNotFound:
            return "Conversation session not found on server during recovery (404). Reapply is blocked."
        case .missingExecutionRecord:
            return "No correlated execution record found in conversation history. Reapply is blocked to prevent duplicate mutation."
        case .ambiguousExecutionRecord:
            return "Multiple execution records found for proposal. Reapply is blocked due to ambiguity."
        case .mismatchedIdentity(let expected, let actual):
            return "Execution identity mismatch (expected: \(expected), actual: \(actual)). Reapply is blocked."
        case .stalePendingConfirmation:
            return "Persisted session retained stale pending confirmation. Re-confirmation is prohibited."
        case .partialExecution(let completed, let total):
            return "Partial execution: \(completed) of \(total) operations completed. Please review Tasks to verify state."
        case .terminalStateMismatch:
            return "Conflict on server indicates action is no longer pending confirmation. Reapply is blocked."
        case .malformedResponse(let details):
            return "Malformed execution payload: \(details). Reapply is blocked."
        }
    }
}

public enum ScheduleCoordinationState: Equatable, Sendable {
    case idle
    case analysisLoaded(ResolveConflictsResponsePayload)
    case submittingProposal(strategyId: String)
    case awaitingConfirmation(
        strategy: StrategyOptionPayload,
        proposalId: String,
        confirmationId: String,
        executionId: String,
        conversationId: String,
        stagedOperationIds: [String]
    )
    case executingMutation(
        proposalId: String,
        confirmationId: String,
        executionId: String,
        conversationId: String,
        stagedOperationIds: [String]
    )
    case completed(summary: String)
    case rejected
    case mutationOutcomeUnknown(
        proposalId: String,
        confirmationId: String,
        executionId: String,
        conversationId: String,
        stagedOperationIds: [String],
        error: String
    )
    case recovering(
        proposalId: String,
        confirmationId: String,
        executionId: String,
        conversationId: String,
        stagedOperationIds: [String]
    )
    case recoveryUncertain(
        proposalId: String,
        conversationId: String,
        reason: RecoveryUncertainReason
    )
    case failed(message: String)

    public var isTerminal: Bool {
        switch self {
        case .completed, .rejected, .failed:
            return true
        default:
            return false
        }
    }
}

// MARK: - ViewModel Implementation

@MainActor
public final class ScheduleCoordinationViewModel: ObservableObject {
    @Published public private(set) var state: ScheduleCoordinationState = .idle
    @Published public private(set) var isStaging: Bool = false
    @Published public private(set) var isMutating: Bool = false
    @Published public private(set) var isRecovering: Bool = false
    @Published public private(set) var errorMessage: String? = nil

    // Task ownership & Generation Fencing (Phase 6N/6P Parity)
    private var activeStagingTask: Task<Void, Never>? = nil
    private var activeMutationTask: Task<Void, Never>? = nil
    private var activeRecoveryTask: Task<Void, Never>? = nil
    private var coordinationGeneration: UInt64 = 0

    public var currentGeneration: UInt64 { coordinationGeneration }
    public var hasActiveMutationTask: Bool { activeMutationTask != nil }
    public var hasActiveRecoveryTask: Bool { activeRecoveryTask != nil }
    public var hasActiveStagingTask: Bool { activeStagingTask != nil }

    private let apiClient: AuroraAPIClient

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient
    }

    // MARK: - Lifecycle Management

    /// Explicit local reset. Increments generation to invalidate any in-flight background callbacks.
    /// Strictly prohibited while an executing mutation, unknown mutation, or recovery is active.
    public func startFresh() {
        guard canStartFresh() else { return }

        coordinationGeneration &+= 1
        activeStagingTask?.cancel()
        activeStagingTask = nil

        state = .idle
        isStaging = false
        isMutating = false
        isRecovering = false
        errorMessage = nil
    }

    public func canStartFresh() -> Bool {
        switch state {
        case .idle, .analysisLoaded, .completed, .rejected, .failed:
            return true
        case .submittingProposal, .awaitingConfirmation, .executingMutation,
             .mutationOutcomeUnknown, .recovering, .recoveryUncertain:
            return false
        }
    }

    // MARK: - Conflict Analysis

    public func analyzeConflicts(
        date: String? = nil,
        timezone: String? = nil,
        windowHours: Int = 24,
        defaultDurationMinutes: Int = 30,
        reschedulingRequested: Bool = false
    ) async {
        guard canStartFresh() else { return }

        let capturedGen = coordinationGeneration
        isStaging = true
        errorMessage = nil

        do {
            let response = try await apiClient.resolveScheduleConflicts(
                date: date,
                timezone: timezone,
                windowHours: windowHours,
                defaultDurationMinutes: defaultDurationMinutes,
                reschedulingRequested: reschedulingRequested
            )
            guard capturedGen == self.coordinationGeneration else { return }
            self.state = .analysisLoaded(response)
            self.isStaging = false
        } catch let err as APIError {
            guard capturedGen == self.coordinationGeneration else { return }
            self.isStaging = false
            self.errorMessage = err.localizedDescription
            self.state = .failed(message: err.localizedDescription)
        } catch {
            guard capturedGen == self.coordinationGeneration else { return }
            self.isStaging = false
            self.errorMessage = error.localizedDescription
            self.state = .failed(message: error.localizedDescription)
        }
    }

    // MARK: - Proposal Staging (Non-Mutating Phase)

    public func stageProposal(
        strategy: StrategyOptionPayload,
        conversationId: String? = nil
    ) {
        guard case .analysisLoaded = state else { return }
        guard activeStagingTask == nil else { return }

        let capturedGen = coordinationGeneration
        let stratId = strategy.strategyId
        state = .submittingProposal(strategyId: stratId)
        isStaging = true
        errorMessage = nil

        let stagedOpIds = strategy.previewOperations.map(\.id)

        activeStagingTask = Task { [weak self] in
            guard let self = self else { return }
            defer {
                self.isStaging = false
                self.activeStagingTask = nil
            }

            do {
                // Request agent loop to coordinate and stage proposal
                let messageText = "Coordinate schedule using strategy: \(stratId)"
                let response = try await self.apiClient.sendChatMessage(
                    message: messageText,
                    conversationId: conversationId
                )

                guard capturedGen == self.coordinationGeneration else { return }

                if response.type == "confirmation_required",
                   let confId = response.confirmationId,
                   let execId = response.executionId {

                    let propId = response.tool?.arguments["proposal_id"]?.stringValue ?? "prop_\(stratId)"
                    let convId = response.conversationId

                    self.state = .awaitingConfirmation(
                        strategy: strategy,
                        proposalId: propId,
                        confirmationId: confId,
                        executionId: execId,
                        conversationId: convId,
                        stagedOperationIds: stagedOpIds
                    )
                } else if response.type == "message" {
                    let summary = response.message?.content ?? "Completed successfully"
                    self.state = .completed(summary: summary)
                } else {
                    self.state = .failed(message: "Proposal staging did not pause for confirmation.")
                }
            } catch {
                guard capturedGen == self.coordinationGeneration else { return }
                self.state = .failed(message: error.localizedDescription)
            }
        }
    }

    // MARK: - Mutation Execution (Potentially Mutating Phase)

    public func confirmProposal(approved: Bool) {
        guard case let .awaitingConfirmation(strategy, propId, confId, execId, convId, stagedOpIds) = state else {
            return
        }
        guard activeMutationTask == nil else { return }

        if !approved {
            state = .rejected
            return
        }

        let capturedGen = coordinationGeneration
        state = .executingMutation(
            proposalId: propId,
            confirmationId: confId,
            executionId: execId,
            conversationId: convId,
            stagedOperationIds: stagedOpIds
        )
        isMutating = true
        errorMessage = nil

        activeMutationTask = Task { [weak self] in
            guard let self = self else { return }
            defer {
                self.isMutating = false
                self.activeMutationTask = nil
            }

            do {
                let response = try await self.apiClient.confirmAction(
                    conversationId: convId,
                    confirmationId: confId,
                    executionId: execId,
                    approved: true
                )

                guard capturedGen == self.coordinationGeneration else { return }
                let summary = response.message?.content ?? "Completed successfully"
                self.state = .completed(summary: summary)

            } catch let error as APIError {
                guard capturedGen == self.coordinationGeneration else { return }
                self.handleConfirmAPIError(
                    error: error,
                    propId: propId,
                    confId: confId,
                    execId: execId,
                    convId: convId,
                    stagedOpIds: stagedOpIds
                )
            } catch {
                guard capturedGen == self.coordinationGeneration else { return }
                // Transport / Network drop during mutating phase -> mutationOutcomeUnknown
                self.state = .mutationOutcomeUnknown(
                    proposalId: propId,
                    confirmationId: confId,
                    executionId: execId,
                    conversationId: convId,
                    stagedOperationIds: stagedOpIds,
                    error: error.localizedDescription
                )
            }
        }
    }

    private func handleConfirmAPIError(
        error: APIError,
        propId: String,
        confId: String,
        execId: String,
        convId: String,
        stagedOpIds: [String]
    ) {
        switch error {
        case .conflict(let code, let message):
            process409Conflict(message: message, code: code)

        case .serverError(let statusCode, let message):
            if statusCode == 409 {
                process409Conflict(message: message, code: "409")
            } else if statusCode == 500 || statusCode == 507 {
                // Server crash or storage fail during mutation -> mutationOutcomeUnknown
                self.state = .mutationOutcomeUnknown(
                    proposalId: propId,
                    confirmationId: confId,
                    executionId: execId,
                    conversationId: convId,
                    stagedOperationIds: stagedOpIds,
                    error: message
                )
            } else if statusCode == 404 {
                // Session not found on confirm -> recoveryUncertain
                self.state = .recoveryUncertain(
                    proposalId: propId,
                    conversationId: convId,
                    reason: .sessionNotFound
                )
            } else if statusCode == 400 {
                // Pre-mutation bad request / expired
                self.state = .failed(message: message)
            } else {
                self.state = .failed(message: message)
            }

        default:
            self.state = .mutationOutcomeUnknown(
                proposalId: propId,
                confirmationId: confId,
                executionId: execId,
                conversationId: convId,
                stagedOperationIds: stagedOpIds,
                error: error.localizedDescription
            )
        }
    }

    private func process409Conflict(
        message: String,
        code: String
    ) {
        guard case let .executingMutation(propId, confId, execId, convId, stagedOpIds) = state else {
            return
        }

        let lowerMsg = message.lowercased()
        let isRecognizedConflict = lowerMsg.contains("waiting for confirmation")
            || lowerMsg.contains("active execution")
            || lowerMsg.contains("conflict")
            || code.lowercased().contains("conflict")

        if isRecognizedConflict {
            // Blocks reapplication and initiates exactly one authoritative recovery attempt
            self.state = .mutationOutcomeUnknown(
                proposalId: propId,
                confirmationId: confId,
                executionId: execId,
                conversationId: convId,
                stagedOperationIds: stagedOpIds,
                error: message
            )
            self.performSingleRecoveryAttempt()
        } else {
            // Unrecognized 409 fails closed to recoveryUncertain
            self.state = .recoveryUncertain(
                proposalId: propId,
                conversationId: convId,
                reason: .terminalStateMismatch
            )
        }
    }

    // MARK: - Single Authoritative Recovery Attempt

    /// Performs exactly one authoritative recovery attempt. No timers, no polling, no autonomous loops.
    public func performSingleRecoveryAttempt() {
        guard case let .mutationOutcomeUnknown(propId, confId, execId, convId, stagedOpIds, _) = state else {
            return
        }
        guard activeRecoveryTask == nil else { return }

        let capturedGen = coordinationGeneration
        isRecovering = true
        state = .recovering(
            proposalId: propId,
            confirmationId: confId,
            executionId: execId,
            conversationId: convId,
            stagedOperationIds: stagedOpIds
        )

        activeRecoveryTask = Task { [weak self] in
            guard let self = self else { return }
            defer {
                self.isRecovering = false
                self.activeRecoveryTask = nil
            }

            do {
                let history = try await self.apiClient.fetchConversationHistory(conversationId: convId)
                guard capturedGen == self.coordinationGeneration else { return }

                self.state = self.evaluateRecoveryHistory(
                    history: history,
                    expectedProposalId: propId,
                    expectedExecutionId: execId,
                    stagedOperationIds: stagedOpIds,
                    conversationId: convId
                )

            } catch let error as APIError {
                guard capturedGen == self.coordinationGeneration else { return }
                if case .serverError(404, _) = error {
                    self.state = .recoveryUncertain(
                        proposalId: propId,
                        conversationId: convId,
                        reason: .sessionNotFound
                    )
                } else {
                    // Transport failure preserves mutationOutcomeUnknown
                    self.state = .mutationOutcomeUnknown(
                        proposalId: propId,
                        confirmationId: confId,
                        executionId: execId,
                        conversationId: convId,
                        stagedOperationIds: stagedOpIds,
                        error: error.localizedDescription
                    )
                }
            } catch {
                guard capturedGen == self.coordinationGeneration else { return }
                // Generic transport failure preserves uncertainty
                self.state = .mutationOutcomeUnknown(
                    proposalId: propId,
                    confirmationId: confId,
                    executionId: execId,
                    conversationId: convId,
                    stagedOperationIds: stagedOpIds,
                    error: error.localizedDescription
                )
            }
        }
    }

    // MARK: - Recovery Evaluation Logic

    public func evaluateRecoveryHistory(
        history: ConversationHistoryResponsePayload,
        expectedProposalId: String,
        expectedExecutionId: String,
        stagedOperationIds: [String],
        conversationId: String
    ) -> ScheduleCoordinationState {
        // 1. Fetch complete conversation history and inspect ALL tool messages
        let toolMessages = history.messages.filter { $0.role == "tool" }

        // 2. Parse structurally valid coordination outputs matching expected proposal and exact operation sequence
        var correlatedRecords: [CoordinationProposalExecutionOutputPayload] = []

        for msg in toolMessages {
            guard let payload = self.parseToolOutput(msg.content) else {
                continue
            }

            // Must match expected proposal_id
            guard payload.proposalId == expectedProposalId else {
                continue
            }

            // If an execution_id is explicitly present in the record, mismatch must fail closed
            if let recordExecId = payload.executionId, !recordExecId.isEmpty, recordExecId != expectedExecutionId {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .mismatchedIdentity(expected: expectedExecutionId, actual: recordExecId)
                )
            }

            // Must match exact staged operation_id sequence in count and order
            let recordOpIds = (payload.operations ?? []).map(\.operationId)
            guard recordOpIds == stagedOperationIds else {
                continue
            }

            correlatedRecords.append(payload)
        }

        // 3. Ambiguity & Missing Checks
        if correlatedRecords.isEmpty {
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .missingExecutionRecord
            )
        }

        if correlatedRecords.count > 1 {
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .ambiguousExecutionRecord
            )
        }

        // Exactly one correlated record exists: evaluate it
        let record = correlatedRecords[0]
        return evaluateSingleExecutionRecord(
            record: record,
            expectedProposalId: expectedProposalId,
            stagedOperationIds: stagedOperationIds,
            conversationId: conversationId
        )
    }

    private func evaluateSingleExecutionRecord(
        record: CoordinationProposalExecutionOutputPayload,
        expectedProposalId: String,
        stagedOperationIds: [String],
        conversationId: String
    ) -> ScheduleCoordinationState {
        guard let statusString = record.proposalStatus?.lowercased() else {
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .malformedResponse("Missing proposal_status in execution record")
            )
        }

        guard let ops = record.operations, !ops.isEmpty else {
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .malformedResponse("Operations array missing or empty in execution record")
            )
        }

        let recognizedStatuses: Set<String> = ["pending", "executing", "completed", "failed", "aborted"]
        for op in ops {
            if !recognizedStatuses.contains(op.status.lowercased()) {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .malformedResponse("Unrecognized operation status: '\(op.status)'")
                )
            }
        }

        let completedCount = ops.filter { $0.status.lowercased() == "completed" }.count
        let failedCount = ops.filter { $0.status.lowercased() == "failed" }.count

        switch statusString {
        case "completed":
            // SUCCESS VALIDATION:
            // Require: operations non-empty, all operation statuses == completed,
            // count matches staged count, and IDs match in order.
            guard completedCount == ops.count,
                  ops.count == stagedOperationIds.count,
                  ops.map(\.operationId) == stagedOperationIds else {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .malformedResponse("Completed proposal status does not have all operations completed in order")
                )
            }
            return .completed(summary: "Coordination proposal completed successfully.")

        case "partially_executed":
            // PARTIAL EXECUTION:
            // Require: completed > 0, at least one failed operation, structurally valid
            guard completedCount > 0, failedCount >= 1 else {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .malformedResponse("Partially executed proposal must have at least one completed and one failed operation")
                )
            }
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .partialExecution(completedOps: completedCount, totalOps: ops.count)
            )

        case "failed":
            // ZERO-MUTATION PROOF:
            // May become .failed ONLY when ALL required conditions are satisfied:
            // - completed operation count == 0
            // - failed operation count is 0 or 1
            // - if one operation failed, it must be the first operation
            // - all remaining operations must be pending/aborted
            // - no later operation may be completed or failed
            guard completedCount == 0 else {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .partialExecution(completedOps: completedCount, totalOps: ops.count)
                )
            }

            guard failedCount <= 1 else {
                return .recoveryUncertain(
                    proposalId: expectedProposalId,
                    conversationId: conversationId,
                    reason: .malformedResponse("Failed proposal cannot contain multiple failed operations")
                )
            }

            if failedCount == 1 {
                // Failed operation must be the first operation (index 0)
                guard ops[0].status.lowercased() == "failed" else {
                    return .recoveryUncertain(
                        proposalId: expectedProposalId,
                        conversationId: conversationId,
                        reason: .malformedResponse("Failed operation is not the first operation")
                    )
                }
                // All remaining operations (indices 1..<ops.count) must be aborted or pending
                for i in 1..<ops.count {
                    let st = ops[i].status.lowercased()
                    guard st == "aborted" || st == "pending" else {
                        return .recoveryUncertain(
                            proposalId: expectedProposalId,
                            conversationId: conversationId,
                            reason: .malformedResponse("Subsequent operation after failure is not aborted or pending")
                        )
                    }
                }
            } else {
                // failedCount == 0: Tier 1 Global Preflight failure (all ops pending/aborted)
                for op in ops {
                    let st = op.status.lowercased()
                    guard st == "pending" || st == "aborted" else {
                        return .recoveryUncertain(
                            proposalId: expectedProposalId,
                            conversationId: conversationId,
                            reason: .malformedResponse("Preflight failed proposal contains non-pending/non-aborted operations")
                        )
                    }
                }
            }

            let failMsg = record.failureReason ?? (ops.first?.failureError ?? "Proposal execution failed before domain mutation")
            return .failed(message: failMsg)

        default:
            return .recoveryUncertain(
                proposalId: expectedProposalId,
                conversationId: conversationId,
                reason: .malformedResponse("Unknown proposal_status: '\(statusString)'")
            )
        }
    }

    private func parseToolOutput(_ jsonString: String) -> CoordinationProposalExecutionOutputPayload? {
        guard let data = jsonString.data(using: .utf8) else { return nil }
        let decoder = JSONDecoder()
        return try? decoder.decode(CoordinationProposalExecutionOutputPayload.self, from: data)
    }
}
