import Foundation
import EventKit

// MARK: - Calendar Provider Protocol

public protocol CalendarProviderProtocol: Sendable {
    func checkAuthorizationStatus() async -> PermissionStatus
    func requestAuthorization() async throws -> PermissionStatus
    func fetchEvents(startAt: Date, endAt: Date, maxResults: Int) async throws -> [CalendarEventItem]
}

// MARK: - Calendar Provider Errors

public enum CalendarProviderError: LocalizedError, Equatable {
    case unauthorized(PermissionStatus)
    case invalidDateRange
    case fetchFailed(String)

    public var errorDescription: String? {
        switch self {
        case .unauthorized(let status):
            return "Calendar access is not authorized (status: \(status.rawValue))."
        case .invalidDateRange:
            return "End date must be greater than or equal to start date."
        case .fetchFailed(let message):
            return "Failed to fetch calendar events: \(message)"
        }
    }
}

// MARK: - Native EventKit Provider (Read-Only)

public final class EKCalendarProvider: CalendarProviderProtocol, @unchecked Sendable {
    private let eventStore: EKEventStore

    public init(eventStore: EKEventStore = EKEventStore()) {
        self.eventStore = eventStore
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        let status = EKEventStore.authorizationStatus(for: .event)
        return mapStatus(status)
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        let current = await checkAuthorizationStatus()
        guard current == .notDetermined else {
            return current
        }

        if #available(iOS 17.0, macOS 14.0, *) {
            let granted = try await eventStore.requestFullAccessToEvents()
            return granted ? .authorized : .denied
        } else {
            let granted = try await eventStore.requestAccess(to: .event)
            return granted ? .authorized : .denied
        }
    }

    public func fetchEvents(startAt: Date, endAt: Date, maxResults: Int = 10) async throws -> [CalendarEventItem] {
        let auth = await checkAuthorizationStatus()
        guard auth == .authorized else {
            throw CalendarProviderError.unauthorized(auth)
        }

        guard endAt >= startAt else {
            throw CalendarProviderError.invalidDateRange
        }

        // Predicate for read-only event retrieval
        let predicate = eventStore.predicateForEvents(withStart: startAt, end: endAt, calendars: nil)
        let ekEvents = eventStore.events(matching: predicate)

        // Deterministic sort: startDate ascending
        let sorted = ekEvents.sorted { ($0.startDate, $0.eventIdentifier ?? "") < ($1.startDate, $1.eventIdentifier ?? "") }
        let limited = sorted.prefix(max(1, maxResults))

        return limited.map { ev in
            CalendarEventItem(
                eventId: ev.eventIdentifier ?? UUID().uuidString,
                title: ev.title ?? "Untitled Event",
                startAt: ev.startDate,
                endAt: ev.endDate,
                allDay: ev.isAllDay,
                calendarName: ev.calendar?.title,
                location: ev.location,
                notesAvailable: ev.hasNotes,
                source: "ios_eventkit"
            )
        }
    }

    private func mapStatus(_ status: EKAuthorizationStatus) -> PermissionStatus {
        switch status {
        case .notDetermined:
            return .notDetermined
        case .restricted:
            return .restricted
        case .denied:
            return .denied
        case .authorized, .fullAccess:
            return .authorized
        case .writeOnly:
            // Write-only does NOT permit reading calendar events
            return .restricted
        @unknown default:
            return .unavailable
        }
    }
}

// MARK: - Mock Calendar Provider (for Previews & Offline Tests)

public final class MockCalendarProvider: CalendarProviderProtocol, @unchecked Sendable {
    public var currentStatus: PermissionStatus
    public var mockEvents: [CalendarEventItem]
    public var shouldThrowError: Bool = false

    public init(
        initialStatus: PermissionStatus = .notDetermined,
        mockEvents: [CalendarEventItem] = []
    ) {
        self.currentStatus = initialStatus
        self.mockEvents = mockEvents
    }

    public func checkAuthorizationStatus() async -> PermissionStatus {
        return currentStatus
    }

    public func requestAuthorization() async throws -> PermissionStatus {
        if currentStatus == .notDetermined {
            currentStatus = .authorized
        }
        return currentStatus
    }

    public func fetchEvents(startAt: Date, endAt: Date, maxResults: Int = 10) async throws -> [CalendarEventItem] {
        guard currentStatus == .authorized else {
            throw CalendarProviderError.unauthorized(currentStatus)
        }

        if shouldThrowError {
            throw CalendarProviderError.fetchFailed("Simulated calendar provider error")
        }

        guard endAt >= startAt else {
            throw CalendarProviderError.invalidDateRange
        }

        // Filter events overlapping [startAt, endAt]
        let matching = mockEvents.filter { ev in
            ev.endAt >= startAt && ev.startAt <= endAt
        }

        let sorted = matching.sorted { ($0.startAt, $0.eventId) < ($1.startAt, $1.eventId) }
        return Array(sorted.prefix(max(1, maxResults)))
    }
}
