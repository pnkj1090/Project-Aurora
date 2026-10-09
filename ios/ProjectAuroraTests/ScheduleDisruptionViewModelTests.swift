//
//  ScheduleDisruptionViewModelTests.swift
//  ProjectAuroraTests
//
//  Created for Phase 6K: iOS Schedule Disruption Integration & Recovery Presentation.
//

import Foundation

@MainActor
public final class ScheduleDisruptionViewModelTests {
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
        testInitialStateIsIdle()
        try await testExplicitFetchSuccess()
        try await testEmptyDiagnostic()
        try await testTaskPermissionFailure()
        try await testCalendarFailure()
        try await testStaleCalendar()
        try await testUniverseSaturation()
        try await testRetry()
        testSelectionToggleWithinUniverse()
        testArbitraryTaskIdRejected()
        testArbitraryReminderIdRejected()
        testSelectRecommended()
        testClearSelection()
        testZeroTaskRecoveryDisabled()
        testReminderOnlyRecoveryDisabled()
        try await testExplicitPermissionRetry()
        print("✅ All ScheduleDisruptionViewModelTests passed successfully.")
    }

    // 1. Initial state must be idle (no auto-fetch)
    public func testInitialStateIsIdle() {
        setup()
        let vm = ScheduleDisruptionViewModel(apiClient: client)
        assert(vm.state == .idle, "Initial state must be idle")
        assert(vm.selectedTaskIds.isEmpty, "Initial selectedTaskIds must be empty")
        assert(vm.selectedReminderIds.isEmpty, "Initial selectedReminderIds must be empty")
        assert(!vm.isRecoveryActionEnabled, "Recovery action must be disabled initially")
    }

    private func makeSampleDiagnostic(severity: ScheduleDisruptionSeverity = .moderate, deficit: Int = 60) -> ScheduleDisruptionDiagnostic {
        return ScheduleDisruptionDiagnostic(
            diagnosticId: "diag_1",
            computedAt: Date(),
            evaluationWindowStart: Date(),
            evaluationWindowEnd: Date().addingTimeInterval(32400),
            timezone: "Asia/Kolkata",
            severity: severity,
            totalWorkingMinutes: 540,
            availableWorkingMinutes: 360,
            requiredTaskMinutes: 420,
            focusCapacityDeficitMinutes: deficit,
            overdueTaskCount: 1,
            jeopardyTasks: [
                TaskJeopardyItem(
                    taskId: "task_1",
                    title: "Audit Reports",
                    currentDueAt: Date(),
                    linkedCommitmentId: "comm_1",
                    commitmentTargetDate: "2026-10-01",
                    jeopardyReasons: [.overdue],
                    estimatedDurationMinutes: 60
                ),
                TaskJeopardyItem(
                    taskId: "task_2",
                    title: "Prepare Slides",
                    currentDueAt: Date(),
                    linkedCommitmentId: nil,
                    commitmentTargetDate: nil,
                    jeopardyReasons: [.workingHoursOverflow],
                    estimatedDurationMinutes: 60
                )
            ],
            recommendedRecoveryTaskIds: ["task_1"],
            recommendedRecoveryReminderIds: ["rem_1"],
            omittedDomains: []
        )
    }

    // 2. Explicit fetch transitions to loaded
    public func testExplicitFetchSuccess() async throws {
        setup()
        let diagnostic = makeSampleDiagnostic()
        let encoded = try JSONEncoder().encode(diagnostic)

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, encoded)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        assert(vm.state == .idle)

        await vm.diagnose()
        if case .loaded(let loadedDiag) = vm.state {
            assert(loadedDiag.diagnosticId == "diag_1")
            assert(loadedDiag.jeopardyTasks.count == 2)
        } else {
            assertionFailure("Expected state .loaded, got \(vm.state)")
        }
    }

    // 3. Empty diagnostic state
    public func testEmptyDiagnostic() async throws {
        setup()
        let emptyDiag = ScheduleDisruptionDiagnostic(
            diagnosticId: "diag_empty",
            computedAt: Date(),
            evaluationWindowStart: Date(),
            evaluationWindowEnd: Date().addingTimeInterval(32400),
            timezone: "Asia/Kolkata",
            severity: .none,
            totalWorkingMinutes: 540,
            availableWorkingMinutes: 540,
            requiredTaskMinutes: 0,
            focusCapacityDeficitMinutes: 0,
            overdueTaskCount: 0,
            jeopardyTasks: [],
            recommendedRecoveryTaskIds: [],
            recommendedRecoveryReminderIds: [],
            omittedDomains: []
        )
        let encoded = try JSONEncoder().encode(emptyDiag)

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, encoded)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .empty, "Expected .empty state for balanced schedule with 0 tasks")
    }

    // 4. Task permission failure (HTTP 412 TASK_DOMAIN_UNAVAILABLE)
    public func testTaskPermissionFailure() async throws {
        setup()
        let errorJSON = """
        {"detail": "TASK_DOMAIN_UNAVAILABLE: Task access not permitted"}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .taskDomainUnavailable, "Expected .taskDomainUnavailable, got \(vm.state)")
    }

    // 5. Calendar failure (HTTP 412 NO_CALENDAR_SNAPSHOT / CALENDAR_SNAPSHOT_SATURATED)
    public func testCalendarFailure() async throws {
        setup()
        let errorJSON = """
        {"detail": "NO_CALENDAR_SNAPSHOT: Calendar snapshot missing"}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .calendarUnavailable, "Expected .calendarUnavailable, got \(vm.state)")
    }

    // 6. Stale calendar (HTTP 412 STALE_CALENDAR_SNAPSHOT)
    public func testStaleCalendar() async throws {
        setup()
        let errorJSON = """
        {"detail": "STALE_CALENDAR_SNAPSHOT: Calendar older than 24h"}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .staleCalendar, "Expected .staleCalendar, got \(vm.state)")
    }

    // 7. Universe saturation (HTTP 412 TASK_UNIVERSE_SATURATED)
    public func testUniverseSaturation() async throws {
        setup()
        let errorJSON = """
        {"detail": "TASK_UNIVERSE_SATURATED: Over 500 tasks active"}
        """.data(using: .utf8)!

        MockURLProtocol.requestHandler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (response, errorJSON)
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .universeSaturated, "Expected .universeSaturated, got \(vm.state)")
    }

    // 8. Retry action triggers new fetch
    public func testRetry() async throws {
        setup()
        var callCount = 0
        MockURLProtocol.requestHandler = { request in
            callCount += 1
            if callCount == 1 {
                let err = """
                {"detail": "INTERNAL_ERROR: Transient error"}
                """.data(using: .utf8)!
                let response = HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: [:])!
                return (response, err)
            } else {
                let diag = self.makeSampleDiagnostic()
                let data = try JSONEncoder().encode(diag)
                let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: [:])!
                return (response, data)
            }
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        if case .failed = vm.state {
            // expected
        } else {
            assertionFailure("Expected .failed on 500")
        }

        await vm.retry()
        if case .loaded = vm.state {
            assert(callCount == 2, "Retry should have issued second request")
        } else {
            assertionFailure("Expected .loaded on retry")
        }
    }

    // 9. Selection toggle within allowable universe
    public func testSelectionToggleWithinUniverse() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        assert(vm.selectedTaskIds.isEmpty)
        vm.toggleTaskSelection("task_1")
        assert(vm.selectedTaskIds.contains("task_1"))
        assert(vm.isRecoveryActionEnabled)

        vm.toggleTaskSelection("task_1")
        assert(!vm.selectedTaskIds.contains("task_1"))
        assert(!vm.isRecoveryActionEnabled)
    }

    // 10. Arbitrary task ID rejected
    public func testArbitraryTaskIdRejected() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        vm.toggleTaskSelection("arbitrary_unknown_task_999")
        assert(vm.selectedTaskIds.isEmpty, "Arbitrary task ID must NOT be selected")
        assert(!vm.isRecoveryActionEnabled)
    }

    // 11. Arbitrary reminder ID rejected
    public func testArbitraryReminderIdRejected() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        vm.toggleReminderSelection("arbitrary_unknown_reminder_999")
        assert(vm.selectedReminderIds.isEmpty, "Arbitrary reminder ID must NOT be selected")
    }

    // 12. Select recommended
    public func testSelectRecommended() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        vm.selectAllRecommended()
        assert(vm.selectedTaskIds == ["task_1"], "Only recommended task in universe selected")
        assert(vm.selectedReminderIds == ["rem_1"], "Only recommended reminder in universe selected")
        assert(vm.isRecoveryActionEnabled)
    }

    // 13. Clear selection
    public func testClearSelection() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        vm.selectAllRecommended()
        assert(!vm.selectedTaskIds.isEmpty)

        vm.clearSelection()
        assert(vm.selectedTaskIds.isEmpty)
        assert(vm.selectedReminderIds.isEmpty)
        assert(!vm.isRecoveryActionEnabled)
    }

    // 14. Zero-task recovery disabled
    public func testZeroTaskRecoveryDisabled() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        assert(vm.selectedTaskIds.isEmpty)
        assert(!vm.isRecoveryActionEnabled, "Recovery must be disabled when 0 tasks selected")
    }

    // 15. Reminder-only recovery disabled
    public func testReminderOnlyRecoveryDisabled() {
        let diag = makeSampleDiagnostic()
        let vm = ScheduleDisruptionViewModel(initialDiagnostic: diag)

        vm.toggleReminderSelection("rem_1")
        assert(vm.selectedTaskIds.isEmpty)
        assert(vm.selectedReminderIds.contains("rem_1"))
        assert(!vm.isRecoveryActionEnabled, "Recovery must remain disabled if tasks are empty (reminder-only forbidden)")
    }

    // 16. Explicit permission retry
    public func testExplicitPermissionRetry() async throws {
        setup()
        var requestCount = 0
        MockURLProtocol.requestHandler = { request in
            requestCount += 1
            if requestCount == 1 {
                let err = """
                {"detail": "TASK_DOMAIN_UNAVAILABLE: Denied"}
                """.data(using: .utf8)!
                return (HTTPURLResponse(url: request.url!, statusCode: 412, httpVersion: nil, headerFields: [:])!, err)
            } else {
                let diag = self.makeSampleDiagnostic()
                let data = try JSONEncoder().encode(diag)
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: [:])!, data)
            }
        }

        let vm = ScheduleDisruptionViewModel(apiClient: client)
        await vm.diagnose()
        assert(vm.state == .taskDomainUnavailable)

        // User returning to view does NOT auto-fetch: state remains taskDomainUnavailable
        assert(vm.state == .taskDomainUnavailable)
        assert(requestCount == 1)

        // Explicit user retry triggers server re-evaluation
        await vm.retry()
        assert(requestCount == 2)
        if case .loaded = vm.state {
            // Success
        } else {
            assertionFailure("Expected .loaded after permission retry, got \(vm.state)")
        }
    }
}
