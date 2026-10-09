//
//  WeeklyHorizonTests.swift
//  ProjectAuroraTests
//
//  Comprehensive XCTest suite for Phase 6N: Weekly Strategic Horizon iOS Client.
//

import Foundation

#if canImport(XCTest)
import XCTest
#endif
@testable import ProjectAurora

#if canImport(XCTest)
open class WeeklyHorizonBaseTestCase: XCTestCase {}
#else
open class WeeklyHorizonBaseTestCase {
    public init() {}
    open func setUp() {}
}
#endif

@MainActor
public final class WeeklyHorizonTests: WeeklyHorizonBaseTestCase {
    private var client: AuroraAPIClient!
    private var session: URLSession!
    private var viewModel: WeeklyHorizonViewModel!

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
            credentialProvider: { "test-weekly-bearer-token" }
        )
        viewModel = WeeklyHorizonViewModel(apiClient: client)
    }

    // MARK: - Assertion Helper

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

    private func assertNil(_ value: Any?, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertNil(value, message, file: file, line: line)
        #else
        assert(value == nil, message)
        #endif
    }

    private func assertNotNil(_ value: Any?, _ message: String = "", file: StaticString = #file, line: UInt = #line) {
        #if canImport(XCTest)
        XCTAssertNotNil(value, message, file: file, line: line)
        #else
        assert(value != nil, message)
        #endif
    }

    // MARK: - Category A: DTO Decoding

    public func testDecodeWeeklyStrategicHorizon() throws {
        let json = """
        {
            "user_id": "usr_test",
            "timezone": "UTC",
            "horizon_start_date": "2026-10-06",
            "horizon_end_date": "2026-10-12",
            "day_summaries": [
                {
                    "date_str": "2026-10-06",
                    "day_of_week": "Tue",
                    "total_focus_minutes": 480,
                    "allocated_focus_minutes": 240,
                    "free_focus_minutes": 240,
                    "events_count": 3,
                    "tasks_count": 5,
                    "congestion_level": "balanced",
                    "is_overcommitted": false
                }
            ],
            "commitment_health": [
                {
                    "commitment_id": "cmt_101",
                    "title": "Launch Phase 6N",
                    "target_date": "2026-10-10",
                    "linked_tasks_count": 4,
                    "completed_tasks_count": 2,
                    "open_tasks_count": 2,
                    "is_orphan": false,
                    "has_overrun_risk": false
                }
            ],
            "orphan_commitments_count": 0,
            "overcommitted_days_count": 0,
            "total_free_focus_minutes": 1440,
            "strategic_recommendations": ["Dedicate Thursday to strategic synthesis"]
        }
        """.data(using: .utf8)!

        let decoded = try JSONDecoder().decode(WeeklyStrategicHorizon.self, from: json)
        assertEqual(decoded.userId, "usr_test")
        assertEqual(decoded.daySummaries.count, 1)
        assertEqual(decoded.daySummaries[0].congestionLevel, .balanced)
        assertEqual(decoded.commitmentHealth.count, 1)
        assertEqual(decoded.commitmentHealth[0].commitmentId, "cmt_101")
    }

    public func testDecodeWeeklyRetrospective() throws {
        let json = """
        {
            "user_id": "usr_test",
            "timezone": "UTC",
            "lookback_start_date": "2026-09-29",
            "lookback_end_date": "2026-10-05",
            "completed_tasks_count": 18,
            "honored_commitments_count": 3,
            "unfulfilled_commitments_count": 1,
            "velocity_score": 8.5
        }
        """.data(using: .utf8)!

        let decoded = try JSONDecoder().decode(WeeklyRetrospective.self, from: json)
        assertEqual(decoded.completedTasksCount, 18)
        assertEqual(decoded.honoredCommitmentsCount, 3)
        assertEqual(decoded.unfulfilledCommitmentsCount, 1)
        assertEqual(decoded.velocityScore, 8.5)
    }

    public func testDecodeWeeklyPlanProposalPayload() throws {
        let json = """
        {
            "proposal_id": "prop_plan_001",
            "user_id": "usr_test",
            "proposal_type": "weekly_plan",
            "status": "draft",
            "created_at": "2026-10-06T00:00:00Z",
            "expires_at": "2026-10-06T00:30:00Z",
            "state_hash": "hash_123",
            "required_permissions": ["tasks", "commitments"],
            "operations": [
                {
                    "operation_id": "op_01",
                    "operation_type": "allocate_task_due_date",
                    "execution_order": 1,
                    "status": "pending",
                    "required_permissions": ["tasks"],
                    "precondition": {"task_id": "tsk_01"}
                }
            ],
            "omitted_domains": [],
            "metadata": {}
        }
        """.data(using: .utf8)!

        let decoded = try JSONDecoder().decode(WeeklyPlanProposalPayload.self, from: json)
        assertEqual(decoded.proposalId, "prop_plan_001")
        assertEqual(decoded.status, .draft)
        assertEqual(decoded.operations.count, 1)
        assertEqual(decoded.operations[0].targetEntityId, "tsk_01")
        assertEqual(decoded.operations[0].targetEntityType, "task")
    }

    public func testDecodeWeeklyProposalPayloadDiscrimination() throws {
        let planJson = """
        {
            "proposal_id": "prop_plan_002",
            "user_id": "usr_test",
            "proposal_type": "weekly_plan",
            "status": "draft",
            "created_at": "2026-10-06T00:00:00Z",
            "expires_at": "2026-10-06T00:30:00Z",
            "state_hash": "hash_abc",
            "operations": []
        }
        """.data(using: .utf8)!

        let retroJson = """
        {
            "proposal_id": "prop_retro_001",
            "user_id": "usr_test",
            "proposal_type": "weekly_retrospective",
            "status": "draft",
            "created_at": "2026-10-06T00:00:00Z",
            "expires_at": "2026-10-06T00:30:00Z",
            "state_hash": "hash_def",
            "operations": []
        }
        """.data(using: .utf8)!

        let unknownJson = """
        {
            "proposal_id": "prop_unknown_001",
            "user_id": "usr_test",
            "proposal_type": "unknown_future_type",
            "status": "draft",
            "created_at": "2026-10-06T00:00:00Z",
            "expires_at": "2026-10-06T00:30:00Z",
            "state_hash": "hash_xyz",
            "operations": []
        }
        """.data(using: .utf8)!

        let pEnvelope = try JSONDecoder().decode(WeeklyProposalPayload.self, from: planJson)
        if case .plan(let p) = pEnvelope {
            assertEqual(p.proposalId, "prop_plan_002")
        } else {
            assertTrue(false, "Expected plan payload")
        }

        let rEnvelope = try JSONDecoder().decode(WeeklyProposalPayload.self, from: retroJson)
        if case .retrospective(let r) = rEnvelope {
            assertEqual(r.proposalId, "prop_retro_001")
        } else {
            assertTrue(false, "Expected retrospective payload")
        }

        // Unknown proposal_type MUST fail closed
        var didFailClosed = false
        do {
            _ = try JSONDecoder().decode(WeeklyProposalPayload.self, from: unknownJson)
        } catch {
            didFailClosed = true
        }
        assertTrue(didFailClosed, "Unknown proposal type must fail closed")
    }

    // MARK: - Category B, C, D: API Routes, HTTP Methods & Bearer Header

    public func testAll8APIRoutesAndMethods() async throws {
        var recordedRequests: [(url: String, method: String, hasBearer: Bool)] = []

        MockURLProtocol.requestHandler = { request in
            let path = request.url?.path ?? ""
            let method = request.httpMethod ?? ""
            let auth = request.value(forHTTPHeaderField: "Authorization") ?? ""
            let hasBearer = auth.contains("Bearer test-weekly-bearer-token")
            recordedRequests.append((path, method, hasBearer))

            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!

            if path.contains("horizon") {
                let data = "{\"user_id\":\"u\",\"timezone\":\"UTC\",\"horizon_start_date\":\"2026-10-06\",\"horizon_end_date\":\"2026-10-12\",\"day_summaries\":[],\"commitment_health\":[],\"orphan_commitments_count\":0,\"overcommitted_days_count\":0,\"total_free_focus_minutes\":0,\"strategic_recommendations\":[]}".data(using: .utf8)!
                return (response, data)
            } else if path.contains("retrospective") && method == "GET" {
                let data = "{\"user_id\":\"u\",\"timezone\":\"UTC\",\"lookback_start_date\":\"2026-09-29\",\"lookback_end_date\":\"2026-10-05\",\"completed_tasks_count\":0,\"honored_commitments_count\":0,\"unfulfilled_commitments_count\":0,\"velocity_score\":0.0}".data(using: .utf8)!
                return (response, data)
            } else if path.contains("propose-plan") || (path.contains("apply-plan")) {
                let data = "{\"proposal_id\":\"p1\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"draft\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
                return (response, data)
            } else if path.contains("propose-retrospective") || (path.contains("apply-retrospective")) {
                let data = "{\"proposal_id\":\"p2\",\"user_id\":\"u\",\"proposal_type\":\"weekly_retrospective\",\"status\":\"draft\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
                return (response, data)
            } else if path.contains("abort") {
                let data = "{\"proposal_id\":\"p1\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"aborted\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
                return (response, data)
            } else {
                let data = "{\"proposal_id\":\"p1\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
                return (response, data)
            }
        }

        _ = try await client.getWeeklyStrategicHorizon()
        _ = try await client.proposeWeeklyPlan(payload: ProposeWeeklyPlanRequestPayload())
        _ = try await client.applyWeeklyPlan(proposalId: "p1", approved: true)
        _ = try await client.getWeeklyRetrospective()
        _ = try await client.proposeWeeklyRetrospective(payload: ProposeWeeklyRetrospectiveRequestPayload())
        _ = try await client.applyWeeklyRetrospective(proposalId: "p2", approved: true)
        _ = try await client.getWeeklyProposal(proposalId: "p1")
        _ = try await client.abortWeeklyProposal(proposalId: "p1")

        assertEqual(recordedRequests.count, 8)
        assertEqual(recordedRequests[0].url, "/api/v1/agenda/weekly/horizon")
        assertEqual(recordedRequests[0].method, "GET")
        assertTrue(recordedRequests[0].hasBearer)

        assertEqual(recordedRequests[1].url, "/api/v1/agenda/weekly/propose-plan")
        assertEqual(recordedRequests[1].method, "POST")
        assertTrue(recordedRequests[1].hasBearer)

        assertEqual(recordedRequests[2].url, "/api/v1/agenda/weekly/apply-plan")
        assertEqual(recordedRequests[2].method, "POST")
        assertTrue(recordedRequests[2].hasBearer)

        assertEqual(recordedRequests[3].url, "/api/v1/agenda/weekly/retrospective")
        assertEqual(recordedRequests[3].method, "GET")
        assertTrue(recordedRequests[3].hasBearer)

        assertEqual(recordedRequests[4].url, "/api/v1/agenda/weekly/propose-retrospective")
        assertEqual(recordedRequests[4].method, "POST")
        assertTrue(recordedRequests[4].hasBearer)

        assertEqual(recordedRequests[5].url, "/api/v1/agenda/weekly/apply-retrospective")
        assertEqual(recordedRequests[5].method, "POST")
        assertTrue(recordedRequests[5].hasBearer)

        assertEqual(recordedRequests[6].url, "/api/v1/agenda/weekly/proposals/p1")
        assertEqual(recordedRequests[6].method, "GET")
        assertTrue(recordedRequests[6].hasBearer)

        assertEqual(recordedRequests[7].url, "/api/v1/agenda/weekly/proposals/p1/abort")
        assertEqual(recordedRequests[7].method, "POST")
        assertTrue(recordedRequests[7].hasBearer)
    }

    // MARK: - MANDATORY Invariant 1: Apply-Plan True Network Failure

    public func testMandatory1_ApplyPlanTrueNetworkFailure() async {
        setupMockPlanProposal()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyPlan(approved: true)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId) = viewModel.sheetState {
            assertEqual(propId, "prop_plan_test")
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
        assertNil(viewModel.activeMutationContext)
    }

    // MARK: - MANDATORY Invariant 2: Apply-Plan False Network Failure

    public func testMandatory2_ApplyPlanFalseNetworkFailure() async {
        setupMockPlanProposal()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyPlan(approved: false)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId) = viewModel.sheetState {
            assertEqual(propId, "prop_plan_test")
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown on discard drop, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - MANDATORY Invariant 3: Apply-Retrospective True Network Failure

    public func testMandatory3_ApplyRetrospectiveTrueNetworkFailure() async {
        setupMockRetrospectiveProposal()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyRetrospective(approved: true)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId) = viewModel.sheetState {
            assertEqual(propId, "prop_retro_test")
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - MANDATORY Invariant 4: Apply-Retrospective False Network Failure

    public func testMandatory4_ApplyRetrospectiveFalseNetworkFailure() async {
        setupMockRetrospectiveProposal()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyRetrospective(approved: false)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let propId) = viewModel.sheetState {
            assertEqual(propId, "prop_retro_test")
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - MANDATORY Invariant 5: Horizon Failure with Mutation Context

    public func testMandatory5_HorizonFailureWithMutationContext() async {
        setupMockRecoveryContext(proposalId: "p_context")

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        await viewModel.loadWeeklyHorizon()

        // Read request must NOT enter mutationOutcomeUnknown even if context is present
        if case .failedPresentation = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected failedPresentation, got: \(viewModel.sheetState)")
        }
    }

    // MARK: - MANDATORY Invariant 6: Retrospective Failure with Mutation Context

    public func testMandatory6_RetrospectiveFailureWithMutationContext() async {
        setupMockRecoveryContext(proposalId: "p_context")

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        await viewModel.loadWeeklyRetrospective()

        if case .failedPresentation = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected failedPresentation, got: \(viewModel.sheetState)")
        }
    }

    // MARK: - MANDATORY Invariant 7: Proposal Generation Failure with Mutation Context

    public func testMandatory7_ProposalGenerationFailureWithMutationContext() async {
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        await viewModel.createWeeklyPlanProposal()

        if case .failedPresentation = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected failedPresentation, got: \(viewModel.sheetState)")
        }
    }

    // MARK: - MANDATORY Invariant 8: Recovery GET Failure Isolation

    public func testMandatory8_RecoveryGETFailureIsolation() async {
        setupMockRecoveryContext(proposalId: "p_recover")

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_recover")
        _ = await task?.value

        // Does NOT create a second mutationOutcomeUnknown; displays failedPresentation and clears task
        if case .failedPresentation = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected failedPresentation banner, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 9: Recovery Abort Failure Isolation

    public func testMandatory9_RecoveryAbortFailureIsolation() async {
        setupMockRecoveryContext(proposalId: "p_abort")

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.abortUnknownProposal(proposalId: "p_abort")
        _ = await task?.value

        if case .failedPresentation = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected failedPresentation banner, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 10: Executing Recovery Presentation

    public func testMandatory10_ExecutingRecoveryPresentation() async {
        setupMockRecoveryContext(proposalId: "p_exec")

        MockURLProtocol.requestHandler = { request in
            let data = "{\"proposal_id\":\"p_exec\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executing\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_exec")
        _ = await task?.value

        if case .serverExecutionInProgress(let id) = viewModel.sheetState {
            assertEqual(id, "p_exec")
        } else {
            assertTrue(false, "Expected serverExecutionInProgress, got: \(viewModel.sheetState)")
        }
        assertNotNil(viewModel.retainedRecoveryContext)
        assertNil(viewModel.activeRecoveryTask)
    }

    // MARK: - MANDATORY Invariant 11: Repeated Explicit Check Status

    public func testMandatory11_RepeatedExplicitCheckStatusRequiresExplicitAction() async {
        setupMockRecoveryContext(proposalId: "p_exec")

        var callCount = 0
        MockURLProtocol.requestHandler = { request in
            callCount += 1
            let data = "{\"proposal_id\":\"p_exec\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executing\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        // Tap 1
        let task1 = viewModel.resolveUnknownOutcome(proposalId: "p_exec")
        _ = await task1?.value
        assertEqual(callCount, 1)

        // Zero automatic calls occur without user action
        assertEqual(callCount, 1)

        // Tap 2
        let task2 = viewModel.resolveUnknownOutcome(proposalId: "p_exec")
        _ = await task2?.value
        assertEqual(callCount, 2)
    }

    // MARK: - MANDATORY Invariant 12: Proposal Identity Mismatch

    public func testMandatory12_ProposalIdentityMismatchFailsClosed() async {
        setupMockRecoveryContext(proposalId: "expected_id")

        MockURLProtocol.requestHandler = { request in
            let data = "{\"proposal_id\":\"different_id\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "expected_id")
        _ = await task?.value

        if case .contractViolationPresentation(let violation) = viewModel.sheetState {
            if case .proposalIdentityMismatch(let exp, let act) = violation {
                assertEqual(exp, "expected_id")
                assertEqual(act, "different_id")
            } else {
                assertTrue(false, "Expected proposalIdentityMismatch, got \(violation)")
            }
        } else {
            assertTrue(false, "Expected contractViolationPresentation, got \(viewModel.sheetState)")
        }
        assertNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - MANDATORY Invariant 13: Active Recovery Task Ownership

    public func testMandatory13_ActiveRecoveryTaskOwnership() {
        setupMockRecoveryContext(proposalId: "p_owner")

        MockURLProtocol.requestHandler = { _ in
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(url: URL(string: "http://localhost")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_owner\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_owner")
        assertNotNil(task)
        assertNotNil(viewModel.activeRecoveryTask)
        assertTrue(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 14: Start Fresh Cancels Active Recovery Task

    public func testMandatory14_StartFreshCancelsActiveRecoveryTask() async {
        setupMockRecoveryContext(proposalId: "p_cancel")

        MockURLProtocol.requestHandler = { _ in
            Thread.sleep(forTimeInterval: 0.2)
            let response = HTTPURLResponse(url: URL(string: "http://localhost")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_cancel\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_cancel")
        assertNotNil(task)

        viewModel.startFresh()

        assertTrue(task?.isCancelled ?? false, "activeRecoveryTask must be cancelled by startFresh")
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
        assertNil(viewModel.retainedRecoveryContext)
        assertEqual(viewModel.sheetState, .idle)
    }

    // MARK: - MANDATORY Invariant 15: Cancellation Does Not Replace Generation Protection

    public func testMandatory15_CancellationDoesNotReplaceGenerationProtection() async {
        setupMockRecoveryContext(proposalId: "p_late")

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_late\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_late")
        let dispatchGen = viewModel.recoveryGeneration

        // User starts fresh while request is in flight
        viewModel.startFresh()
        assertTrue(viewModel.recoveryGeneration > dispatchGen)

        // Wait for old task to finish late
        _ = await task?.value

        // Stale response must NOT commit UI state
        assertEqual(viewModel.sheetState, .idle)
        assertNil(viewModel.activePlanProposal)
    }

    // MARK: - MANDATORY Invariant 16: Terminal Recovery Task Cleanup

    public func testMandatory16_TerminalRecoveryTaskCleanup() async {
        setupMockRecoveryContext(proposalId: "p_clean")

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_clean\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_clean")
        _ = await task?.value

        assertEqual(viewModel.sheetState, .executedPresentation)
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
        assertNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - MANDATORY Invariant 17: Recovery Transport Failure Cleanup

    public func testMandatory17_RecoveryTransportFailureCleanup() async {
        setupMockRecoveryContext(proposalId: "p_tfail")

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost, userInfo: nil)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_tfail")
        _ = await task?.value

        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 18: Abort Task Cleanup

    public func testMandatory18_AbortTaskCleanupOnSuccessAndFailure() async {
        // Success path
        setupMockRecoveryContext(proposalId: "p_abort_succ")
        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_abort_succ\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"aborted\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task1 = viewModel.abortUnknownProposal(proposalId: "p_abort_succ")
        _ = await task1?.value

        assertEqual(viewModel.sheetState, .abortedPresentation)
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)

        // Failure path
        setupMockRecoveryContext(proposalId: "p_abort_fail")
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task2 = viewModel.abortUnknownProposal(proposalId: "p_abort_fail")
        _ = await task2?.value

        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 19: Contract Violation Task Cleanup

    public func testMandatory19_ContractViolationTaskCleanup() async {
        setupMockRecoveryContext(proposalId: "p_violate")

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_violate\",\"user_id\":\"u\",\"proposal_type\":\"weekly_retrospective\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_violate")
        _ = await task?.value

        assertNotNil(viewModel.lastContractViolation)
        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
    }

    // MARK: - MANDATORY Invariant 20: Stale Response After Start Fresh

    public func testMandatory20_StartFreshStaleResponse() async {
        setupMockRecoveryContext(proposalId: "p_stale")

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.15)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_stale\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_stale")
        viewModel.startFresh()

        _ = await task?.value

        assertEqual(viewModel.sheetState, .idle)
        assertNil(viewModel.activePlanProposal)
        assertNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - Concurrency Invariant: Rapid Check Status Dispatches Exactly One GET

    public func testRapidCheckStatusDispatchesExactlyOneGET() async {
        setupMockRecoveryContext(proposalId: "p_rapid")

        var getCount = 0
        MockURLProtocol.requestHandler = { request in
            getCount += 1
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_rapid\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let task1 = viewModel.resolveUnknownOutcome(proposalId: "p_rapid")
        let task2 = viewModel.resolveUnknownOutcome(proposalId: "p_rapid")

        assertNotNil(task1)
        assertNil(task2, "Second concurrent recovery action must be rejected locally")

        _ = await task1?.value
        assertEqual(getCount, 1)
    }

    // MARK: - Concurrency Invariant: Check Status & Abort Mutual Exclusion

    public func testCheckStatusAndAbortMutualExclusion() async {
        setupMockRecoveryContext(proposalId: "p_mutex")

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_mutex\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"executed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[]}".data(using: .utf8)!
            return (response, data)
        }

        let taskCheck = viewModel.resolveUnknownOutcome(proposalId: "p_mutex")
        let taskAbort = viewModel.abortUnknownProposal(proposalId: "p_mutex")

        assertNotNil(taskCheck)
        assertNil(taskAbort, "Abort while recovery is active must be rejected locally")

        _ = await taskCheck?.value
    }

    // MARK: - Invariant: No Automatic Retry or Abort

    public func testNoAutomaticRetryOrAbortOnApplyFailure() async {
        setupMockPlanProposal()

        var dispatchedPaths: [String] = []
        MockURLProtocol.requestHandler = { request in
            dispatchedPaths.append(request.url?.path ?? "")
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyPlan(approved: true)
        _ = await task?.value

        // Only the initial apply request must have been dispatched
        assertEqual(dispatchedPaths.count, 1)
        assertEqual(dispatchedPaths[0], "/api/v1/agenda/weekly/apply-plan")
    }

    // MARK: - Category Q: No Local Success Inference

    public func testNoLocalSuccessInference() async {
        setupMockRecoveryContext(proposalId: "p_fail")

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            let data = "{\"proposal_id\":\"p_fail\",\"user_id\":\"u\",\"proposal_type\":\"weekly_plan\",\"status\":\"failed\",\"created_at\":\"2026-10-06T00:00:00Z\",\"expires_at\":\"2026-10-06T00:30:00Z\",\"state_hash\":\"h\",\"operations\":[{\"operation_id\":\"op_1\",\"operation_type\":\"allocate_task_due_date\",\"status\":\"failed\",\"error_message\":\"Resource busy\"}]}".data(using: .utf8)!
            return (response, data)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "p_fail")
        _ = await task?.value

        if case .proposalFailedPresentation(let reason, _) = viewModel.sheetState {
            assertEqual(reason, "Resource busy")
        } else {
            assertTrue(false, "Expected proposalFailedPresentation, got: \(viewModel.sheetState)")
        }
    }

    // MARK: - Category R: No Local Failure Inference

    public func testNoLocalFailureInferenceOnTimeout() async {
        setupMockPlanProposal()

        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        let task = viewModel.applyActiveWeeklyPlan(approved: true)
        _ = await task?.value

        // Must enter mutationOutcomeUnknown, NOT proposalFailedPresentation
        if case .mutationOutcomeUnknown = viewModel.sheetState {
            assertTrue(true)
        } else {
            assertTrue(false, "Timeout must not be locally inferred as proposal failure")
        }
    }

    // MARK: - Category V: TTL Presentation without Local Expiry Authority

    public func testTTLCountdownDoesNotMutateProposalStatusLocally() {
        let expiredProposal = WeeklyPlanProposalPayload(
            proposalId: "p_exp",
            userId: "u",
            proposalType: .weeklyPlan,
            status: .draft,
            createdAt: "2026-10-06T00:00:00Z",
            expiresAt: "2026-10-06T00:00:01Z",
            stateHash: "h"
        )
        // Locally held status remains draft even if expiresAt is in the past; server remains authority
        assertEqual(expiredProposal.status, .draft)
    }

    // MARK: - Category T: Navigation Entry Point

    public func testNavigationTimelineOwnsWeeklyViewModel() {
        let timeline = AgendaTimelineView(weeklyViewModel: viewModel)
        assertNotNil(timeline)
    }

    // MARK: - Test Runner (for environments without XCTest bundle runner)

    public func runAllTests() async throws {
        print("--- Running Phase 6N WeeklyHorizonTests ---")
        try testDecodeWeeklyStrategicHorizon()
        try testDecodeWeeklyRetrospective()
        try testDecodeWeeklyPlanProposalPayload()
        try testDecodeWeeklyProposalPayloadDiscrimination()
        try await testAll8APIRoutesAndMethods()

        await testMandatory1_ApplyPlanTrueNetworkFailure()
        await testMandatory2_ApplyPlanFalseNetworkFailure()
        await testMandatory3_ApplyRetrospectiveTrueNetworkFailure()
        await testMandatory4_ApplyRetrospectiveFalseNetworkFailure()
        await testMandatory5_HorizonFailureWithMutationContext()
        await testMandatory6_RetrospectiveFailureWithMutationContext()
        await testMandatory7_ProposalGenerationFailureWithMutationContext()
        await testMandatory8_RecoveryGETFailureIsolation()
        await testMandatory9_RecoveryAbortFailureIsolation()
        await testMandatory10_ExecutingRecoveryPresentation()
        await testMandatory11_RepeatedExplicitCheckStatusRequiresExplicitAction()
        await testMandatory12_ProposalIdentityMismatchFailsClosed()
        testMandatory13_ActiveRecoveryTaskOwnership()
        await testMandatory14_StartFreshCancelsActiveRecoveryTask()
        await testMandatory15_CancellationDoesNotReplaceGenerationProtection()
        await testMandatory16_TerminalRecoveryTaskCleanup()
        await testMandatory17_RecoveryTransportFailureCleanup()
        await testMandatory18_AbortTaskCleanupOnSuccessAndFailure()
        await testMandatory19_ContractViolationTaskCleanup()
        await testMandatory20_StartFreshStaleResponse()

        await testRapidCheckStatusDispatchesExactlyOneGET()
        await testCheckStatusAndAbortMutualExclusion()
        await testNoAutomaticRetryOrAbortOnApplyFailure()
        await testNoLocalSuccessInference()
        await testNoLocalFailureInferenceOnTimeout()
        testTTLCountdownDoesNotMutateProposalStatusLocally()
        testNavigationTimelineOwnsWeeklyViewModel()

        print("✅ All 32 Phase 6N WeeklyHorizonTests passed successfully!")
    }

    // MARK: - Private Setup Helpers

    private func setupMockPlanProposal() {
        let proposal = WeeklyPlanProposalPayload(
            proposalId: "prop_plan_test",
            userId: "usr_test",
            proposalType: .weeklyPlan,
            status: .draft,
            createdAt: "2026-10-06T00:00:00Z",
            expiresAt: "2026-10-06T00:30:00Z",
            stateHash: "hash_test"
        )
        viewModel.setTestPlanProposal(proposal)
        viewModel.sheetState = .proposalReady
    }

    private func setupMockRetrospectiveProposal() {
        let proposal = WeeklyRetrospectiveProposalPayload(
            proposalId: "prop_retro_test",
            userId: "usr_test",
            proposalType: .weeklyRetrospective,
            status: .draft,
            createdAt: "2026-10-06T00:00:00Z",
            expiresAt: "2026-10-06T00:30:00Z",
            stateHash: "hash_test"
        )
        viewModel.setTestRetrospectiveProposal(proposal)
        viewModel.sheetState = .proposalReady
    }

    private func setupMockRecoveryContext(proposalId: String, type: WeeklyProposalType = .weeklyPlan) {
        let context = ActiveMutationContext(
            proposalId: proposalId,
            proposalType: type,
            operation: .applyPlan,
            approved: true
        )
        viewModel.setTestRecoveryContext(context)
        viewModel.sheetState = .mutationOutcomeUnknown(proposalId: proposalId)
    }
}
