import Foundation

/// One event line, on the home screen (#142) and the calendar screen (#143).
public struct CalendarRow: Equatable, Sendable, Identifiable {
    public var id: String
    /// "All day", "6:00 PM" or "Until 12:00 PM" (Hub time).
    public var time: String
    /// "Busy" for busy-only events.
    public var title: String
    /// The calendar's colour as `0xRRGGBB`; nil when it has none (the view picks one).
    public var color: UInt32?
    /// "Family", "Work".
    public var calendarName: String
    public var accessibilityLabel: String
}

/// "Today" or "Tomorrow" and what's left on it.
public struct HomeCalendarSection: Equatable, Sendable {
    public var title: String
    public var rows: [CalendarRow]
    /// "+3 more" when more events are left than rows shown.
    public var moreText: String?
    /// Shown instead of rows when nothing is left.
    public var emptyText: String?
}

public struct HomeCalendarContent: Equatable, Sendable {
    public var today: HomeCalendarSection
    /// Only when today is light (and the Hub sent tomorrow).
    public var tomorrow: HomeCalendarSection?
    /// "Updated 2:15 PM" / "Not updated" when the Hub's calendar data is stale.
    public var staleLabel: String?
}

/// What the home screen lists from `IdleInfo.calendar` (#142). Evaluated against the iPad's own
/// clock, so ended events drop off and "today" rolls over at midnight without a Hub fetch.
public enum HomeCalendar {
    public static let todayLimit = 5
    public static let tomorrowLimit = 3
    /// Tomorrow shows only when today has at most this many events left.
    public static let tomorrowThreshold = 3

    public static let allDayLabel = "All day"
    public static let todayEmpty = "Nothing else today"
    public static let tomorrowEmpty = "Nothing tomorrow"

    /// `nil` (draw the home screen without a calendar) when no calendars are configured, or when
    /// the Hub asks to hide events at night and `now` is inside the night window.
    public static func content(
        _ calendar: IdleCalendar?, timeZone: TimeZone, night: Night?, now: Date, locale: Locale = .current
    ) -> HomeCalendarContent? {
        guard let calendar else { return nil }
        if calendar.hideAtNight, let night, let schedule = NightSchedule(night),
            schedule.contains(now, in: timeZone)
        {
            return nil
        }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let todayKey = dayKey(now, calendar: cal)
        let tomorrowKey = cal.date(byAdding: .day, value: 1, to: cal.startOfDay(for: now))
            .map { dayKey($0, calendar: cal) }
        let startOfToday = cal.startOfDay(for: now)

        let todayLeft = (calendar.days.first { $0.date == todayKey }?.events ?? []).filter { $0.end > now }
        let today = section(
            "Today", todayLeft, limit: todayLimit, empty: todayEmpty, startOfDay: startOfToday,
            timeZone: timeZone, locale: locale)

        var tomorrow: HomeCalendarSection?
        if todayLeft.count <= tomorrowThreshold, let tomorrowKey,
            let day = calendar.days.first(where: { $0.date == tomorrowKey })
        {
            let start = cal.date(byAdding: .day, value: 1, to: startOfToday) ?? startOfToday
            tomorrow = section(
                "Tomorrow", day.events, limit: tomorrowLimit, empty: tomorrowEmpty, startOfDay: start,
                timeZone: timeZone, locale: locale)
        }
        return HomeCalendarContent(
            today: today, tomorrow: tomorrow,
            staleLabel: staleLabel(calendar, now: now, timeZone: timeZone, locale: locale))
    }

    /// "Updated 2:15 PM" (or "Updated Nov 23" from an earlier day) when stale; "Not updated" if a
    /// calendar never fetched; nil when fresh.
    public static func staleLabel(
        _ calendar: IdleCalendar, now: Date, timeZone: TimeZone, locale: Locale = .current
    ) -> String? {
        staleLabel(stale: calendar.stale, fetchedAt: calendar.fetchedAt, now: now, timeZone: timeZone, locale: locale)
    }

    /// The same label from a `calendar` view's `stale` and `fetched_at` (#143).
    public static func staleLabel(
        stale: Bool, fetchedAt: Date?, now: Date, timeZone: TimeZone, locale: Locale = .current
    ) -> String? {
        guard stale else { return nil }
        guard let fetchedAt else { return "Not updated" }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        if cal.isDate(fetchedAt, inSameDayAs: now) {
            return "Updated \(time(fetchedAt, timeZone: timeZone, locale: locale))"
        }
        let style = Date.FormatStyle(locale: locale, timeZone: timeZone).month(.abbreviated).day()
        return "Updated \(fetchedAt.formatted(style))"
    }

    /// "All day"; "Until 12:00 PM" for a timed event that began on an earlier day; else the start.
    public static func timeLabel(
        _ event: CalendarEvent, startOfDay: Date, timeZone: TimeZone, locale: Locale = .current
    ) -> String {
        if event.allDay { return allDayLabel }
        if event.start < startOfDay {
            let endOfDay = startOfDay.addingTimeInterval(24 * 3600)
            return event.end >= endOfDay ? allDayLabel :"Until \(time(event.end, timeZone: timeZone, locale: locale))"
        }
        return time(event.start, timeZone: timeZone, locale: locale)
    }

    /// `#RRGGBB` (the `#` optional) as `0xRRGGBB`.
    public static func color(hex: String?) -> UInt32? {
        guard var text = hex else { return nil }
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, text.allSatisfy(\.isHexDigit) else { return nil }
        return UInt32(text, radix: 16)
    }

    // MARK: -

    private static func section(
        _ title: String, _ events: [CalendarEvent], limit: Int, empty: String, startOfDay: Date,
        timeZone: TimeZone, locale: Locale
    ) -> HomeCalendarSection {
        let rows = events.prefix(limit).map {
            row($0, startOfDay: startOfDay, timeZone: timeZone, locale: locale)
        }
        return HomeCalendarSection(
            title: title, rows: rows, moreText: moreText(total: events.count, shown: rows.count),
            emptyText: events.isEmpty ? empty : nil)
    }

    /// One event as a row on the day that starts at `startOfDay` (Hub time). Shared with #143.
    static func row(_ event: CalendarEvent, startOfDay: Date, timeZone: TimeZone, locale: Locale) -> CalendarRow {
        let time = timeLabel(event, startOfDay: startOfDay, timeZone: timeZone, locale: locale)
        let title = event.busyOnly ? "Busy" : event.title
        return CalendarRow(
            id: event.id, time: time, title: title, color: color(hex: event.calendar.color),
            calendarName: event.calendar.name,
            accessibilityLabel: "\(time), \(title), \(event.calendar.name) calendar")
    }

    /// "+3 more" when fewer rows are shown than there are events.
    static func moreText(total: Int, shown: Int) -> String? {
        total > shown ? "+\(total - shown) more" : nil
    }

    /// "6:00 PM" in en_US, like the clock.
    private static func time(_ date: Date, timeZone: TimeZone, locale: Locale) -> String {
        let style = Date.FormatStyle(locale: locale, timeZone: timeZone).hour().minute()
        // ICU puts a narrow no-break space before AM/PM (as in `WeatherFormat.hourLabel`).
        return date.formatted(style)
            .replacingOccurrences(of: "\u{202F}", with: " ")
            .replacingOccurrences(of: "\u{00A0}", with: " ")
    }

    private static func dayKey(_ date: Date, calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}
