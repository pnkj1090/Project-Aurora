//
//  EventBriefingTests.swift
//  ProjectAuroraTests
//
//  Native XCTest suite for Phase 6O: Event Briefing & Debrief Orchestration.
//

import Foundation

#if canImport(XCTest)
import XCTest
#endif
@testable import ProjectAurora

#if canImport(XCTest)
open class EventBriefingBaseTestCase: XCTestCase {}
#else
open class EventBriefingBaseTestCase {
    public init() {}
    open func setUp() {}
}
#endif

@MainActor
public final class EventBriefingTests: EventBriefingBaseTestCase {
    private var client: AuroraAPIClient!
    private var session: URLSession!
    private var viewModel: EventBriefingViewModel!

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
            credentialProvider: { "test-debrief-bearer-token" }
        )
        viewModel = EventBriefingViewModel(apiClient: client)
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

    // MARK: - 1. CREATE_TASK Payload Decoding

    public func test01_CreateTaskPayloadDecoding() throws {
        let json = """
        {
            "operation_id": "dop_123",
            "operation_type": "create_task",
            "status": "pending",
            "proposed_payload": {
                "exact_title": "Follow up with client",
                "exact_due_at": "2026-10-07T12:00:00Z",
                "exact_duration_minutes": 45,
                "exact_priority": "high",
                "exact_commitment_id": "cmt_999"
            }
        }
        """.data(using: .utf8)!

        let op = try JSONDecoder().decode(DebriefOperation.self, from: json)
        assertEqual(op.operationId, "dop_123")
        assertEqual(op.operationType, .createTask)
        assertEqual(op.status, .pending)

        if case .createTask(let task) = op.proposedPayload {
            assertEqual(task.exactTitle, "Follow up with client")
            assertEqual(task.exactDueAt, "2026-10-07T12:00:00Z")
            assertEqual(task.exactDurationMinutes, 45)
            assertEqual(task.exactPriority, "high")
            assertEqual(task.exactCommitmentId, "cmt_999")
        } else {
            assertTrue(false, "Expected createTask payload")
        }
    }

    // MARK: - 2. CREATE_REMINDER Payload Decoding

    public func test02_CreateReminderPayloadDecoding() throws {
        let json = """
        {
            "operation_id": "dop_456",
            "operation_type": "create_reminder",
            "status": "pending",
            "proposed_payload": {
                "exact_title": "Send meeting notes",
                "exact_scheduled_at": "2026-10-06T18:00:00Z",
                "linked_entity_type": "task",
                "linked_entity_id": "tsk_111"
            }
        }
        """.data(using: .utf8)!

        let op = try JSONDecoder().decode(DebriefOperation.self, from: json)
        assertEqual(op.operationId, "dop_456")
        assertEqual(op.operationType, .createReminder)

        if case .createReminder(let reminder) = op.proposedPayload {
            assertEqual(reminder.exactTitle, "Send meeting notes")
            assertEqual(reminder.exactScheduledAt, "2026-10-06T18:00:00Z")
            assertEqual(reminder.linkedEntityType, .task)
            assertEqual(reminder.linkedEntityId, "tsk_111")
        } else {
            assertTrue(false, "Expected createReminder payload")
        }
    }

    // MARK: - 3. CREATE_MEMORY Payload Decoding

    public func test03_CreateMemoryPayloadDecoding() throws {
        let json = """
        {
            "operation_id": "dop_789",
            "operation_type": "create_memory",
            "status": "pending",
            "proposed_payload": {
                "exact_content": "Project roadmap agreed for Q4",
                "exact_memory_type": "episodic",
                "exact_confidence": 0.95,
                "source_event_id": "evt_001",
                "context_tags": ["meeting", "roadmap"]
            }
        }
        """.data(using: .utf8)!

        let op = try JSONDecoder().decode(DebriefOperation.self, from: json)
        assertEqual(op.operationId, "dop_789")
        assertEqual(op.operationType, .createMemory)

        if case .createMemory(let memory) = op.proposedPayload {
            assertEqual(memory.exactContent, "Project roadmap agreed for Q4")
            assertEqual(memory.exactMemoryType, "episodic")
            assertEqual(memory.exactConfidence, 0.95)
            assertEqual(memory.sourceEventId, "evt_001")
            assertEqual(memory.contextTags, ["meeting", "roadmap"])
        } else {
            assertTrue(false, "Expected createMemory payload")
        }
    }

    // MARK: - 4. Task Operation + Reminder Payload -> DecodingError

    public func test04_TaskOperationWithReminderPayloadThrowsDecodingError() {
        let json = """
        {
            "operation_id": "dop_mismatch_1",
            "operation_type": "create_task",
            "status": "pending",
            "proposed_payload": {
                "exact_title": "Reminder Title",
                "exact_scheduled_at": "2026-10-06T18:00:00Z",
                "linked_entity_type": "none"
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DebriefOperation.self, from: json)) { error in
            assertTrue(error is DecodingError, "Expected DecodingError for mismatched payload")
        }
        #endif
    }

    // MARK: - 5. Reminder Operation + Memory Payload -> DecodingError

    public func test05_ReminderOperationWithMemoryPayloadThrowsDecodingError() {
        let json = """
        {
            "operation_id": "dop_mismatch_2",
            "operation_type": "create_reminder",
            "status": "pending",
            "proposed_payload": {
                "exact_content": "Memory content only",
                "exact_memory_type": "episodic",
                "exact_confidence": 1.0,
                "source_event_id": "evt_001",
                "context_tags": []
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DebriefOperation.self, from: json)) { error in
            assertTrue(error is DecodingError, "Expected DecodingError for mismatched payload")
        }
        #endif
    }

    // MARK: - 6. Memory Operation + Task Payload -> DecodingError

    public func test06_MemoryOperationWithTaskPayloadThrowsDecodingError() {
        let json = """
        {
            "operation_id": "dop_mismatch_3",
            "operation_type": "create_memory",
            "status": "pending",
            "proposed_payload": {
                "exact_title": "Task title only",
                "exact_priority": "high",
                "exact_duration_minutes": 30
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DebriefOperation.self, from: json)) { error in
            assertTrue(error is DecodingError, "Expected DecodingError for mismatched payload")
        }
        #endif
    }

    // MARK: - 7. Malformed Payload -> DecodingError

    public func test07_MalformedPayloadThrowsDecodingError() {
        let json = """
        {
            "operation_id": "dop_malformed",
            "operation_type": "create_task",
            "status": "pending",
            "proposed_payload": {
                "exact_title": 12345,
                "exact_priority": "high"
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DebriefOperation.self, from: json)) { error in
            assertTrue(error is DecodingError, "Expected DecodingError for malformed field types")
        }
        #endif
    }

    // MARK: - 8. Unknown Operation Type -> DecodingError

    public func test08_UnknownOperationTypeThrowsDecodingError() {
        let json = """
        {
            "operation_id": "dop_unknown",
            "operation_type": "create_calendar_event",
            "status": "pending",
            "proposed_payload": {}
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DebriefOperation.self, from: json)) { error in
            assertTrue(error is DecodingError, "Expected DecodingError for unknown operation_type")
        }
        #endif
    }

    // MARK: - 9. Exact DebriefFailureReason Enum Mapping

    public func test09_ExactDebriefFailureReasonEnumMapping() {
        let reasons: [DebriefFailureReason] = [
            .expired,
            .insufficientPermissions,
            .stateChanged,
            .preconditionFailed,
            .operationFailed,
            .calendarNotFound
        ]

        assertEqual(reasons.count, 6)
        assertEqual(DebriefFailureReason.expired.rawValue, "EXPIRED")
        assertEqual(DebriefFailureReason.insufficientPermissions.rawValue, "INSUFFICIENT_PERMISSIONS")
        assertEqual(DebriefFailureReason.stateChanged.rawValue, "STATE_CHANGED")
        assertEqual(DebriefFailureReason.preconditionFailed.rawValue, "PRECONDITION_FAILED")
        assertEqual(DebriefFailureReason.operationFailed.rawValue, "OPERATION_FAILED")
        assertEqual(DebriefFailureReason.calendarNotFound.rawValue, "CALENDAR_NOT_FOUND")
    }

    // MARK: - 10. Candidate DTO Encoding

    public func test10_CandidateDTOEncoding() throws {
        let taskCandidate = DebriefTaskCandidate(
            exactTitle: "Ship Phase 6O",
            exactDueAt: "2026-10-07T00:00:00Z",
            exactDurationMinutes: 60,
            exactPriority: "urgent",
            exactCommitmentId: "cmt_1"
        )
        let reminderCandidate = DebriefReminderCandidate(
            exactTitle: "Ping team",
            exactScheduledAt: "2026-10-06T15:00:00Z",
            linkedEntityType: .commitment,
            linkedEntityId: "cmt_1"
        )
        let memoryCandidate = DebriefMemoryCandidate(
            exactContent: "Approved by leadership",
            exactMemoryType: "episodic",
            exactConfidence: 0.99,
            contextTags: ["review"]
        )

        let request = ProposeDebriefRequestPayload(
            rawNotes: "Meeting transcript here",
            taskCandidates: [taskCandidate],
            reminderCandidates: [reminderCandidate],
            memoryCandidates: [memoryCandidate]
        )

        let encodedData = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: encodedData) as? [String: Any]

        assertNotNil(json)
        assertEqual(json?["raw_notes"] as? String, "Meeting transcript here")
        let tasks = json?["task_candidates"] as? [[String: Any]]
        assertEqual(tasks?.count, 1)
        assertEqual(tasks?[0]["exact_title"] as? String, "Ship Phase 6O")
        assertEqual(tasks?[0]["exact_priority"] as? String, "urgent")

        let reminders = json?["reminder_candidates"] as? [[String: Any]]
        assertEqual(reminders?.count, 1)
        assertEqual(reminders?[0]["exact_title"] as? String, "Ping team")
        assertEqual(reminders?[0]["linked_entity_type"] as? String, "commitment")

        let memories = json?["memory_candidates"] as? [[String: Any]]
        assertEqual(memories?.count, 1)
        assertEqual(memories?[0]["exact_content"] as? String, "Approved by leadership")
    }

    // MARK: - 11. EventBriefing Decoding

    public func test11_EventBriefingDecoding() throws {
        let json = """
        {
            "event_id": "evt_brief_01",
            "user_id": "usr_test",
            "title": "Quarterly Planning",
            "start_time": "2026-10-06T14:00:00Z",
            "end_time": "2026-10-06T15:00:00Z",
            "location": "Boardroom",
            "attendees": [
                {"name": "Alice", "email": "alice@test.com", "status": "accepted"}
            ],
            "related_tasks": [
                {"task_id": "tsk_01", "title": "Prep slides", "status": "completed", "priority": "high"}
            ],
            "active_commitments": [
                {"commitment_id": "cmt_01", "title": "Deliver Q4 plan", "status": "active", "target_date": "2026-10-15"}
            ],
            "relevant_memories": [
                {"memory_id": "mem_01", "content": "Last quarter goals achieved", "type": "episodic"}
            ],
            "summary": "Briefing for Quarterly Planning with 1 attendee."
        }
        """.data(using: .utf8)!

        let briefing = try JSONDecoder().decode(EventBriefing.self, from: json)
        assertEqual(briefing.eventId, "evt_brief_01")
        assertEqual(briefing.title, "Quarterly Planning")
        assertEqual(briefing.attendees.count, 1)
        assertEqual(briefing.attendees[0].name, "Alice")
        assertEqual(briefing.relatedTasks.count, 1)
        assertEqual(briefing.relatedTasks[0].title, "Prep slides")
        assertEqual(briefing.activeCommitments.count, 1)
        assertEqual(briefing.relevantMemories.count, 1)
        assertEqual(briefing.summary, "Briefing for Quarterly Planning with 1 attendee.")
    }

    // MARK: - 12. Apply API Request Path / Body / Headers

    public func test12_ApplyAPIRequestPathBodyHeaders() async throws {
        var interceptedRequest: URLRequest?

        MockURLProtocol.requestHandler = { request in
            interceptedRequest = request
            let respData = """
            {
                "proposal_id": "dbp_apply_01",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "hash123",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        _ = try await client.applyDebriefProposal(proposalId: "dbp_apply_01", approved: true)

        assertNotNil(interceptedRequest)
        let path = interceptedRequest?.url?.path ?? ""
        assertTrue(path.contains("/api/v1/agenda/debrief/proposals/dbp_apply_01/apply"), "Expected apply path, got \(path)")
        assertEqual(interceptedRequest?.httpMethod, "POST")
        assertEqual(interceptedRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        assertTrue(interceptedRequest?.value(forHTTPHeaderField: "Authorization")?.contains("Bearer") == true)

        if let body = interceptedRequest?.httpBody {
            let reqObj = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            assertEqual(reqObj?["approved"] as? Bool, true)
        }
    }

    // MARK: - 13. 409 -> APIError.conflict

    public func test13_Conflict409MapsToAPIErrorConflict() async {
        MockURLProtocol.requestHandler = { request in
            let errData = """
            {
                "detail": {
                    "code": "PROPOSAL_EXPIRED",
                    "message": "Debrief proposal 'dbp_exp' has expired."
                }
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (response, errData)
        }

        do {
            _ = try await client.applyDebriefProposal(proposalId: "dbp_exp", approved: true)
            assertTrue(false, "Expected 409 conflict error")
        } catch let error as APIError {
            if case .conflict(let code, let message) = error {
                assertEqual(code, "PROPOSAL_EXPIRED")
                assertTrue(message.contains("expired"))
            } else {
                assertTrue(false, "Expected .conflict, got \(error)")
            }
        } catch {
            assertTrue(false, "Expected APIError, got \(error)")
        }
    }

    // MARK: - 14. activeMutationTask Retained and Cleared

    public func test14_ActiveMutationTaskRetainedAndCleared() async {
        MockURLProtocol.requestHandler = { request in
            let respData = """
            {
                "proposal_id": "dbp_retain_01",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.applyProposal(proposalId: "dbp_retain_01", approved: true)
        assertNotNil(task)
        assertNotNil(viewModel.activeMutationTask)

        _ = await task?.value

        assertNil(viewModel.activeMutationTask)
        if case .terminalCompleted = viewModel.debriefState {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected terminalCompleted, got \(viewModel.debriefState)")
        }
    }

    // MARK: - 15. activeRecoveryTask Retained and Cleared

    public func test15_ActiveRecoveryTaskRetainedAndCleared() async {
        viewModel.setTestRecoveryContext("dbp_rec_01")

        MockURLProtocol.requestHandler = { request in
            let respData = """
            {
                "proposal_id": "dbp_rec_01",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "dbp_rec_01")
        assertNotNil(task)
        assertNotNil(viewModel.activeRecoveryTask)
        assertTrue(viewModel.isRecoveryInFlight)

        _ = await task?.value

        assertNil(viewModel.activeRecoveryTask)
        assertFalse(viewModel.isRecoveryInFlight)
        assertNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - 16. startFresh Cancels Both

    public func test16_StartFreshCancelsBoth() {
        let initialGen = viewModel.recoveryGeneration

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.1)
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, Data())
        }

        let mutationTask = viewModel.applyProposal(proposalId: "dbp_cancel_test", approved: true)
        assertNotNil(mutationTask)
        assertNotNil(viewModel.activeMutationTask)

        viewModel.setTestRecoveryContext("dbp_cancel_test")
        let recoveryTask = viewModel.resolveUnknownOutcome(proposalId: "dbp_cancel_test")

        viewModel.startFresh()

        assertNil(viewModel.activeMutationTask)
        assertNil(viewModel.activeRecoveryTask)
        assertTrue(mutationTask?.isCancelled == true)
        assertTrue(recoveryTask?.isCancelled == true)
        assertTrue(viewModel.recoveryGeneration > initialGen)
        assertFalse(viewModel.isRecoveryInFlight)
        assertNil(viewModel.retainedRecoveryContext)
        assertEqual(viewModel.briefingState, .idle)
        assertEqual(viewModel.debriefState, .idle)
    }

    // MARK: - 17. Transport Failure -> mutationOutcomeUnknown

    public func test17_TransportFailureProducesMutationOutcomeUnknown() async {
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: [NSLocalizedDescriptionKey: "The connection timed out."])
        }

        let task = viewModel.applyProposal(proposalId: "dbp_timeout_test", approved: true)
        _ = await task?.value

        if case .mutationOutcomeUnknown(let pid, let reason) = viewModel.debriefState {
            assertEqual(pid, "dbp_timeout_test")
            assertTrue(reason.contains("timed out") || reason.contains("Network"))
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown, got: \(viewModel.debriefState)")
        }
        assertEqual(viewModel.retainedRecoveryContext, "dbp_timeout_test")
    }

    // MARK: - 18. Recovery GET -> Authoritative State

    public func test18_RecoveryGETReturnsAuthoritativeState() async {
        viewModel.setTestRecoveryContext("dbp_rec_auth")
        viewModel.setTestDebriefState(.mutationOutcomeUnknown(proposalId: "dbp_rec_auth", reason: "Connection lost"))

        MockURLProtocol.requestHandler = { request in
            let respData = """
            {
                "proposal_id": "dbp_rec_auth",
                "event_id": "evt_auth",
                "user_id": "usr_auth",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "hash_auth",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "dbp_rec_auth")
        _ = await task?.value

        if case .terminalCompleted(let prop) = viewModel.debriefState {
            assertEqual(prop.proposalId, "dbp_rec_auth")
            assertEqual(prop.status, .completed)
        } else {
            assertTrue(false, "Expected terminalCompleted, got: \(viewModel.debriefState)")
        }
        assertNil(viewModel.retainedRecoveryContext)
    }

    // MARK: - 19. Local Cancellation Does Not Imply Server Rollback

    public func test19_LocalCancellationDoesNotImplyServerRollback() async {
        var recordedRequests: [URLRequest] = []

        MockURLProtocol.requestHandler = { request in
            recordedRequests.append(request)
            Thread.sleep(forTimeInterval: 0.05)
            let respData = """
            {
                "proposal_id": "dbp_no_rollback",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.applyProposal(proposalId: "dbp_no_rollback", approved: true)
        task?.cancel()
        _ = await task?.value

        // Verify that NO abort request was sent upon cancellation
        let abortRequests = recordedRequests.filter { $0.url?.path.contains("/abort") == true }
        assertEqual(abortRequests.count, 0)
    }

    // MARK: - 20. Stale Recovery Response Rejected by Generation

    public func test20_StaleRecoveryResponseRejectedByGeneration() async {
        viewModel.setTestRecoveryContext("dbp_stale")

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.05)
            let respData = """
            {
                "proposal_id": "dbp_stale",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "dbp_stale")
        // Stale generation increment
        viewModel.setTestRecoveryGeneration(viewModel.recoveryGeneration + 10)

        _ = await task?.value

        // Because generation was incremented, the delayed response must NOT have committed to .terminalCompleted
        if case .terminalCompleted = viewModel.debriefState {
            assertTrue(false, "Stale response should not have committed to terminalCompleted")
        } else {
            assertTrue(true)
        }
    }

    // MARK: - 21. Cancelled Recovery Cannot Mutate UI State

    public func test21_CancelledRecoveryCannotMutateUIState() async {
        viewModel.setTestRecoveryContext("dbp_cancel_rec")
        viewModel.setTestDebriefState(.mutationOutcomeUnknown(proposalId: "dbp_cancel_rec", reason: "Lost connection"))

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.05)
            let respData = """
            {
                "proposal_id": "dbp_cancel_rec",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        let task = viewModel.resolveUnknownOutcome(proposalId: "dbp_cancel_rec")
        task?.cancel()
        _ = await task?.value

        // Cancelled task should not have updated state to .terminalCompleted
        if case .terminalCompleted = viewModel.debriefState {
            assertTrue(false, "Cancelled recovery should not update debriefState to terminalCompleted")
        } else {
            assertTrue(true)
        }
    }

    // MARK: - 22. Sheet Dismissal Does Not Destroy Lifecycle

    public func test22_SheetDismissalDoesNotDestroyLifecycle() async {
        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.05)
            let respData = """
            {
                "proposal_id": "dbp_lifecycle_01",
                "event_id": "evt_01",
                "user_id": "usr_01",
                "created_at": "2026-10-06T00:00:00Z",
                "expires_at": "2026-10-06T00:30:00Z",
                "status": "completed",
                "state_hash": "h",
                "ordered_operations": []
            }
            """.data(using: .utf8)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, respData)
        }

        // Parent view holds the ViewModel
        let parentViewModel = viewModel!

        // Child sheet starts mutation
        let runningTask = parentViewModel.applyProposal(proposalId: "dbp_lifecycle_01", approved: true)

        // Child sheet dismissed (local view deallocated, but parent retains viewModel)
        // Ensure running task completes under parent ownership
        _ = await runningTask?.value

        if case .terminalCompleted(let prop) = parentViewModel.debriefState {
            assertEqual(prop.proposalId, "dbp_lifecycle_01")
        } else {
            assertTrue(false, "Expected terminalCompleted, got \(parentViewModel.debriefState)")
        }
    }
}
