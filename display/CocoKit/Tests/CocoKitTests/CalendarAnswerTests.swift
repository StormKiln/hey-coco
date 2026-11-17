import Foundation
import Testing
@testable import CocoKit

// #143: the calendar screen for voice answers ("what's on Saturday?", "when is soccer?").
// Fictional calendars and events only.

private let chicago = TimeZone(identifier: "America/Chicago")!
private let enUS = Locale(identifier: "en_US")
private let family = EventCalendar(name: "Family", color: "#33B679")
private let work = EventCalendar(name: "Work", color: "#1A73E8")

private func at(_ iso: String) -> Date {
    try! Date(iso, strategy: .iso8601)
}

private func local(_ day: Int, _ hour: Int = 0, _ minute: Int = 0) -> Date {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = chicago
    return cal.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
}

/// A timed event in November 2026, Chicago time.
private func event(
    _ title: String, day: Int, hour: Int, minute: Int = 0, hours: Int = 1, calendar: EventCalendar = family,
    busy: Bool = false
) -> CalendarEvent {
    let start = local(day, hour, minute)
    return CalendarEvent(
        id: "\(title)-\(day)-\(hour)-\(minute)", title: busy ? "Busy" : title, start: start,
        end: start.addingTimeInterval(TimeInterval(hours * 3600)), allDay: false, calendar: calendar,
        busyOnly: busy)
}

private func allDay(_ title: String, day: Int, days: Int = 1, calendar: EventCalendar = family) -> CalendarEvent {
    CalendarEvent(
        id: "\(title)-\(day)", title: title, start: local(day), end: local(day + days), allDay: true,
        calendar: calendar, busyOnly: false)
}

private func payload(
    _ title: String, query: String? = nil, _ days: [CalendarDay], stale: Bool = false,
    fetchedAt: Date? = at("2026-11-24T15:52:00Z")
) -> ViewSpec.CalendarPayload {
    ViewSpec.CalendarPayload(title: title, query: query, days: days, stale: stale, fetchedAt: fetchedAt)
}

/// Tuesday 2026-11-24, 10:00 AM in Chicago.
private let morning = at("2026-11-24T16:00:00Z")

private func content(_ payload: ViewSpec.CalendarPayload, now: Date = morning) -> CalendarAnswerContent {
    CalendarAnswer.content(payload, timeZone: chicago, now: now, locale: enUS)
}

