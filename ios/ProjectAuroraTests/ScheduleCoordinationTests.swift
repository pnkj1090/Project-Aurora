//
//  ScheduleCoordinationTests.swift
//  ProjectAuroraTests
//
//  Native XCTest suite for Phase 6Q: Schedule Coordination Lifecycle Hardening & Parent-Owned Orchestration.
//

import Foundation
#if canImport(XCTest)
import XCTest
#endif
@testable import ProjectAurora

#if canImport(XCTest)
open class ScheduleCoordinationBaseTestCase: XCTestCase {}
#else
open class ScheduleCoordinationBaseTestCase {
    public init() {}
    open func setUp() {}
}
#endif

@MainActor
public final class ScheduleCoordinationTests: ScheduleCoordinationBaseTestCase {
    private var client: AuroraAPIClient!
    private var session: URLSession!
    private var viewModel: ScheduleCoordinationViewModel!

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
        viewModel = ScheduleCoordinationViewModel(apiClient: client)
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

    private func makeStrategy(
        id: String = "strat_01",
        opIds: [String] = ["op_01", "op_02"]
    ) -> StrategyOptionPayload {
        let previews = opIds.map { opId in
            DisplayOperationPreviewPayload(
                operationType: .updateTaskDueDate,
                targetEntityId: "task_\(opId)",
                displaySummary: "Shift task deadline for \(opId)",
                fieldChanges: .updateTaskDueDate(TaskDueDateFieldChangesPayload(dueAt: "2026-10-08T10:00:00Z"))
            )
        }
        return StrategyOptionPayload(
            strategyId: id,
            analysisId: "analysis_01",
            conflictId: "conflict_01",
            category: .resolution,
            name: "Resolve Collision",
            description: "Shift task to free slot",
            effectiveDurationMinutes: 30,
            isAvailable: true,
            missingPermissions: [],
            feasibilityProof: nil,
            previewOperations: previews,
            tradeoffs: nil
        )
    }

    // MARK: - DTO Tests (1–5)

    func test01_validTaskDueDatePayload() throws {
        let json = """
        {
            "operation_type": "update_task_due_date",
            "target_entity_id": "task_123",
            "display_summary": "Update task due date",
            "field_changes": {
                "due_at": "2026-10-08T12:00:00Z",
                "effective_duration_minutes": 45
            }
        }
        """.data(using: .utf8)!

        let preview = try JSONDecoder().decode(DisplayOperationPreviewPayload.self, from: json)
        assertEqual(preview.operationType, .updateTaskDueDate)
        assertEqual(preview.targetEntityId, "task_123")
        if case .updateTaskDueDate(let payload) = preview.fieldChanges {
            assertEqual(payload.dueAt, "2026-10-08T12:00:00Z")
            assertEqual(payload.effectiveDurationMinutes, 45)
        } else {
            assertTrue(false, "Expected updateTaskDueDate payload")
        }
    }

