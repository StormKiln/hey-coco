import Foundation

// Calendar events (#137, #140): the same shape on the home screen (`IdleInfo.calendar`) and in
// voice answers (`ViewSpec.calendar`).

public struct EventCalendar: Codable, Sendable, Equatable, Hashable {
    public var name: String
    /// `#RRGGBB`.
    public var color: String?

    public init(name: String, color: String?) {
        self.name = name
        self.color = color
    }
}

public struct CalendarEvent: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    /// `Busy` when `busyOnly`.
    public var title: String
    public var start: Date
    /// All-day events end at the Hub-local midnight after their last day.
    public var end: Date
    public var allDay: Bool
    public var calendar: EventCalendar
    /// Private event or a busy-only calendar: show the time, not a title.
    public var busyOnly: Bool

    public init(
        id: String, title: String, start: Date, end: Date, allDay: Bool,
        calendar: EventCalendar, busyOnly: Bool
    ) {
        self.id = id
        self.title = title
        self.start = start
        self.end = end
        self.allDay = allDay
        self.calendar = calendar
        self.busyOnly = busyOnly
    }

    enum CodingKeys: String, CodingKey {
        case id, title, start, end, calendar
        case allDay = "all_day"
        case busyOnly = "busy_only"
    }
}

public struct CalendarDay: Codable, Sendable, Equatable {
    /// `YYYY-MM-DD` in the Hub's timezone.
    public var date: String
    /// All-day first, then by start.
    public var events: [CalendarEvent]

    public init(date: String, events: [CalendarEvent]) {
        self.date = date
        self.events = events
    }
}

public struct IdleCalendar: Codable, Sendable, Equatable {
    /// Today and tomorrow (Hub dates).
    public var days: [CalendarDay]
    public var stale: Bool
    /// The oldest calendar's last good fetch; nil if some calendar never fetched.
    public var fetchedAt: Date?
    /// Leave events off the home screen during night hours.
    public var hideAtNight: Bool

    public init(days: [CalendarDay], stale: Bool, fetchedAt: Date?, hideAtNight: Bool) {
        self.days = days
        self.stale = stale
        self.fetchedAt = fetchedAt
        self.hideAtNight = hideAtNight
    }

    enum CodingKeys: String, CodingKey {
        case days, stale
        case fetchedAt = "fetched_at"
        case hideAtNight = "hide_at_night"
    }
}
