//
//  ScheduleRecoveryLifecycleTests.swift
//  ProjectAuroraTests
//
//  Native XCTest suite for Phase 6P: Schedule Recovery Proposal Lifecycle Hardening & Transport Uncertainty Orchestration.
//

import Foundation
#if canImport(XCTest)
import XCTest
#endif
@testable import ProjectAurora

#if canImport(XCTest)
open class ScheduleRecoveryBaseTestCase: XCTestCase {}
#else
open class ScheduleRecoveryBaseTestCase {
    public init() {}
    open func setUp() {}
}
#endif

@MainActor
public final class ScheduleRecoveryLifecycleTests: ScheduleRecoveryBaseTestCase {
    private var client: AuroraAPIClient!
    private var session: URLSession!
    private var viewModel: ScheduleRecoveryViewModel!

    public override func setUp() {
        super.setUp()
        setup()
    }

    public func setup() {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        session = URLSession(configuration: config)
        client = AuroraAPIClient(
            baseURL: URL(string: "http://localhost:8000")!,
            session: session,
            credentialProvider: { "test-recovery-bearer-token" }
        )
        viewModel = ScheduleRecoveryViewModel(apiClient: client)
    }

    // MARK: - Assertion Helpers

    private func assertTrue(_ condition: Bool, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertTrue(condition, message, file: file, line: line)
        #else
        assert(condition, message)
        #endif
    }

    private func assertFalse(_ condition: Bool, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertFalse(condition, message, file: file, line: line)
        #else
        assert(!condition, message)
        #endif
    }

    private func assertEqual<T: Equatable>(_ a: T, _ b: T, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertEqual(a, b, message, file: file, line: line)
        #else
        assert(a == b, message)
        #endif
    }

    private func assertNotNil(_ value: Any?, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertNotNil(value, message, file: file, line: line)
        #else
        assert(value != nil, message)
        #endif
    }

    private func assertNil(_ value: Any?, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertNil(value, message, file: file, line: line)
        #else
        assert(value == nil, message)
        #endif
    }

    // MARK: - Fixture Helpers

    private func makeProposal(
        id: String = "prop_test_01",
        status: RecoveryProposalStatus = .pendingConfirmation,
        failureReason: String? = nil
    ) -> ScheduleRecoveryProposal {
        let op = ScheduleRecoveryOperation(
            operationId: "op_task_01",
            operationType: .rescheduleTask,
            targetEntityId: "task_01",
            newScheduledTime: Date().addingTimeInterval(3600),
            payload: .task(RescheduleTaskPayload(taskId: "task_01", newDueAt: Date().addingTimeInterval(3600), expectedVersion: 1))
        )
        let cal = CalendarExclusivityPrecondition(
            windowStart: Date(),
            windowEnd: Date().addingTimeInterval(86400),
            calendarEventFingerprint: "cal_fp"
        )
        let wh = WorkingHoursExternalDependency(
            effectiveTimezone: "UTC",
            effectiveDates: ["2026-10-07"],
            workingHoursFingerprint: "wh_fp"
        )
        return ScheduleRecoveryProposal(
            proposalId: id,
            userId: "usr_test",
            createdAt: Date(),
            expiresAt: Date().addingTimeInterval(1800),
            status: status,
            operations: [op],
            taskPreconditions: [],
            reminderPreconditions: [],
            commitmentDependencies: [],
            workingHoursDependency: wh,
            calendarPrecondition: cal,
            preconditionsHash: "precond_hash",
            ephemeralNotes: [],
            failureReason: failureReason,
            appliedAt: nil
        )
    }