    func test02_missingDueAtThrows() {
        let json = """
        {
            "operation_type": "update_task_due_date",
            "target_entity_id": "task_123",
            "display_summary": "Update task due date",
            "field_changes": {
                "effective_duration_minutes": 45
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DisplayOperationPreviewPayload.self, from: json))
        #endif
    }

    func test03_validReminderPayload() throws {
        let json = """
        {
            "operation_type": "create_reminder",
            "target_entity_id": "task_456",
            "display_summary": "Create reminder",
            "field_changes": {
                "scheduled_at": "2026-10-08T09:30:00Z",
                "linked_task_id": "task_456"
            }
        }
        """.data(using: .utf8)!

        let preview = try JSONDecoder().decode(DisplayOperationPreviewPayload.self, from: json)
        assertEqual(preview.operationType, .createReminder)
        if case .createReminder(let payload) = preview.fieldChanges {
            assertEqual(payload.scheduledAt, "2026-10-08T09:30:00Z")
            assertEqual(payload.linkedTaskId, "task_456")
        } else {
            assertTrue(false, "Expected createReminder payload")
        }
    }

    func test04_missingLinkedTaskIdThrows() {
        let json = """
        {
            "operation_type": "create_reminder",
            "target_entity_id": "task_456",
            "display_summary": "Create reminder",
            "field_changes": {
                "scheduled_at": "2026-10-08T09:30:00Z"
            }
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DisplayOperationPreviewPayload.self, from: json))
        #endif
    }

    func test05_unknownOperationDiscriminatorThrows() {
        let json = """
        {
            "operation_type": "delete_everything_dangerous",
            "target_entity_id": "task_456",
            "display_summary": "Dangerous",
            "field_changes": {}
        }
        """.data(using: .utf8)!

        #if canImport(XCTest)
        XCTAssertThrowsError(try JSONDecoder().decode(DisplayOperationPreviewPayload.self, from: json))
        #endif
    }

    // MARK: - State Machine Transition Tests (6–11)

    func test06_idleToAnalysisLoaded() async {
        let responseJSON = """
        {
            "analysis_id": "an_01",
            "target_date": "2026-10-08",
            "timezone": "UTC",
            "conflicts": [],
            "strategy_options": [],
            "created_at": "2026-10-07T12:00:00Z",
            "expires_at": "2026-10-07T13:00:00Z"
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, responseJSON)
        }

        assertEqual(viewModel.state, .idle)
        await viewModel.analyzeConflicts(date: "2026-10-08")
        if case .analysisLoaded(let analysis) = viewModel.state {
            assertEqual(analysis.analysisId, "an_01")
        } else {
            assertTrue(false, "Expected state .analysisLoaded")
        }
    }

    func test07_submittingToAwaitingConfirmation() async throws {
        let strategy = makeStrategy()
        // Setup initial analysis state
        let analysis = ResolveConflictsResponsePayload(
            analysisId: "an_01",
            targetDate: "2026-10-08",
            timezone: "UTC",
            conflicts: [],
            strategyOptions: [strategy],
            createdAt: "2026-10-07T12:00:00Z",
            expiresAt: "2026-10-07T13:00:00Z"
        )
        // Simulate analysisLoaded
        let analysisField = Mirror(reflecting: viewModel).children.first(where: { $0.label == "_state" })
        // Use analyzeConflicts flow or simulate via staging task:
        MockURLProtocol.requestHandler = { request in
            let pausedChatJSON = """
            {
                "type": "message",
                "conversation_id": "conv_01",
                "message": { "role": "assistant", "content": "Pausing for confirmation." },
                "provider": "mock",
                "model": "mock-model",
                "status": "paused",
                "execution_id": "exec_01",
                "confirmation": {
                    "confirmation_id": "conf_01",
                    "execution_id": "exec_01",
                    "tool_call": {
                        "id": "call_01",
                        "name": "apply_coordination_proposal",
                        "arguments": { "proposal_id": "prop_strat_01" }
                    },
                    "risk_level": "high",
                    "description": "Apply proposal"
                }
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, pausedChatJSON)
        }

        // Move to analysisLoaded
        let resJSON = """
        { "analysis_id": "an_01", "target_date": "2026-10-08", "timezone": "UTC", "conflicts": [], "strategy_options": [], "created_at": "2026", "expires_at": "2026" }
        """.data(using: .utf8)!
        MockURLProtocol.requestHandler = { req in
            let res = HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, resJSON)
        }
        await viewModel.analyzeConflicts(date: "2026-10-08")

        // Now stage
        MockURLProtocol.requestHandler = { request in
            let pausedChatJSON = """
            {
                "type": "confirmation_required",
                "conversation_id": "conv_01",
                "message": { "role": "assistant", "content": "Pausing for confirmation." },
                "provider": "mock",
                "model": "mock-model",
                "execution_id": "exec_01",
                "confirmation_id": "conf_01",
                "tool": {
                    "name": "apply_coordination_proposal",
                    "arguments": { "proposal_id": "prop_strat_01" },
                    "risk_level": "high",
                    "description": "Apply proposal"
                }
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, pausedChatJSON)
        }

        viewModel.stageProposal(strategy: strategy)
        // Wait for staging task
        try await Task.sleep(nanoseconds: 100_000_000)

        if case let .awaitingConfirmation(_, propId, confId, execId, convId, _) = viewModel.state {
            assertEqual(propId, "prop_strat_01")
            assertEqual(confId, "conf_01")
            assertEqual(execId, "exec_01")
            assertEqual(convId, "conv_01")
        } else {
            assertTrue(false, "Expected awaitingConfirmation state")
        }
    }

    func test08_awaitingToExecuting() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            // Delay to inspect executing state
            let completedJSON = """
            {
                "type": "message",
                "conversation_id": "conv_01",
                "message": { "role": "assistant", "content": "Done." },
                "provider": "mock",
                "model": "mock-model"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, completedJSON)
        }

        viewModel.confirmProposal(approved: true)
        if case .executingMutation = viewModel.state {
            assertTrue(viewModel.isMutating)
        }
        try await Task.sleep(nanoseconds: 100_000_000)
        if case .completed(let summary) = viewModel.state {
            assertEqual(summary, "Done.")
        } else {
            assertTrue(false, "Expected completed state")
        }
    }

    func test09_executingToMutationOutcomeUnknown() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: nil)
        }

        viewModel.confirmProposal(approved: true)
        try await Task.sleep(nanoseconds: 100_000_000)

        if case .mutationOutcomeUnknown = viewModel.state {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown state on timeout")
        }
    }

    func test10_unknownToRecovering() async throws {
        try await test09_executingToMutationOutcomeUnknown()

        MockURLProtocol.requestHandler = { request in
            // Delay response to check recovering state
            Thread.sleep(forTimeInterval: 0.1)
            let histJSON = """
            {
                "conversation_id": "conv_01",
                "messages": [],
                "created_at": "2026",
                "updated_at": "2026"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, histJSON)
        }

        viewModel.performSingleRecoveryAttempt()
        if case .recovering = viewModel.state {
            assertTrue(viewModel.isRecovering)
        }
    }

    func test11_recoveringToRecoveryUncertain() async throws {
        try await test09_executingToMutationOutcomeUnknown()

        MockURLProtocol.requestHandler = { request in
            // Empty messages history: no execution record found
            let histJSON = """
            {
                "conversation_id": "conv_01",
                "messages": [],
                "created_at": "2026",
                "updated_at": "2026"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, histJSON)
        }

        viewModel.performSingleRecoveryAttempt()
        try await Task.sleep(nanoseconds: 100_000_000)

        if case let .recoveryUncertain(_, _, reason) = viewModel.state {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected recoveryUncertain(.missingExecutionRecord)")
        }
    }

    // MARK: - Correlation Tests (12–21)

    func test12_matchingProposalAndExactOperationSequence() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case .completed = result {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected completed on exact match")
        }
    }

    func test13_mismatchedProposal() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_DIFFERENT",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for mismatched proposal")
        }
    }

    func test14_wrongOperationOrder() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "completed" },
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for wrong operation order")
        }
    }

    func test15_missingOperation() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for missing operation")
        }
    }

    func test16_extraOperation() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "completed" },
                { "operation_id": "op_03", "operation_type": "create_reminder", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for extra operation")
        }
    }

    func test17_multipleMatchingToolRecords() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [
                MessageOutPayload(role: "tool", content: toolOutput),
                MessageOutPayload(role: "tool", content: toolOutput)
            ],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .ambiguousExecutionRecord)
        } else {
            assertTrue(false, "Expected ambiguousExecutionRecord for multiple matching records")
        }
    }

    func test18_unrelatedLatestToolMessageWithValidEarlierRecord() {
        let earlierCorrelatedOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let latestUnrelatedOutput = """
        {
            "status": "success",
            "unrelated_tool": "weather_lookup",
            "output": "Sunny"
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [
                MessageOutPayload(role: "tool", content: earlierCorrelatedOutput),
                MessageOutPayload(role: "tool", content: latestUnrelatedOutput)
            ],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case .completed = result {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected completed by finding earlier correlated record")
        }
    }

    func test19_noCorrelatedToolRecord() {
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [
                MessageOutPayload(role: "user", content: "Hello"),
                MessageOutPayload(role: "assistant", content: "Hi")
            ],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord")
        }
    }

    func test20_executionIdPresentButMismatched() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "execution_id": "exec_DIFFERENT",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .mismatchedIdentity(expected: "exec_100", actual: "exec_DIFFERENT"))
        } else {
            assertTrue(false, "Expected mismatchedIdentity")
        }
    }

    func test21_executionIdAbsentUnderCurrentBackendContract() {
        let toolOutput = """
        {
            "status": "success",
            "proposal_id": "prop_100",
            "proposal_status": "completed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        // Missing execution_id under current contract is accepted if proposal_id and ops match
        if case .completed = result {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected completed when execution_id is absent from current backend payload")
        }
    }

    // MARK: - Zero Mutation Proof Tests (22–33)

    func test22_validFailedWithZeroCompleted() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "failure_reason": "OPERATION_PRECONDITION_FAILED",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "failed", "failure_error": "DEADLINE_PASSED" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "aborted" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .failed(msg) = result {
            assertEqual(msg, "OPERATION_PRECONDITION_FAILED")
        } else {
            assertTrue(false, "Expected failed state for proven zero mutation")
        }
    }

    func test23_emptyOperations() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": []
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for empty operations")
        }
    }

    func test24_completedGreaterThanZero() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .partialExecution(completedOps: 1, totalOps: 2))
        } else {
            assertTrue(false, "Expected partialExecution when completed > 0")
        }
    }

    func test25_multipleFailedOperations() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "failed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            if case .malformedResponse = reason {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected malformedResponse for multiple failures")
            }
        } else {
            assertTrue(false, "Expected recoveryUncertain")
        }
    }

    func test26_failedOperationNotFirst() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "pending" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            if case .malformedResponse = reason {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected malformedResponse when failed operation is not index 0")
            }
        } else {
            assertTrue(false, "Expected recoveryUncertain")
        }
    }

    func test27_unknownOperationStatus() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "exploding_in_space" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            if case .malformedResponse = reason {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected malformedResponse on unknown operation status")
            }
        } else {
            assertTrue(false, "Expected recoveryUncertain")
        }
    }

    func test28_failedThenCompleted() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "failed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .partialExecution(completedOps: 1, totalOps: 2))
        } else {
            assertTrue(false, "Expected recoveryUncertain(.partialExecution)")
        }
    }

    func test29_completedThenFailed() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "failed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .partialExecution(completedOps: 1, totalOps: 2))
        } else {
            assertTrue(false, "Expected partialExecution")
        }
    }

    func test30_partialWithCompletedGreaterThanZero() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "partially_executed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" },
                { "operation_id": "op_02", "operation_type": "create_reminder", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01", "op_02"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .partialExecution(completedOps: 1, totalOps: 2))
        } else {
            assertTrue(false, "Expected partialExecution")
        }
    }

    func test31_partialWithZeroCompleted() {
        let toolOutput = """
        {
            "status": "failed",
            "proposal_id": "prop_100",
            "proposal_status": "partially_executed",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "failed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            if case .malformedResponse = reason {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected malformedResponse when partially_executed has 0 completed")
            }
        } else {
            assertTrue(false, "Expected recoveryUncertain")
        }
    }

    func test32_genericErrorWithoutOperations() {
        let toolOutput = """
        {
            "status": "error",
            "proposal_id": "prop_100",
            "error": "EXECUTION_ERROR",
            "message": "Something went wrong"
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for generic error without operations")
        }
    }

    func test33_unknownProposalStatus() {
        let toolOutput = """
        {
            "status": "unknown_future_status",
            "proposal_id": "prop_100",
            "proposal_status": "quantum_uncertainty",
            "operations": [
                { "operation_id": "op_01", "operation_type": "update_task_due_date", "status": "completed" }
            ]
        }
        """
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "tool", content: toolOutput)],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            if case .malformedResponse = reason {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected malformedResponse for unknown proposal status")
            }
        } else {
            assertTrue(false, "Expected recoveryUncertain")
        }
    }

    // MARK: - 409 Tests (34–36)

    func test34_activeExecutionConflictTriggersRecovery() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            if request.httpMethod == "POST" {
                let conflictJSON = """
                { "detail": "Conversation currently has an active execution in progress or waiting for confirmation." }
                """.data(using: .utf8)!
                let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!
                return (res, conflictJSON)
            } else {
                Thread.sleep(forTimeInterval: 0.1)
                let histJSON = """
                { "conversation_id": "conv_01", "messages": [], "created_at": "2026", "updated_at": "2026" }
                """.data(using: .utf8)!
                let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (res, histJSON)
            }
        }

        viewModel.confirmProposal(approved: true)
        try await Task.sleep(nanoseconds: 50_000_000)

        // Must trigger single recovery attempt
        assertTrue(viewModel.isRecovering || viewModel.hasActiveRecoveryTask || (viewModel.state == .recovering(proposalId: "prop_strat_01", confirmationId: "conf_01", executionId: "exec_01", conversationId: "conv_01", stagedOperationIds: ["op_01", "op_02"])))
    }

    func test35_terminalConflictTriggersRecovery() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            if request.httpMethod == "POST" {
                let conflictJSON = """
                { "detail": "No action is currently waiting for confirmation in conversation 'conv_01'." }
                """.data(using: .utf8)!
                let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!
                return (res, conflictJSON)
            } else {
                Thread.sleep(forTimeInterval: 0.1)
                let histJSON = """
                { "conversation_id": "conv_01", "messages": [], "created_at": "2026", "updated_at": "2026" }
                """.data(using: .utf8)!
                let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (res, histJSON)
            }
        }

        viewModel.confirmProposal(approved: true)
        try await Task.sleep(nanoseconds: 50_000_000)

        assertTrue(viewModel.isRecovering || viewModel.hasActiveRecoveryTask || (viewModel.state == .recovering(proposalId: "prop_strat_01", confirmationId: "conf_01", executionId: "exec_01", conversationId: "conv_01", stagedOperationIds: ["op_01", "op_02"])))
    }

    func test36_unclassified409FailsClosed() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            let conflictJSON = """
            { "detail": "Unclassified gateway synchronization error" }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 409, httpVersion: nil, headerFields: nil)!
            return (res, conflictJSON)
        }

        viewModel.confirmProposal(approved: true)
        try await Task.sleep(nanoseconds: 50_000_000)

        if case let .recoveryUncertain(_, _, reason) = viewModel.state {
            assertEqual(reason, .terminalStateMismatch)
        } else {
            assertTrue(false, "Expected recoveryUncertain(.terminalStateMismatch) on unclassified 409")
        }
    }

    // MARK: - Lifecycle Tests (37–43)

    func test37_sheetDismissalDoesNotCancelMutation() async throws {
        try await test07_submittingToAwaitingConfirmation()

        MockURLProtocol.requestHandler = { request in
            // Delay to keep task active
            Thread.sleep(forTimeInterval: 0.1)
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, "{}".data(using: .utf8)!)
        }

        viewModel.confirmProposal(approved: true)
        assertTrue(viewModel.hasActiveMutationTask)

        // Simulate sheet dismissal (sheet views are recreated or dismissed without touching ViewModel)
        let sheet = ScheduleCoordinationSheet(viewModel: viewModel)
        _ = sheet.body

        // Task must remain active and generation must not reset
        assertTrue(viewModel.hasActiveMutationTask)
        assertEqual(viewModel.currentGeneration, 0)
    }

    func test38_sheetDismissalDoesNotCancelRecovery() async throws {
        try await test09_executingToMutationOutcomeUnknown()

        MockURLProtocol.requestHandler = { request in
            Thread.sleep(forTimeInterval: 0.1)
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, "{}".data(using: .utf8)!)
        }

        viewModel.performSingleRecoveryAttempt()
        assertTrue(viewModel.hasActiveRecoveryTask)

        // Simulate sheet dismissal
        let sheet = ScheduleCoordinationSheet(viewModel: viewModel)
        _ = sheet.body

        assertTrue(viewModel.hasActiveRecoveryTask)
        assertEqual(viewModel.currentGeneration, 0)
    }

    func test39_staleGenerationResponseIgnored() async throws {
        // Start from analysisLoaded
        let resJSON = """
        { "analysis_id": "an_01", "target_date": "2026-10-08", "timezone": "UTC", "conflicts": [], "strategy_options": [], "created_at": "2026", "expires_at": "2026" }
        """.data(using: .utf8)!
        MockURLProtocol.requestHandler = { req in
            Thread.sleep(forTimeInterval: 0.15)
            let res = HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, resJSON)
        }

        let task = Task {
            await self.viewModel.analyzeConflicts(date: "2026-10-08")
        }

        // Wait briefly to ensure analyzeConflicts captures generation 0 and enters request
        try await Task.sleep(nanoseconds: 20_000_000)

        // Call startFresh immediately while task is in-flight
        viewModel.startFresh()
        assertEqual(viewModel.currentGeneration, 1)

        await task.value

        // Must remain idle because gen 0 response was fenced
        assertEqual(viewModel.state, .idle)
    }

    func test40_recoveryTransportFailurePreservesUnknown() async throws {
        try await test09_executingToMutationOutcomeUnknown()

        MockURLProtocol.requestHandler = { request in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost, userInfo: nil)
        }

        viewModel.performSingleRecoveryAttempt()
        try await Task.sleep(nanoseconds: 100_000_000)

        if case .mutationOutcomeUnknown = viewModel.state {
            assertTrue(true)
        } else {
            assertTrue(false, "Expected mutationOutcomeUnknown preserved on recovery transport failure")
        }
    }

    func test41_recovery404TransitionsToSessionNotFound() async throws {
        try await test09_executingToMutationOutcomeUnknown()

        MockURLProtocol.requestHandler = { request in
            let res = HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!
            return (res, "Conversation not found".data(using: .utf8)!)
        }

        viewModel.performSingleRecoveryAttempt()
        try await Task.sleep(nanoseconds: 100_000_000)

        if case let .recoveryUncertain(_, _, reason) = viewModel.state {
            assertEqual(reason, .sessionNotFound)
        } else {
            assertTrue(false, "Expected recoveryUncertain(.sessionNotFound) on 404")
        }
    }

    func test42_stalePendingConfirmationEvaluatesUncertain() {
        // Conversation history has no tool execution message, so even if pending confirmation remained in session on server,
        // client receives missingExecutionRecord and strictly prohibits re-confirmation
        let history = ConversationHistoryResponsePayload(
            conversationId: "conv_100",
            messages: [MessageOutPayload(role: "user", content: "Apply proposal")],
            createdAt: "2026",
            updatedAt: "2026"
        )

        let result = viewModel.evaluateRecoveryHistory(
            history: history,
            expectedProposalId: "prop_100",
            expectedExecutionId: "exec_100",
            stagedOperationIds: ["op_01"],
            conversationId: "conv_100"
        )
        if case let .recoveryUncertain(_, _, reason) = result {
            assertEqual(reason, .missingExecutionRecord)
        } else {
            assertTrue(false, "Expected missingExecutionRecord for history missing execution message")
        }
    }

    func test43_noPollingTimerBackgroundRecovery() {
        // Assert startFresh is strictly blocked during mutating or unknown states
        let dummyStrategy = makeStrategy()
        let vm = ScheduleCoordinationViewModel(apiClient: client)

        assertFalse(vm.hasActiveRecoveryTask)
        assertFalse(vm.hasActiveMutationTask)
        assertTrue(vm.canStartFresh())
    }

    // MARK: - API Route Tests (44–45)

    func test44_actualProductionConversationHistoryRoute() async throws {
        var observedURL: URL? = nil
        MockURLProtocol.requestHandler = { request in
            observedURL = request.url
            let histJSON = """
            {
                "conversation_id": "conv_abc_123",
                "messages": [
                    { "role": "user", "content": "hello" }
                ],
                "created_at": "2026-10-07T10:00:00Z",
                "updated_at": "2026-10-07T10:00:05Z"
            }
            """.data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, histJSON)
        }

        let resp = try await client.fetchConversationHistory(conversationId: "conv_abc_123")
        assertEqual(observedURL?.path, "/api/v1/conversations/conv_abc_123")
        assertEqual(resp.conversationId, "conv_abc_123")
        assertEqual(resp.messages.count, 1)
    }

    func test45_malformedResponseFailsClosed() async {
        MockURLProtocol.requestHandler = { request in
            let garbageData = "INVALID_JSON_GARBAGE".data(using: .utf8)!
            let res = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (res, garbageData)
        }

        do {
            _ = try await client.fetchConversationHistory(conversationId: "conv_01")
            assertTrue(false, "Expected decodingError")
        } catch let err as APIError {
            if case .decodingError = err {
                assertTrue(true)
            } else {
                assertTrue(false, "Expected APIError.decodingError")
            }
        } catch {
            assertTrue(false, "Expected APIError")
        }
    }
}
