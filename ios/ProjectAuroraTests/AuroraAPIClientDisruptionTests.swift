//
//  AuroraAPIClientDisruptionTests.swift
//  ProjectAuroraTests
//
//  Created for Phase 6K: iOS Schedule Disruption Integration & Recovery Presentation.
//

import Foundation

public final class AuroraAPIClientDisruptionTests {
    private var client: AuroraAPIClient!
    private var session: URLSession!

    public init() {
        setup()
    }

    public func setup() {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        session = URLSession(configuration: config)
        client = AuroraAPIClient(baseURL: URL(string: "http://localhost:8000")!, session: session)
    }

    public func runAllTests() async throws {
        try await testDiagnoseDisruptionSuccess()
        try await testDiagnoseDisruptionPreconditionFailed412()
        try await testDiagnoseDisruptionInvalidWindow400()
        try await testDiagnoseDisruptionUnauthorized401()
        try await testDiagnoseDisruptionDecodingFailure()
        try await testDiagnoseDisruptionNetworkFailure()
        print("✅ All AuroraAPIClientDisruptionTests passed successfully.")
    }

    public func testDiagnoseDisruptionSuccess() async throws {
        setup()
        let sampleJSON = """
        {
            "diagnostic_id": "diag_test_123",
            "computed_at": "2026-10-01T10:00:00Z",
            "evaluation_window_start": "2026-10-01T09:00:00Z",
            "evaluation_window_end": "2026-10-01T18:00:00Z",
            "timezone": "Asia/Kolkata",
            "severity": "moderate",
            "total_working_minutes": 540,
            "available_working_minutes": 360,
            "required_task_minutes": 480,
            "focus_capacity_deficit_minutes": 120,
            "overdue_task_count": 1,
            "jeopardy_tasks": [
                {
                    "task_id": "task_1",
                    "title": "Prepare Quarterly Review",
                    "current_due_at": "2026-10-01T14:00:00Z",
                    "linked_commitment_id": "comm_1",
                    "commitment_target_date": "2026-10-01",
                    "jeopardy_reasons": ["overdue", "deadline_during_event"],
                    "estimated_duration_minutes": 120
                }
            ],
            "recommended_recovery_task_ids": ["task_1"],
            "recommended_recovery_reminder_ids": ["rem_1"],
            "omitted_domains": []
        }
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            guard let url = request.url, url.path == "/api/v1/schedule/recovery/diagnose" else {
                throw NSError(domain: "Test", code: -1, userInfo: [NSLocalizedDescriptionKey: "Unexpected URL"])
            }
            guard request.httpMethod == "POST" else {
                throw NSError(domain: "Test", code: -1, userInfo: [NSLocalizedDescriptionKey: "Expected POST"])
            }
            let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, sampleJSON)
        }

        let payload = DiagnoseScheduleDisruptionPayload(
            targetWindowStart: Date(timeIntervalSince1970: 1727773200),
            targetWindowEnd: Date(timeIntervalSince1970: 1727805600),
            clientPreferredTimezone: "Asia/Kolkata",
            includeReminders: true
        )

        let diagnostic = try await client.diagnoseScheduleDisruption(payload: payload)
        assert(diagnostic.diagnosticId == "diag_test_123", "diagnosticId mismatch")
        assert(diagnostic.severity == .moderate, "severity mismatch")
        assert(diagnostic.focusCapacityDeficitMinutes == 120, "deficit mismatch")
        assert(diagnostic.jeopardyTasks.count == 1, "jeopardyTasks count mismatch")
        assert(diagnostic.jeopardyTasks[0].taskId == "task_1", "task id mismatch")
        assert(diagnostic.jeopardyTasks[0].jeopardyReasons == [.overdue, .deadlineDuringEvent], "reasons mismatch")
        assert(diagnostic.recommendedRecoveryTaskIds == ["task_1"], "recommended tasks mismatch")
    }

    public func testDiagnoseDisruptionPreconditionFailed412() async throws {
        setup()
        let errorJSON = """
        {"detail": {"error": "TASK_DOMAIN_UNAVAILABLE", "message": "Tasks domain access is not authorized"}}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let payload = DiagnoseScheduleDisruptionPayload()
        do {
            _ = try await client.diagnoseScheduleDisruption(payload: payload)
            assertionFailure("Expected APIError.preconditionFailed to be thrown")
        } catch let APIError.preconditionFailed(code, message) {
            assert(code == "TASK_DOMAIN_UNAVAILABLE", "Expected TASK_DOMAIN_UNAVAILABLE, got \(code)")
            assert(message.contains("Tasks domain access is not authorized"), "Message mismatch")
        } catch {
            assertionFailure("Unexpected error type: \(error)")
        }
    }

    public func testDiagnoseDisruptionInvalidWindow400() async throws {
        setup()
        let errorJSON = """
        {"detail": "INVALID_WINDOW: target_window_start must precede target_window_end"}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 400, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let payload = DiagnoseScheduleDisruptionPayload()
        do {
            _ = try await client.diagnoseScheduleDisruption(payload: payload)
            assertionFailure("Expected 400 serverError to be thrown")
        } catch let APIError.serverError(code, message) {
            assert(code == 400, "Expected 400, got \(code)")
            assert(message.contains("INVALID_WINDOW"), "Message should mention INVALID_WINDOW")
        } catch {
            assertionFailure("Unexpected error type: \(error)")
        }
    }

    public func testDiagnoseDisruptionUnauthorized401() async throws {
        setup()
        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 401, httpVersion: nil, headerFields: [:])!
            return (response, Data())
        }

        let payload = DiagnoseScheduleDisruptionPayload()
        do {
            _ = try await client.diagnoseScheduleDisruption(payload: payload)
            assertionFailure("Expected authenticationRequired on 401")
        } catch APIError.authenticationRequired {
            // Success
        } catch {
            assertionFailure("Expected APIError.authenticationRequired, got: \(error)")
        }
    }

    public func testDiagnoseDisruptionDecodingFailure() async throws {
        setup()
        let invalidJSON = "not a valid json response".data(using: .utf8)!
        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, invalidJSON)
        }

        let payload = DiagnoseScheduleDisruptionPayload()
        do {
            _ = try await client.diagnoseScheduleDisruption(payload: payload)
            assertionFailure("Expected decodingError")
        } catch APIError.decodingError {
            // Success
        } catch {
            assertionFailure("Expected APIError.decodingError, got: \(error)")
        }
    }

    public func testDiagnoseDisruptionNetworkFailure() async throws {
        setup()
        MockURLProtocol.requestHandler = { _ in
            throw NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut, userInfo: [NSLocalizedDescriptionKey: "The request timed out."])
        }

        let payload = DiagnoseScheduleDisruptionPayload()
        do {
            _ = try await client.diagnoseScheduleDisruption(payload: payload)
            assertionFailure("Expected network failure")
        } catch {
            // Success
        }
    }
}