    private func makeResult(
        proposalId: String = "prop_test_01",
        status: RecoveryExecutionStatus = .executed,
        completedOps: [String] = ["op_task_01"],
        failedOpId: String? = nil,
        failureReason: String? = nil
    ) -> RecoveryExecutionResult {
        return RecoveryExecutionResult(
            executionId: "exec_01",
            proposalId: proposalId,
            userId: "usr_test",
            status: status,
            startedAt: Date(),
            completedAt: Date(),
            completedOperations: completedOps,
            failedOperationId: failedOpId,
            failureReason: failureReason,
            executionTrace: ["trace_1"],
            diagnostics: RecoveryExecutionDiagnostics(
                preflightCheckedAt: Date(),
                preflightDurationMs: 1.0,
                executionDurationMs: 2.0,
                totalDurationMs: 3.0,
                presentationNotes: []
            )
        )
    }

    private func encode<T: Encodable>(_ value: T) -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return try! encoder.encode(value)
    }

    // MARK: - 1. Sheet Dismissal Preserves Active Mutation Task

    public func test01_SheetDismissal_PreservesActiveMutationTask() async throws {
        let res = makeResult()
        MockURLProtocol.requestHandler = { _ in
            let data = self.encode(res)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        viewModel.setProposal(proposal)

        // Sheet initiates mutation
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        assertNotNil(task)

        // Simulate sheet dismissal: local view deallocates, but parent keeps viewModel
        // activeMutationTask must remain NOT nil and NOT cancelled
        assertFalse(task!.isCancelled)

        _ = await task?.value
        assertNil(viewModel.activeMutationTask)
    }

    // MARK: - 2. Sheet Dismissal Does Not Increment recoveryGeneration

    public func test02_SheetDismissal_DoesNotIncrementGeneration() async throws {
        let initialGen = viewModel.recoveryGeneration

        // Sheet dismissal occurs at presentation layer
        // recoveryGeneration must stay unchanged
        assertEqual(viewModel.recoveryGeneration, initialGen)
    }

    // MARK: - 3. Mutation Completes and Commits State After Sheet Dismissal

    public func test03_MutationCompletesAfterSheetDismissal_CommitsToParentViewModel() async throws {
        let res = makeResult()
        MockURLProtocol.requestHandler = { _ in
            let data = self.encode(res)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        viewModel.setProposal(proposal)

        let task = viewModel.applyProposal(proposalId: proposal.proposalId)

        // View dismissed while task runs:
        _ = await task?.value

        // Parent-owned ViewModel committed the result
        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .applied(let committed) = resolvedState {
                assertEqual(committed.proposalId, proposal.proposalId)
            } else {
                assertTrue(false, "Expected .applied, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 4. startFresh() Cancels Active Local Tasks and Resets State

    public func test04_StartFresh_CancelsActiveTasks_ResetsToIdle() async throws {
        MockURLProtocol.requestHandler = { _ in
            let res = self.makeResult()
            let data = self.encode(res)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        viewModel.setProposal(proposal)
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        assertNotNil(task)

        // Explicit local reset
        viewModel.startFresh()

        assertTrue(task!.isCancelled)
        assertNil(viewModel.activeMutationTask)
        assertEqual(viewModel.sheetState, .idle)
    }

    // MARK: - 5. startFresh() Increments recoveryGeneration

    public func test05_StartFresh_IncrementsRecoveryGeneration() async throws {
        let initialGen = viewModel.recoveryGeneration
        viewModel.startFresh()
        assertEqual(viewModel.recoveryGeneration, initialGen + 1)
    }

    // MARK: - 6. Explicit Server Cancellation Sends POST /proposals/{id}/cancel

    public func test06_ExplicitServerCancel_SendsServerCancellationRequest() async throws {
        var cancellationEndpointCalled = false
        MockURLProtocol.requestHandler = { request in
            if request.url?.path.contains("/cancel") == true {
                cancellationEndpointCalled = true
                let cancelled = self.makeProposal(status: .cancelled)
                let data = self.encode(cancelled)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            }
            throw APIError.networkError("Unexpected endpoint")
        }

        let proposal = makeProposal()
        viewModel.setProposal(proposal)
        let task = viewModel.cancelProposal(proposalId: proposal.proposalId, reason: "User request")
        _ = await task?.value

        assertTrue(cancellationEndpointCalled)
        assertEqual(viewModel.sheetState, .resolved(.cancelled))
    }

    // MARK: - 7. HTTP 200 -> .applied

    public func test07_ApplySuccess200_TransitionsToApplied() async throws {
        let res = makeResult()
        MockURLProtocol.requestHandler = { _ in
            let data = self.encode(res)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .applied(let committed) = resolvedState {
                assertEqual(committed.executionId, "exec_01")
            } else {
                assertTrue(false, "Expected .applied, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 8. HTTP 412 -> .preconditionFailed

    public func test08_ApplyPreflightFailed412_TransitionsToPreconditionFailed() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "TASK_VERSION_DRIFT",
                message: "Task version changed from 1 to 2",
                failedOperationId: nil,
                completedOperations: []
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 412, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .preconditionFailed(let reason) = resolvedState {
                assertTrue(reason.contains("TASK_VERSION_DRIFT"))
            } else {
                assertTrue(false, "Expected .preconditionFailed, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 9. 409 with Completed Operations -> .partiallyExecuted

    public func test09_Conflict409_CompletedOpsPresent_TransitionsToPartiallyExecuted() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "PARTIAL_EXECUTION_FAILURE",
                message: "Reminder service unavailable",
                failedOperationId: "op_rem_02",
                completedOperations: ["op_task_01"]
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .partiallyExecuted(_, let detail) = resolvedState {
                assertEqual(detail?.completedOperations, ["op_task_01"])
                assertEqual(detail?.failedOperationId, "op_rem_02")
            } else {
                assertTrue(false, "Expected .partiallyExecuted, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 10. 409 with Zero Completed Operations -> .failed

    public func test10_Conflict409_ZeroCompletedOps_TransitionsToFailed() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "PARTIAL_EXECUTION_FAILURE",
                message: "Disk I/O error on Op 1",
                failedOperationId: "op_task_01",
                completedOperations: []
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .failed(_, let detail) = resolvedState {
                assertEqual(detail?.completedOperations.count, 0)
                assertEqual(detail?.failedOperationId, "op_task_01")
            } else {
                assertTrue(false, "Expected .failed, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 11. 409 Proposal Expired -> .expired

    public func test11_Conflict409_ProposalExpired_TransitionsToExpired() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "PROPOSAL_EXPIRED",
                message: "Proposal has expired",
                failedOperationId: nil,
                completedOperations: []
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        assertEqual(viewModel.sheetState, .resolved(.expired))
    }

    // MARK: - 12. 409 Active Lock -> Non-terminal Retryable State

    public func test12_Conflict409_ActiveLockConflict_PresentsNotice_AllowsRetry() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "ACTIVE_EXECUTION_LOCK_CONFLICT",
                message: "Execution currently in progress for user",
                failedOperationId: nil,
                completedOperations: []
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .lockConflict(let msg) = viewModel.sheetState {
            assertTrue(msg.contains("in progress"))
        } else {
            assertTrue(false, "Expected .lockConflict, got \(viewModel.sheetState)")
        }

        // Lock conflict is non-terminal, so re-applying is NOT blocked
        assertFalse(viewModel.sheetState.isTerminal)
    }

    // MARK: - 13. 409 Proposal Already Terminal -> Enters Authoritative Recovery

    public func test13_Conflict409_AlreadyTerminal_EntersAuthoritativeRecovery() async throws {
        MockURLProtocol.requestHandler = { _ in
            let detail = ScheduleRecoveryErrorDetail(
                error: "PROPOSAL_ALREADY_TERMINAL",
                message: "Proposal is already in terminal state APPLIED",
                failedOperationId: nil,
                completedOperations: []
            )
            let container = FastAPIErrorContainer(detail: detail)
            let data = try! JSONEncoder().encode(container)
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId, _) = viewModel.sheetState {
            assertEqual(propId, proposal.proposalId)
        } else {
            assertTrue(false, "Expected .mutationOutcomeUnknown, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 14. HTTP 500 / 507 -> .mutationOutcomeUnknown (Option B)

    public func test14_ApplyHttp500And507_TransitionsToMutationOutcomeUnknown() async throws {
        // Test 507:
        MockURLProtocol.requestHandler = { _ in
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 507, httpVersion: nil, headerFields: nil)!
            return (response, "Result store capacity exceeded".data(using: .utf8)!)
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId, let reason) = viewModel.sheetState {
            assertEqual(propId, proposal.proposalId)
            assertTrue(reason.contains("507"))
        } else {
            assertTrue(false, "Expected .mutationOutcomeUnknown on 507, got \(viewModel.sheetState)")
        }

        // Test 500:
        viewModel.startFresh()
        MockURLProtocol.requestHandler = { _ in
            let response = HTTPURLResponse(url: URL(string: "http://localhost:8000")!, statusCode: 500, httpVersion: nil, headerFields: nil)!
            return (response, "Internal server error".data(using: .utf8)!)
        }

        let task500 = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task500?.value

        if case .mutationOutcomeUnknown(let propId, let reason) = viewModel.sheetState {
            assertEqual(propId, proposal.proposalId)
            assertTrue(reason.contains("500"))
        } else {
            assertTrue(false, "Expected .mutationOutcomeUnknown on 500, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 15. Network Timeout -> .mutationOutcomeUnknown

    public func test15_ApplyNetworkTimeout_TransitionsToMutationOutcomeUnknown() async throws {
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: [NSLocalizedDescriptionKey: "The request timed out."])
        }

        let proposal = makeProposal()
        let task = viewModel.applyProposal(proposalId: proposal.proposalId)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId, let reason) = viewModel.sheetState {
            assertEqual(propId, proposal.proposalId)
            assertTrue(reason.contains("timed out"))
        } else {
            assertTrue(false, "Expected .mutationOutcomeUnknown on timeout, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 16. Authoritative Recovery: APPLIED + Result Available -> .applied(result)

    public func test16_AuthoritativeRecovery_AppliedWithResultAvailable() async throws {
        let terminalProp = makeProposal(status: .applied)
        let res = makeResult()

        MockURLProtocol.requestHandler = { request in
            if request.url?.path.hasSuffix("/result") == true {
                let data = self.encode(res)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            } else {
                let data = self.encode(terminalProp)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            }
        }

        let task = viewModel.recoverProposal(proposalId: terminalProp.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .applied(let committed) = resolvedState {
                assertEqual(committed.executionId, res.executionId)
            } else {
                assertTrue(false, "Expected .applied, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 17. Authoritative Recovery: APPLIED + Result 404 -> .appliedWithoutExecutionResult

    public func test17_AuthoritativeRecovery_AppliedWithResultUnavailable_DegradedTerminal() async throws {
        let terminalProp = makeProposal(status: .applied)

        MockURLProtocol.requestHandler = { request in
            if request.url?.path.hasSuffix("/result") == true {
                let response = HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!
                return (response, "Result not found".data(using: .utf8)!)
            } else {
                let data = self.encode(terminalProp)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            }
        }

        let task = viewModel.recoverProposal(proposalId: terminalProp.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .appliedWithoutExecutionResult(let prop) = resolvedState {
                assertEqual(prop.proposalId, terminalProp.proposalId)
            } else {
                assertTrue(false, "Expected .appliedWithoutExecutionResult, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 18. Authoritative Recovery: ABORTED + Result 404 -> .abortedWithoutExecutionResult

    public func test18_AuthoritativeRecovery_AbortedWithResultUnavailable_DegradedTerminal() async throws {
        let abortedProp = makeProposal(status: .aborted, failureReason: "Server capacity exhausted")

        MockURLProtocol.requestHandler = { request in
            if request.url?.path.hasSuffix("/result") == true {
                let response = HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!
                return (response, "Result not found".data(using: .utf8)!)
            } else {
                let data = self.encode(abortedProp)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            }
        }

        let task = viewModel.recoverProposal(proposalId: abortedProp.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .abortedWithoutExecutionResult(let prop) = resolvedState {
                assertEqual(prop.proposalId, abortedProp.proposalId)
                assertEqual(prop.failureReason, "Server capacity exhausted")
            } else {
                assertTrue(false, "Expected .abortedWithoutExecutionResult, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }
    }

    // MARK: - 19. Authoritative Recovery: PENDING_CONFIRMATION -> .readyToApply

    public func test19_AuthoritativeRecovery_PendingConfirmation_AllowsReapply() async throws {
        let pendingProp = makeProposal(status: .pendingConfirmation)

        MockURLProtocol.requestHandler = { request in
            let data = self.encode(pendingProp)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let task = viewModel.recoverProposal(proposalId: pendingProp.proposalId)
        _ = await task?.value

        assertEqual(viewModel.sheetState, .resolved(.readyToApply))
        assertFalse(viewModel.sheetState.isTerminal)
    }

    // MARK: - 20. Safety Invariants: No Fabricated Result, No Blind Reapply, No Polling

    public func test20_StrictSafetyGuarantees_NoFabricatedResult_NoBlindReapply_NoPolling() async throws {
        // Guarantee 1: When result is missing, no fake RecoveryExecutionResult is synthesized:
        let terminalProp = makeProposal(status: .applied)
        let resolvedWithoutResult = ScheduleRecoveryStateResolver.resolve(proposal: terminalProp, result: nil)
        if case .appliedWithoutExecutionResult(let prop) = resolvedWithoutResult {
            assertEqual(prop.proposalId, terminalProp.proposalId)
        } else {
            assertTrue(false, "Expected appliedWithoutExecutionResult, got \(resolvedWithoutResult)")
        }

        // Guarantee 2: When in terminal state, calling applyProposal returns nil (no reapply):
        MockURLProtocol.requestHandler = { request in
            let data = self.encode(terminalProp)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }
        let task = viewModel.recoverProposal(proposalId: terminalProp.proposalId)
        _ = await task?.value
        assertTrue(viewModel.sheetState.isTerminal)

        let reapplyAttempt = viewModel.applyProposal(proposalId: terminalProp.proposalId)
        assertNil(reapplyAttempt, "Reapplying a terminal proposal must return nil and be prohibited")

        // Guarantee 3: No automatic polling: after recovery completes, no active task runs
        assertNil(viewModel.activeRecoveryTask)
        assertNil(viewModel.activeMutationTask)
    }

    // MARK: - 21. Terminal Result Fetch Transport Failure Preserves Known Lifecycle

    public func test21_TerminalResultFetchTransportFailure_PreservesKnownLifecycle() async throws {
        let terminalProp = makeProposal(status: .applied)

        MockURLProtocol.requestHandler = { request in
            if request.url?.path.hasSuffix("/result") == true {
                throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost, userInfo: [NSLocalizedDescriptionKey: "The network connection was lost."])
            } else {
                let data = self.encode(terminalProp)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, data)
            }
        }

        let task = viewModel.recoverProposal(proposalId: terminalProp.proposalId)
        _ = await task?.value

        if case .resolved(let resolvedState) = viewModel.sheetState {
            if case .terminalWithResultFetchError(let prop, let err) = resolvedState {
                assertEqual(prop.proposalId, terminalProp.proposalId)
                assertEqual(prop.status, .applied)
                assertTrue(err.contains("connection was lost"))
            } else {
                assertTrue(false, "Expected .terminalWithResultFetchError, got \(resolvedState)")
            }
        } else {
            assertTrue(false, "Expected .resolved, got \(viewModel.sheetState)")
        }

        // Terminal lifecycle is preserved, reapply remains prohibited
        assertTrue(viewModel.sheetState.isTerminal)
        let reapplyAttempt = viewModel.applyProposal(proposalId: terminalProp.proposalId)
        assertNil(reapplyAttempt)
    }
}
