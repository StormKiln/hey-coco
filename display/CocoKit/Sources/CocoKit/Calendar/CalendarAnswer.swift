import Foundation

/// One day on the calendar screen: "Saturday, November 28" and its events.
public struct CalendarAnswerDay: Equatable, Sendable, Identifiable {
    /// The Hub date, `YYYY-MM-DD`.
    public var id: String
    /// "Saturday, November 28" (Hub time); the raw date if it can't be read.
    public var header: String
    /// All-day rows first, then timed rows in the Hub's order (by start).
    public var rows: [CalendarRow]
    /// "+4 more" past `CalendarAnswer.dayLimit` rows.
    public var moreText: String?
    /// "Nothing on the calendar" for a day without events.
    public var emptyText: String?
}

public struct CalendarAnswerContent: Equatable, Sendable {
    /// "Saturday", "This weekend", "Soccer practice": what the Hub named the answer.
    public var title: String
    public var days: [CalendarAnswerDay]
    /// Shown instead of days when the Hub sent none (a "when is X" with no match).
    public var emptyText: String?
    /// "Updated 2:15 PM" / "Not updated" when the Hub's calendar data is stale, as on the home screen.
    public var staleLabel: String?
}

/// What the calendar screen (`ViewSpec.calendar`, #143) lists for a voice answer: "what's on
/// Saturday?", "what's on this weekend?", "when is soccer?". Unlike the home screen (#142) an
/// answer keeps events that have already ended: it lists the days that were asked about.
public enum CalendarAnswer {
    /// Rows per day before "+N more". The screen scrolls, but a day stays one glanceable block:
    /// eight rows, the day header and the title fit the kitchen iPad's landscape height between
    /// the top chrome and the mic button, so a single busy day reads without scrolling.
    public static let dayLimit = 8
    /// Back to idle after this long without a touch (not while Coco is listening or talking),
    /// like the weather screen: a list of days takes longer to read than a one-line answer.
    public static let idleTimeout: Duration = .seconds(30)

    public static let dayEmpty = "Nothing on the calendar"
    public static let notFound = "Nothing found on the calendar"

    public static func content(
        _ payload: ViewSpec.CalendarPayload, timeZone: TimeZone, now: Date, locale: Locale = .current
    ) -> CalendarAnswerContent {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let days = payload.days.map { day($0, calendar: cal, locale: locale) }
        return CalendarAnswerContent(
            title: payload.title, days: days,
            emptyText: payload.days.isEmpty ? (payload.query == nil ? dayEmpty : notFound) : nil,
            staleLabel: HomeCalendar.staleLabel(
                stale: payload.stale, fetchedAt: payload.fetchedAt, now: now, timeZone: timeZone, locale: locale))
    }

    // MARK: -

    private static func day(_ day: CalendarDay, calendar: Calendar, locale: Locale) -> CalendarAnswerDay {
        guard let start = startOfDay(day.date, calendar: calendar) else {
            return CalendarAnswerDay(
                id: day.date, header: day.date, rows: [], moreText: nil,
                emptyText: day.events.isEmpty ? dayEmpty : nil)
        }
        let rows = day.events.map {
            HomeCalendar.row($0, startOfDay: start, timeZone: calendar.timeZone, locale: locale)
        }
        // All-day first: the Hub sorts `all_day` events first, but a timed event that fills the
        // whole day also reads "All day", so it belongs with them.
        let allDay = HomeCalendar.allDayLabel
        let ordered = rows.filter { $0.time == allDay } + rows.filter { $0.time != allDay }
        let shown = Array(ordered.prefix(dayLimit))
        let style = Date.FormatStyle(locale: locale, timeZone: calendar.timeZone).weekday(.wide).month(.wide).day()
        return CalendarAnswerDay(
            id: day.date, header: start.formatted(style), rows: shown,
            moreText: HomeCalendar.moreText(total: ordered.count, shown: shown.count),
            emptyText: day.events.isEmpty ? dayEmpty : nil)
    }

    /// Midnight starting `YYYY-MM-DD` in the calendar's (Hub) timezone.
    private static func startOfDay(_ date: String, calendar: Calendar) -> Date? {
        let parts = date.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        let components = DateComponents(year: parts[0], month: parts[1], day: parts[2])
        guard components.isValidDate(in: calendar) else { return nil }
        return calendar.date(from: components).map { calendar.startOfDay(for: $0) }
    }
}
