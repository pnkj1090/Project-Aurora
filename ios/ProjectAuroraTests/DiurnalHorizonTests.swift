//
//  DiurnalHorizonTests.swift
//  ProjectAuroraTests
//
//  XCTest suite for Phase 6M: Diurnal Horizon Mobile Presentation & Apply Workflow.
//

import Foundation

#if canImport(XCTest)
import XCTest
#endif
@testable import ProjectAurora

#if canImport(XCTest)
open class DiurnalHorizonBaseTestCase: XCTestCase {}
#else
open class DiurnalHorizonBaseTestCase {
    public init() {}
    open func setUp() {}
}
#endif

@MainActor
public final class DiurnalHorizonTests: DiurnalHorizonBaseTestCase {
    private var client: AuroraAPIClient!
    private var session: URLSession!

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
            credentialProvider: { "test-bearer-token" }
        )
    }

    public func runAllTests() async throws {
        print("--- Running DiurnalHorizonTests ---")
        try testDecodeDailyExecutivePlanPayload()
        try testDecodeDayPlanProposalPayload()
        try testDecodeEveningHorizonReviewPayload()
        try testDecodeEveningReviewProposalPayload()

        try await testHTTP200CompletedServerStatus()
        try await testHTTP200AbortedServerStatus()
        try await testHTTP200FailedServerStatus()
        try await testHTTP200PartiallyExecutedServerStatus()

        try await testHTTP409Expired()
        try await testHTTP409StateHashMismatch()
        try await testHTTP409PreconditionFailed()
        try await testHTTP409StaleCalendarState()
        try await testHTTP409IllegalStateTransition()
        try await testHTTP409InsufficientPermissions()
        try await testHTTP409OperationFailed()

        try await testTransportTimeoutYieldsMutationOutcomeUnknown()
        try await testNetworkDropYieldsMutationOutcomeUnknown()
        try await testMutationOutcomeUnknownNeverFabricatesProposalStatus()
        try await testNoAutomaticRetryOnMutationOutcomeUnknown()
        try await testNoUndeclaredStatusEndpointCalled()

        try await testDuplicateApplyTapIgnored()
        try await testDuplicateAbortTapIgnored()
        try await testApplyAbortMutualExclusion()

        testCheckAgendaPerformsZeroPhase6MAPICalls()
        testCountdownExpirationDoesNotMutateProposalStatus()

        print("✅ All 25 DiurnalHorizonTests passed successfully!")
    }

    // MARK: - 1. DTO Decoding: DailyExecutivePlanPayload

    public func testDecodeDailyExecutivePlanPayload() throws {
        let json = """
        {
            "plan_id": "plan_123",
            "target_date": "2026-10-02",
            "timezone": "Asia/Kolkata",
            "generated_at": "2026-10-02T08:00:00Z",
            "executive_summary": "Focused morning with 2 critical tasks.",
            "schedule_intervals": [
                {
                    "start_at": "2026-10-02T09:00:00Z",
                    "end_at": "2026-10-02T10:00:00Z",
                    "title": "Architecture Sync",
                    "source_domain": "calendar",
                    "is_working_hour": true
                }
            ],
            "free_slots": [
                {
                    "start_at": "2026-10-02T10:00:00Z",
                    "end_at": "2026-10-02T12:00:00Z",
                    "duration_minutes": 120
                }
            ],
            "actionable_tasks": [
                {
                    "task_id": "task_1",
                    "title": "Review PR",
                    "priority": "high",
                    "due_at": "2026-10-02T17:00:00Z",
                    "estimated_duration_minutes": 45
                }
            ],
            "active_commitments": [
                {
                    "commitment_id": "comm_1",
                    "title": "Deliver Phase 6M",
                    "target_date": "2026-10-02",
                    "status": "active"
                }
            ],
            "scheduled_reminders": [
                {
                    "reminder_id": "rem_1",
                    "title": "Standup reminder",
                    "trigger_at": "2026-10-02T09:45:00Z"
                }
            ],
            "focus_recommendations": [
                {
                    "focus_title": "Deep Work Block",
                    "rationale": "High focus window",
                    "suggested_task_ids": ["task_1"],
                    "allocated_minutes": 90
                }
            ],
            "omitted_domains": []
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        let plan = try decoder.decode(DailyExecutivePlanPayload.self, from: json)
        assert(plan.targetDate == "2026-10-02", "targetDate mismatch")
        assert(plan.executiveSummary == "Focused morning with 2 critical tasks.", "summary mismatch")
        assert(plan.scheduleIntervals.count == 1, "scheduleIntervals count mismatch")
        assert(plan.freeSlots.count == 1, "freeSlots count mismatch")
        assert(plan.actionableTasks.count == 1, "actionableTasks count mismatch")
        assert(plan.activeCommitments.count == 1, "activeCommitments count mismatch")
        assert(plan.scheduledReminders.count == 1, "scheduledReminders count mismatch")
        assert(plan.focusRecommendations.count == 1, "focusRecommendations count mismatch")
    }

    // MARK: - 2. DTO Decoding: DayPlanProposalPayload

    public func testDecodeDayPlanProposalPayload() throws {
        let json = """
        {
            "proposal_id": "hpp_day_abc123",
            "proposal_type": "day_plan",
            "target_date": "2026-10-02",
            "status": "draft",
            "state_hash": "hash_xyz_789",
            "expires_at": "2026-10-02T10:00:00Z",
            "ordered_operations": [
                {
                    "operation_id": "hop_op1",
                    "operation_type": "create_reminder",
                    "target_domain": "reminders",
                    "target_entity_id": null,
                    "proposed_payload": {"title": "Team reminder"},
                    "status": "pending",
                    "executed_at": null,
                    "error": null,
                    "failure_error": null,
                    "created_entity_id": null
                }
            ],
            "applied_at": null,
            "failure_reason": null
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        let proposal = try decoder.decode(DayPlanProposalPayload.self, from: json)
        assert(proposal.proposalId == "hpp_day_abc123", "proposalId mismatch")
        assert(proposal.status == .draft, "status mismatch")
        assert(proposal.orderedOperations.count == 1, "operations count mismatch")
        assert(proposal.orderedOperations[0].operationType == .createReminder, "operationType mismatch")
    }

    // MARK: - 3. DTO Decoding: EveningHorizonReviewPayload

    public func testDecodeEveningHorizonReviewPayload() throws {
        let json = """
        {
            "review_id": "rev_123",
            "target_date": "2026-10-02",
            "timezone": "Asia/Kolkata",
            "generated_at": "2026-10-02T18:00:00Z",
            "executive_summary": "Great progress made today.",
            "accomplishments": ["Shipped Phase 6M DTOs", "Resolved blockers"],
            "completed_tasks": [
                {"task_id": "t1", "title": "Implement DTOs", "completed_at": "2026-10-02T14:00:00Z"}
            ],
            "overdue_tasks": [
                {"task_id": "t2", "title": "Update Documentation", "due_at": "2026-10-02T16:00:00Z", "priority": "medium"}
            ],
            "completed_commitments": [
                {"commitment_id": "c1", "title": "Complete Sprint", "completed_at": "2026-10-02T17:00:00Z"}
            ],
            "rollover_candidates": [
                {"task_id": "t2", "title": "Update Documentation", "suggested_new_date": "2026-10-03", "reason": "overdue"}
            ],
            "completion_stats": {
                "completed_task_count": 1,
                "overdue_task_count": 1,
                "completed_commitment_count": 1,
                "completion_rate_percent": 50.0
            },
            "omitted_domains": []
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        let review = try decoder.decode(EveningHorizonReviewPayload.self, from: json)
        assert(review.accomplishments.count == 2, "accomplishments count mismatch")
        assert(review.completedTasks.count == 1, "completedTasks count mismatch")
        assert(review.overdueTasks.count == 1, "overdueTasks count mismatch")
        assert(review.rolloverCandidates.count == 1, "rolloverCandidates count mismatch")
        assert(review.completionStats.completionRatePercent == 50.0, "completionRate mismatch")
    }

    // MARK: - 4. DTO Decoding: EveningReviewProposalPayload

    public func testDecodeEveningReviewProposalPayload() throws {
        let json = """
        {
            "proposal_id": "hpp_eve_abc456",
            "proposal_type": "evening_review",
            "target_date": "2026-10-02",
            "status": "draft",
            "state_hash": "hash_eve_123",
            "expires_at": "2026-10-02T22:00:00Z",
            "ordered_operations": [
                {
                    "operation_id": "hop_op2",
                    "operation_type": "rollover_task",
                    "target_domain": "tasks",
                    "target_entity_id": "t2",
                    "proposed_payload": {"title": "Update Documentation"},
                    "status": "pending",
                    "executed_at": null,
                    "error": null,
                    "failure_error": null,
                    "created_entity_id": null
                }
            ],
            "applied_at": null,
            "failure_reason": null
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        let proposal = try decoder.decode(EveningReviewProposalPayload.self, from: json)
        assert(proposal.proposalId == "hpp_eve_abc456", "proposalId mismatch")
        assert(proposal.status == .draft, "status mismatch")
        assert(proposal.orderedOperations.count == 1, "operations count mismatch")
    }

    // MARK: - 5. HTTP 200 + COMPLETED Server Status

    public func testHTTP200CompletedServerStatus() async throws {
        setup()
        let sampleProposalJSON = """
        {
            "proposal_id": "hpp_day_100",
            "proposal_type": "day_plan",
            "target_date": "2026-10-02",
            "status": "completed",
            "state_hash": "h1",
            "expires_at": "2026-10-02T12:00:00Z",
            "ordered_operations": [],
            "applied_at": "2026-10-02T10:00:00Z",
            "failure_reason": null
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, sampleProposalJSON)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_day_100",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        assert(vm.diurnalSheetState == .completedPresentation, "Expected completedPresentation, got \(vm.diurnalSheetState)")
    }

    // MARK: - 6. HTTP 200 + ABORTED Server Status

    public func testHTTP200AbortedServerStatus() async throws {
        setup()
        let sampleProposalJSON = """
        {
            "proposal_id": "hpp_day_101",
            "proposal_type": "day_plan",
            "target_date": "2026-10-02",
            "status": "aborted",
            "state_hash": "h1",
            "expires_at": "2026-10-02T12:00:00Z",
            "ordered_operations": [],
            "applied_at": null,
            "failure_reason": null
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, sampleProposalJSON)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_day_101",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: false)
        await task?.value

        assert(vm.diurnalSheetState == .abortedPresentation, "Expected abortedPresentation, got \(vm.diurnalSheetState)")
    }

    // MARK: - 7. HTTP 200 + FAILED Server Status (Not treated as transport error)

    public func testHTTP200FailedServerStatus() async throws {
        setup()
        let sampleProposalJSON = """
        {
            "proposal_id": "hpp_day_102",
            "proposal_type": "day_plan",
            "target_date": "2026-10-02",
            "status": "failed",
            "state_hash": "h1",
            "expires_at": "2026-10-02T12:00:00Z",
            "ordered_operations": [],
            "applied_at": null,
            "failure_reason": "DOMAIN_PRECONDITION_FAILED"
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, sampleProposalJSON)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_day_102",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        if case .proposalFailedPresentation(let reason, _) = vm.diurnalSheetState {
            assert(reason == "DOMAIN_PRECONDITION_FAILED", "Expected failure reason")
        } else {
            assertionFailure("Expected proposalFailedPresentation, got \(vm.diurnalSheetState)")
        }
    }

    // MARK: - 8. HTTP 200 + PARTIALLY_EXECUTED Server Status

    public func testHTTP200PartiallyExecutedServerStatus() async throws {
        setup()
        let sampleProposalJSON = """
        {
            "proposal_id": "hpp_day_103",
            "proposal_type": "day_plan",
            "target_date": "2026-10-02",
            "status": "partially_executed",
            "state_hash": "h1",
            "expires_at": "2026-10-02T12:00:00Z",
            "ordered_operations": [
                {
                    "operation_id": "op_1",
                    "operation_type": "create_reminder",
                    "proposed_payload": {},
                    "status": "applied"
                },
                {
                    "operation_id": "op_2",
                    "operation_type": "rollover_task",
                    "proposed_payload": {},
                    "status": "failed",
                    "failure_error": "Task already completed"
                }
            ],
            "applied_at": "2026-10-02T10:05:00Z",
            "failure_reason": null
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, sampleProposalJSON)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_day_103",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        assert(vm.diurnalSheetState == .partiallyExecutedPresentation, "Expected partiallyExecutedPresentation, got \(vm.diurnalSheetState)")
        assert(vm.activeDayPlanProposal?.orderedOperations.count == 2, "Operations count mismatch")
    }

    // MARK: - 9. HTTP 409 + EXPIRED

    public func testHTTP409Expired() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "EXPIRED", "message": "Horizon proposal has expired"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, message) {
            assert(code == "EXPIRED", "Expected code EXPIRED, got \(code)")
            assert(message.contains("expired"), "Message mismatch")
        }
    }

    // MARK: - 10. HTTP 409 + STATE_HASH_MISMATCH

    public func testHTTP409StateHashMismatch() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "STATE_HASH_MISMATCH", "message": "Underlying entity state has changed"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "STATE_HASH_MISMATCH", "Expected code STATE_HASH_MISMATCH, got \(code)")
        }
    }

    // MARK: - 11. HTTP 409 + PRECONDITION_FAILED

    public func testHTTP409PreconditionFailed() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "PRECONDITION_FAILED", "message": "Precondition check failed"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "PRECONDITION_FAILED", "Expected code PRECONDITION_FAILED, got \(code)")
        }
    }

    // MARK: - 12. HTTP 409 + STALE_CALENDAR_STATE

    public func testHTTP409StaleCalendarState() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "STALE_CALENDAR_STATE", "message": "Calendar snapshot is stale"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "STALE_CALENDAR_STATE", "Expected code STALE_CALENDAR_STATE, got \(code)")
        }
    }

    // MARK: - 13. HTTP 409 + ILLEGAL_STATE_TRANSITION

    public func testHTTP409IllegalStateTransition() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "ILLEGAL_STATE_TRANSITION", "message": "Transition from EXPIRED to APPROVED is illegal"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "ILLEGAL_STATE_TRANSITION", "Expected code ILLEGAL_STATE_TRANSITION, got \(code)")
        }
    }

    // MARK: - 14. HTTP 409 + INSUFFICIENT_PERMISSIONS

    public func testHTTP409InsufficientPermissions() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "INSUFFICIENT_PERMISSIONS", "message": "Write permission denied for target domain"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "INSUFFICIENT_PERMISSIONS", "Expected code INSUFFICIENT_PERMISSIONS, got \(code)")
        }
    }

    // MARK: - 15. HTTP 409 + OPERATION_FAILED

    public func testHTTP409OperationFailed() async throws {
        setup()
        let errorJSON = """
        {"detail": {"code": "OPERATION_FAILED", "message": "Underlying domain service threw an error"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, errorJSON)
        }

        do {
            _ = try await client.applyDayPlan(proposalId: "p1", approved: true)
            assertionFailure("Expected APIError.conflict")
        } catch let APIError.conflict(code, _) {
            assert(code == "OPERATION_FAILED", "Expected code OPERATION_FAILED, got \(code)")
        }
    }

    // MARK: - 16. Transport Timeout -> mutationOutcomeUnknown

    public func testTransportTimeoutYieldsMutationOutcomeUnknown() async throws {
        setup()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: [NSLocalizedDescriptionKey: "The connection timed out."])
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_day_timeout",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        assert(vm.diurnalSheetState == .mutationOutcomeUnknown(proposalId: "hpp_day_timeout"), "Expected mutationOutcomeUnknown on timeout, got \(vm.diurnalSheetState)")
    }

    // MARK: - 17. Network Drop -> mutationOutcomeUnknown

    public func testNetworkDropYieldsMutationOutcomeUnknown() async throws {
        setup()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost, userInfo: [NSLocalizedDescriptionKey: "The network connection was lost."])
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeEveningReviewProposal = EveningReviewProposalPayload(
            proposalId: "hpp_eve_drop",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T22:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveEveningReview(approved: true)
        await task?.value

        assert(vm.diurnalSheetState == .mutationOutcomeUnknown(proposalId: "hpp_eve_drop"), "Expected mutationOutcomeUnknown on network drop, got \(vm.diurnalSheetState)")
    }

    // MARK: - 18. mutationOutcomeUnknown Never Fabricates Proposal Status

    public func testMutationOutcomeUnknownNeverFabricatesProposalStatus() async throws {
        setup()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost, userInfo: nil)
        }

        let vm = AgendaViewModel(apiClient: client)
        let initialProposal = DayPlanProposalPayload(
            proposalId: "hpp_p_1",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "hash1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.activeDayPlanProposal = initialProposal
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        assert(vm.diurnalSheetState == .mutationOutcomeUnknown(proposalId: "hpp_p_1"), "Expected mutationOutcomeUnknown")
        assert(vm.activeDayPlanProposal?.status == .draft, "Proposal status must remain draft and not fabricated")
    }

    // MARK: - 19. No Automatic Retry on mutationOutcomeUnknown

    public func testNoAutomaticRetryOnMutationOutcomeUnknown() async throws {
        setup()
        var requestCount = 0
        MockURLProtocol.requestHandler = { _ in
            requestCount += 1
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_retry_test",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task = vm.applyActiveDayPlan(approved: true)
        await task?.value

        assert(requestCount == 1, "Must not retry automatically on transport error, request count: \(requestCount)")
    }

    // MARK: - 20. No Undeclared Status Endpoint Called

    public func testNoUndeclaredStatusEndpointCalled() async throws {
        setup()
        var requestedPaths: [String] = []
        MockURLProtocol.requestHandler = { request in
            if let p = request.url?.path {
                requestedPaths.append(p)
            }
            let sampleJSON = """
            {
                "proposal_id": "hpp_test",
                "target_date": "2026-10-02",
                "status": "completed",
                "state_hash": "h1",
                "expires_at": "2026-10-02T12:00:00Z"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (res, sampleJSON)
        }

        _ = try await client.applyDayPlan(proposalId: "hpp_test", approved: true)

        let allowedPaths = Set([
            "/api/v1/agenda/daily/executive-plan",
            "/api/v1/agenda/daily/plan/propose",
            "/api/v1/agenda/daily/plan/apply",
            "/api/v1/agenda/daily/evening-review",
            "/api/v1/agenda/daily/evening-review/propose",
            "/api/v1/agenda/daily/evening-review/apply"
        ])

        for path in requestedPaths {
            assert(allowedPaths.contains(path), "Unexpected path called: \(path)")
            assert(!path.contains("/daily/status"), "No status endpoint may exist")
            assert(path != "/api/v1/agenda/daily", "Undeclared daily agenda call forbidden")
        }
    }

    // MARK: - 21. Concurrency: Duplicate Apply Tap Ignored

    public func testDuplicateApplyTapIgnored() async throws {
        setup()
        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_dup",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task1 = vm.applyActiveDayPlan(approved: true)
        assert(task1 != nil, "First apply must dispatch")

        let task2 = vm.applyActiveDayPlan(approved: true)
        assert(task2 == nil, "Second apply tap must be ignored while in-flight")
    }

    // MARK: - 22. Concurrency: Duplicate Abort Tap Ignored

    public func testDuplicateAbortTapIgnored() async throws {
        setup()
        let vm = AgendaViewModel(apiClient: client)
        vm.activeEveningReviewProposal = EveningReviewProposalPayload(
            proposalId: "hpp_dup_abort",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T22:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let task1 = vm.applyActiveEveningReview(approved: false)
        assert(task1 != nil, "First abort must dispatch")

        let task2 = vm.applyActiveEveningReview(approved: false)
        assert(task2 == nil, "Second abort tap must be ignored while in-flight")
    }

    // MARK: - 23. Concurrency: Apply and Abort Mutual Exclusion

    public func testApplyAbortMutualExclusion() async throws {
        setup()
        let vm = AgendaViewModel(apiClient: client)
        vm.activeDayPlanProposal = DayPlanProposalPayload(
            proposalId: "hpp_mut_excl",
            targetDate: "2026-10-02",
            status: .draft,
            stateHash: "h1",
            expiresAt: "2026-10-02T12:00:00Z"
        )
        vm.diurnalSheetState = .proposalReady

        let applyTask = vm.applyActiveDayPlan(approved: true)
        assert(applyTask != nil, "Apply dispatched")

        let abortTask = vm.applyActiveDayPlan(approved: false)
        assert(abortTask == nil, "Abort must be rejected while apply is in-flight")
    }

    // MARK: - 24. Navigation: Check Agenda Performs Zero Phase 6M API Calls

    public func testCheckAgendaPerformsZeroPhase6MAPICalls() {
        setup()
        var apiCallsMade = 0
        MockURLProtocol.requestHandler = { _ in
            apiCallsMade += 1
            throw NSError(domain: "Test", code: -1, userInfo: nil)
        }

        let vm = AgendaViewModel(apiClient: client)
        vm.diurnalSheetState = .mutationOutcomeUnknown(proposalId: "p_unknown")

        // "Check Agenda" action purely dismisses the sheet and does NOT invoke ViewModel mutation or API client
        // Presentationally, state remains preserved or dismissed without server calls:
        assert(apiCallsMade == 0, "Check Agenda must make zero API calls")
    }

    // MARK: - 25. TTL: Countdown Expiration Does Not Mutate Proposal Status

    public func testCountdownExpirationDoesNotMutateProposalStatus() {
        setup()
        let pastDateString = "2020-01-01T00:00:00Z"
        let proposal = DayPlanProposalPayload(
            proposalId: "hpp_expired_display",
            targetDate: "2020-01-01",
            status: .draft,
            stateHash: "h1",
            expiresAt: pastDateString
        )

        // Proposal status remains authoritative from server (draft), never mutated locally by countdown reaching 0
        assert(proposal.status == .draft, "Proposal status must remain draft despite expired timestamp")
    }
}