@Suite struct CalendarAnswerTests {
    @Test func dayHeaderNamesTheWeekdayAndDate() {
        let shown = content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: [])]))
        #expect(shown.title == "Saturday")
        #expect(shown.days.map(\.header) == ["Saturday, November 28"])
        #expect(shown.days.map(\.id) == ["2026-11-28"])
    }

    @Test func rowsShowColourTimeTitleAndCalendar() throws {
        let day = CalendarDay(
            date: "2026-11-28",
            events: [allDay("Camping trip", day: 28), event("Standup", day: 28, hour: 9, calendar: work),
                     event("Soccer practice", day: 28, hour: 18, minute: 30)])
        let rows = try #require(content(payload("Saturday", [day])).days.first).rows
        #expect(rows.map(\.time) == ["All day", "9:00 AM", "6:30 PM"])
        #expect(rows.map(\.title) == ["Camping trip", "Standup", "Soccer practice"])
        #expect(rows.map(\.color) == [0x33B679, 0x1A73E8, 0x33B679])
        #expect(rows.map(\.calendarName) == ["Family", "Work", "Family"])
        #expect(rows[1].accessibilityLabel == "9:00 AM, Standup, Work calendar")
    }

    @Test func busyOnlyRowsSayBusy() throws {
        var private_ = event("Standup", day: 28, hour: 14, calendar: work)
        private_.busyOnly = true
        let row = try #require(content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: [private_])]))
            .days.first?.rows.first)
        #expect(row.title == "Busy")
        #expect(row.time == "2:00 PM")
    }

    @Test func allDayRowsComeFirstIncludingTimedEventsThatFillTheDay() throws {
        // A timed event spanning the whole day reads "All day", so it sorts with the all-day rows.
        var conference = event("Conference", day: 27, hour: 8, hours: 3 * 24, calendar: work)
        conference.id = "conference"
        let day = CalendarDay(
            date: "2026-11-28",
            events: [allDay("Camping trip", day: 27, days: 3), event("Breakfast", day: 28, hour: 8), conference,
                     event("Soccer practice", day: 28, hour: 10)])
        let rows = try #require(content(payload("Saturday", [day])).days.first).rows
        #expect(rows.map(\.title) == ["Camping trip", "Conference", "Breakfast", "Soccer practice"])
        #expect(rows.map(\.time) == ["All day", "All day", "8:00 AM", "10:00 AM"])
    }

    @Test func timedEventFromTheDayBeforeSaysUntilWhen() throws {
        var overnight = event("Night shift", day: 27, hour: 22, hours: 14, calendar: work)
        overnight.id = "overnight"
        let row = try #require(content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: [overnight])]))
            .days.first?.rows.first)
        #expect(row.time == "Until 12:00 PM")
    }

    @Test func pastEventsStayOnAnAnswer() throws {
        // Unlike the home screen, an answer lists the whole day it was asked about.
        let day = CalendarDay(
            date: "2026-11-24", events: [event("Breakfast", day: 24, hour: 7), event("Piano", day: 24, hour: 16)])
        let rows = try #require(content(payload("Today", [day])).days.first).rows
        #expect(rows.map(\.title) == ["Breakfast", "Piano"])
    }

    @Test func weekendListsEachDay() {
        let shown = content(
            payload(
                "This weekend",
                [CalendarDay(date: "2026-11-28", events: [event("Soccer practice", day: 28, hour: 9)]),
                 CalendarDay(date: "2026-11-29", events: [])]))
        #expect(shown.days.map(\.header) == ["Saturday, November 28", "Sunday, November 29"])
        #expect(shown.days[0].emptyText == nil)
        #expect(shown.days[1].rows.isEmpty)
        #expect(shown.days[1].emptyText == "Nothing on the calendar")
        #expect(shown.emptyText == nil)
    }

    @Test func longDaysShowEightRowsAndHowManyMore() throws {
        let events = (7...18).map { event("Lesson \($0)", day: 28, hour: $0) }
        let day = try #require(content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: events)]))
            .days.first)
        #expect(CalendarAnswer.dayLimit == 8)
        #expect(day.rows.count == 8)
        #expect(day.rows.last?.title == "Lesson 14")
        #expect(day.moreText == "+4 more")
    }

    @Test func exactlyEightFitWithoutMore() throws {
        let events = (7...14).map { event("Lesson \($0)", day: 28, hour: $0) }
        let day = try #require(content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: events)]))
            .days.first)
        #expect(day.rows.count == 8)
        #expect(day.moreText == nil)
    }

    @Test func searchWithNoMatchSaysNothingFound() {
        let shown = content(payload("Soccer practice", query: "soccer", []))
        #expect(shown.title == "Soccer practice")
        #expect(shown.days.isEmpty)
        #expect(shown.emptyText == "Nothing found on the calendar")
    }

    @Test func dayQuestionWithNoDaysSaysNothingOnTheCalendar() {
        #expect(content(payload("Saturday", [])).emptyText == "Nothing on the calendar")
    }

    @Test func searchSpanningSeveralDays() {
        let days = [25, 27, 30].map { d in
            CalendarDay(date: "2026-11-\(d)", events: [event("Soccer practice", day: d, hour: 17, minute: 30)])
        }
        let shown = content(payload("Soccer practice", query: "soccer", days))
        #expect(
            shown.days.map(\.header)
                == ["Wednesday, November 25", "Friday, November 27", "Monday, November 30"])
        #expect(shown.days.flatMap(\.rows).map(\.time) == ["5:30 PM", "5:30 PM", "5:30 PM"])
    }

    @Test func timesAndHeadersUseTheHubsTimezone() throws {
        // 00:30 UTC on the 29th is still the evening of the 28th in Chicago.
        let late = CalendarEvent(
            id: "late", title: "Movie night", start: at("2026-11-29T00:30:00Z"), end: at("2026-11-29T02:30:00Z"),
            allDay: false, calendar: family, busyOnly: false)
        let shown = content(payload("Saturday", [CalendarDay(date: "2026-11-28", events: [late])]))
        #expect(shown.days.first?.header == "Saturday, November 28")
        #expect(shown.days.first?.rows.first?.time == "6:30 PM")
        let toronto = CalendarAnswer.content(
            payload("Saturday", [CalendarDay(date: "2026-11-28", events: [late])]),
            timeZone: TimeZone(identifier: "America/Toronto")!, now: morning, locale: enUS)
        #expect(toronto.days.first?.rows.first?.time == "7:30 PM")
    }

    @Test func badDateFallsBackToTheRawText() {
        #expect(content(payload("Saturday", [CalendarDay(date: "someday", events: [])])).days.first?.header == "someday")
    }

    @Test func staleLabelLikeTheHomeScreen() {
        #expect(content(payload("Saturday", [])).staleLabel == nil)
        // 15:52Z is 9:52 AM in Chicago.
        #expect(content(payload("Saturday", [], stale: true)).staleLabel == "Updated 9:52 AM")
        #expect(
            content(payload("Saturday", [], stale: true, fetchedAt: at("2026-11-23T20:00:00Z"))).staleLabel
                == "Updated Nov 23")
        #expect(content(payload("Saturday", [], stale: true, fetchedAt: nil)).staleLabel == "Not updated")
    }

    @Test func protocolFixtureReadsAsExpected() throws {
        let action = try Fixtures.decode(Action.self, "action_calendar")
        guard case .calendar(let calendar) = action.view else {
            Issue.record("not a calendar view")
            return
        }
        // The fixture's Hub is in Chicago (Camping trip starts at 06:00Z = local midnight).
        let shown = CalendarAnswer.content(calendar, timeZone: chicago, now: at("2026-11-24T16:00:00Z"), locale: enUS)
        #expect(shown.title == "Saturday")
        #expect(shown.days.map(\.header) == ["Saturday, November 28"])
        #expect(shown.days.first?.rows.map(\.time) == ["All day", "9:00 AM"])
        #expect(shown.days.first?.rows.map(\.title) == ["Camping trip", "Call with Grandma"])
        #expect(shown.staleLabel == "Updated 9:52 AM")
    }

    @Test func calendarIsAViewTypeWithACloseButton() {
        let view = ViewSpec.calendar(payload("Saturday", []))
        #expect(view.viewType == .calendar)
        #expect(view.showsCloseButton)
    }
}
