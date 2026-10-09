import Foundation

// MARK: - Calendar Event Item Model

public struct CalendarEventItem: Codable, Equatable, Sendable, Identifiable {
    public var id: String { eventId }

    public let eventId: String
    public let title: String
    public let startAt: Date
    public let endAt: Date
    public let allDay: Bool
    public let calendarName: String?
    public let location: String?
    public let notesAvailable: Bool
    public let source: String
    public let userId: String?

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case title
        case startAt = "start_at"
        case endAt = "end_at"
        case allDay = "all_day"
        case calendarName = "calendar_name"
        case location
        case notesAvailable = "notes_available"
        case source
        case userId = "user_id"
    }

    public init(
        eventId: String,
        title: String,
        startAt: Date,
        endAt: Date,
        allDay: Bool = false,
        calendarName: String? = nil,
        location: String? = nil,
        notesAvailable: Bool = false,
        source: String = "ios_eventkit",
        userId: String? = nil
    ) {
        self.eventId = eventId
        self.title = title
        self.startAt = startAt
        self.endAt = endAt
        self.allDay = allDay
        self.calendarName = calendarName
        self.location = location
        self.notesAvailable = notesAvailable
        self.source = source
        self.userId = userId
    }
}

// MARK: - Calendar API Response & Request DTOs

public struct CalendarEventsResponse: Codable, Equatable, Sendable {
    public let status: String
    public let count: Int
    public let events: [CalendarEventItem]

    enum CodingKeys: String, CodingKey {
        case status
        case count
        case events
    }

    public init(status: String = "success", count: Int = 0, events: [CalendarEventItem] = []) {
        self.status = status
        self.count = count
        self.events = events
    }
}

public struct CalendarSnapshotIngestRequest: Codable, Equatable, Sendable {
    public let events: [CalendarEventItem]
    public let userId: String?

    enum CodingKeys: String, CodingKey {
        case events
        case userId = "user_id"
    }

    public init(events: [CalendarEventItem], userId: String? = nil) {
        self.events = events
        self.userId = userId
    }
}
