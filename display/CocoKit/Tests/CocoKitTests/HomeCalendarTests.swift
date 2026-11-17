import Foundation
import Testing
@testable import CocoKit

// #142: which events the home screen lists. Fictional calendars and events only.

private let chicago = TimeZone(identifier: "America/Chicago")!
private let enUS = Locale(identifier: "en_US")
private let family = EventCalendar(name: "Family", color: "#33B679")
private let work = EventCalendar(name: "Work", color: "#1A73E8")

private func at(_ iso: String) -> Date {
    try! Date(iso, strategy: .iso8601)
}

/// A timed event on 2026-11-24 or 25, Chicago (UTC-6 in November): `hour` is local.
private func event(
    _ title: String, day: Int = 24, hour: Int, minute: Int = 0, hours: Int = 1, calendar: EventCalendar = family,
    busy: Bool = false
) -> CalendarEvent {
    let parts = DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute)
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = chicago
    let start = cal.date(from: parts)!
    return CalendarEvent(
        id: "\(title)-\(day)-\(hour)-\(minute)", title: busy ? "Busy" : title, start: start,
        end: start.addingTimeInterval(TimeInterval(hours * 3600)), allDay: false, calendar: calendar,
        busyOnly: busy)
}

private func allDay(_ title: String, day: Int = 24, calendar: EventCalendar = family) -> CalendarEvent {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = chicago
    let start = cal.date(from: DateComponents(year: 2026, month: 11, day: day))!
    return CalendarEvent(
        id: "\(title)-\(day)", title: title, start: start, end: cal.date(byAdding: .day, value: 1, to: start)!,
        allDay: true, calendar: calendar, busyOnly: false)
}

private func calendar(
    today: [CalendarEvent], tomorrow: [CalendarEvent], stale: Bool = false, fetchedAt: Date? = at("2026-11-24T15:52:00Z"),
    hideAtNight: Bool = false
) -> IdleCalendar {
    IdleCalendar(
        days: [CalendarDay(date: "2026-11-24", events: today), CalendarDay(date: "2026-11-25", events: tomorrow)],
        stale: stale, fetchedAt: fetchedAt, hideAtNight: hideAtNight)
}

private let night = Night(start: "21:30", end: "06:30", darkAfterIdleS: 300)

private func content(_ calendar: IdleCalendar, now: Date) -> HomeCalendarContent? {
    HomeCalendar.content(calendar, timeZone: chicago, night: night, now: now, locale: enUS)
}

/// 10:00 AM in Chicago.
private let morning = at("2026-11-24T16:00:00Z")

@Suite struct HomeCalendarTests {
    @Test func noCalendarMeansNoCalendarContent() {
        #expect(HomeCalendar.content(nil, timeZone: chicago, night: night, now: morning, locale: enUS) == nil)
    }

    @Test func rowsShowColourTimeAndTitle() throws {
        let cal = calendar(
            today: [allDay("School holiday"), event("Standup", hour: 14, calendar: work, busy: true),
                    event("Soccer practice", hour: 18, minute: 30)],
            tomorrow: [])
        let today = try #require(content(cal, now: morning)).today
        #expect(today.title == "Today")
        #expect(today.rows.map(\.time) == ["All day", "2:00 PM", "6:30 PM"])
        #expect(today.rows.map(\.title) == ["School holiday", "Busy", "Soccer practice"])
        #expect(today.rows.map(\.color) == [0x33B679, 0x1A73E8, 0x33B679])
        #expect(today.moreText == nil)
        #expect(today.emptyText == nil)
    }

    @Test func busyOnlyRowsSayBusyWhateverTheTitle() throws {
        var private_ = event("Standup", hour: 14, calendar: work)
        private_.busyOnly = true
        let cal = calendar(today: [private_], tomorrow: [])
        let row = try #require(content(cal, now: morning)?.today.rows.first)
        #expect(row.title == "Busy")
        #expect(row.accessibilityLabel == "2:00 PM, Busy, Work calendar")
    }

    @Test func accessibilityLabelReadsTimeTitleAndCalendar() throws {
        let cal = calendar(today: [allDay("School holiday")], tomorrow: [])
        let row = try #require(content(cal, now: morning)?.today.rows.first)
        #expect(row.accessibilityLabel == "All day, School holiday, Family calendar")
    }

    @Test func endedEventsDropOffByTheClockButInProgressOnesStay() throws {
        let cal = calendar(
            today: [event("Breakfast", hour: 8), event("Standup", hour: 9, minute: 30, calendar: work),
                    event("Soccer practice", hour: 18)],
            tomorrow: [])
        // 10:00: breakfast (8–9) is over, standup (9:30–10:30) is on.
        #expect(try #require(content(cal, now: morning)).today.rows.map(\.title) == ["Standup", "Soccer practice"])
        // 10:30 exactly: standup has ended.
        let later = morning.addingTimeInterval(30 * 60)
        #expect(try #require(content(cal, now: later)).today.rows.map(\.title) == ["Soccer practice"])
    }

    @Test func allDayEventsStayAllDay() throws {
        let cal = calendar(today: [allDay("School holiday")], tomorrow: [])
        let lateEvening = at("2026-11-25T05:59:00Z")  // 11:59 PM
        #expect(try #require(content(cal, now: lateEvening)).today.rows.map(\.title) == ["School holiday"])
    }

    @Test func busyDayShowsFiveRowsAndHowManyMore() throws {
        let today = (11...18).map { event("Lesson \($0)", hour: $0) }
        let cal = calendar(today: today, tomorrow: [event("Dentist", day: 25, hour: 9)])
        let shown = try #require(content(cal, now: morning))
        #expect(shown.today.rows.count == 5)
        #expect(shown.today.rows.first?.title == "Lesson 11")
        #expect(shown.today.moreText == "+3 more")
        #expect(shown.tomorrow == nil)
    }

    @Test func exactlyFiveFitWithoutMore() throws {
        let cal = calendar(today: (11...15).map { event("Lesson \($0)", hour: $0) }, tomorrow: [])
        let today = try #require(content(cal, now: morning)).today
        #expect(today.rows.count == 5)
        #expect(today.moreText == nil)
    }

    @Test func endedEventsDoNotCountTowardsMore() throws {
        // Seven events; 8–9 and 9–10 are over at 10:00: five left, no "+N more".
        let cal = calendar(today: (8...14).map { event("Lesson \($0)", hour: $0) }, tomorrow: [])
        let today = try #require(content(cal, now: morning)).today
        #expect(today.rows.map(\.title) == (10...14).map { "Lesson \($0)" })
        #expect(today.moreText == nil)
    }

    @Test func tomorrowOnlyWhenThreeOrFewerLeftToday() throws {
        let tomorrow = [allDay("Bin day", day: 25), event("Dentist", day: 25, hour: 9, minute: 15),
                        event("Standup", day: 25, hour: 10, calendar: work), event("Piano", day: 25, hour: 16),
                        event("Book club", day: 25, hour: 19)]
        let three = calendar(today: (12...14).map { event("Lesson \($0)", hour: $0) }, tomorrow: tomorrow)
        let shown = try #require(content(three, now: morning)?.tomorrow)
        #expect(shown.title == "Tomorrow")
        #expect(shown.rows.map(\.time) == ["All day", "9:15 AM", "10:00 AM"])
        #expect(shown.moreText == "+2 more")

        let four = calendar(today: (12...15).map { event("Lesson \($0)", hour: $0) }, tomorrow: tomorrow)
        #expect(try #require(content(four, now: morning)).tomorrow == nil)
    }

    @Test func tomorrowAppearsOnceTodayThinsOut() throws {
        // Four left at 10:00; at 11:00 the first has ended, leaving three.
        let cal = calendar(
            today: [event("Standup", hour: 10, calendar: work)] + (12...14).map { event("Lesson \($0)", hour: $0) },
            tomorrow: [event("Dentist", day: 25, hour: 9)])
        #expect(try #require(content(cal, now: morning)).tomorrow == nil)
        let eleven = morning.addingTimeInterval(3600)
        #expect(try #require(content(cal, now: eleven)).tomorrow?.rows.map(\.title) == ["Dentist"])
    }

    @Test func emptyDaysSayNothingElse() throws {
        let cal = calendar(today: [event("Breakfast", hour: 8)], tomorrow: [])
        let shown = try #require(content(cal, now: morning))
        #expect(shown.today.rows.isEmpty)
        #expect(shown.today.emptyText == "Nothing else today")
        #expect(shown.tomorrow?.rows.isEmpty == true)
        #expect(shown.tomorrow?.emptyText == "Nothing tomorrow")
    }

    @Test func afterMidnightYesterdaysTomorrowBecomesToday() throws {
        // No fetch since yesterday: the Hub's "tomorrow" is today now, and there's no tomorrow to show.
        let cal = calendar(today: [event("Soccer practice", hour: 18)], tomorrow: [event("Dentist", day: 25, hour: 9)])
        let pastMidnight = at("2026-11-25T06:30:00Z")  // 12:30 AM on the 25th
        let shown = try #require(content(cal, now: pastMidnight))
        #expect(shown.today.rows.map(\.title) == ["Dentist"])
        #expect(shown.tomorrow == nil)
    }

    @Test func timedEventFromYesterdaySaysUntilWhen() throws {
        var overnight = event("Night shift", day: 23, hour: 22, hours: 14, calendar: work)
        overnight.id = "overnight"
        let cal = calendar(today: [overnight], tomorrow: [])
        #expect(try #require(content(cal, now: morning)).today.rows.first?.time == "Until 12:00 PM")
    }

    @Test func timesUseTheHubsTimezone() throws {
        let cal = calendar(today: [event("Soccer practice", hour: 18)], tomorrow: [])
        let shown = try #require(
            HomeCalendar.content(cal, timeZone: chicago, night: nil, now: morning, locale: enUS))
        #expect(shown.today.rows.first?.time == "6:00 PM")
        let toronto = TimeZone(identifier: "America/Toronto")!
        let other = HomeCalendar.content(
            IdleCalendar(days: [CalendarDay(date: "2026-11-24", events: cal.days[0].events)], stale: false,
                         fetchedAt: nil, hideAtNight: false),
            timeZone: toronto, night: nil, now: morning, locale: enUS)
        #expect(other?.today.rows.first?.time == "7:00 PM")
    }

    @Test func staleLabel() throws {
        let fresh = calendar(today: [], tomorrow: [])
        #expect(try #require(content(fresh, now: morning)).staleLabel == nil)
        // 15:52Z is 9:52 AM in Chicago.
        let stale = calendar(today: [], tomorrow: [], stale: true)
        #expect(try #require(content(stale, now: morning)).staleLabel == "Updated 9:52 AM")
        let yesterday = calendar(today: [], tomorrow: [], stale: true, fetchedAt: at("2026-11-23T20:00:00Z"))
        #expect(try #require(content(yesterday, now: morning)).staleLabel == "Updated Nov 23")
        let never = calendar(today: [], tomorrow: [], stale: true, fetchedAt: nil)
        #expect(try #require(content(never, now: morning)).staleLabel == "Not updated")
    }

    @Test func hiddenAtNightOnlyWhenAskedAndInsideTheWindow() {
        let late = at("2026-11-25T04:00:00Z")  // 10:00 PM, inside 21:30–06:30
        let shown = calendar(today: [allDay("School holiday")], tomorrow: [], hideAtNight: false)
        #expect(content(shown, now: late) != nil)
        let hidden = calendar(today: [allDay("School holiday")], tomorrow: [], hideAtNight: true)
        #expect(content(hidden, now: late) == nil)
        #expect(content(hidden, now: morning) != nil)
        // No night window known: never hidden.
        #expect(HomeCalendar.content(hidden, timeZone: chicago, night: nil, now: late, locale: enUS) != nil)
    }

    @Test func colourParsesHexOrIsNil() {
        #expect(HomeCalendar.color(hex: "#33B679") == 0x33B679)
        #expect(HomeCalendar.color(hex: "33b679") == 0x33B679)
        #expect(HomeCalendar.color(hex: nil) == nil)
        #expect(HomeCalendar.color(hex: "#GGGGGG") == nil)
        #expect(HomeCalendar.color(hex: "#123") == nil)
    }

    @Test func protocolFixtureReadsAsExpected() throws {
        let info = try Fixtures.decode(IdleInfo.self, "idle_info_calendar")
        let shown = try #require(
            HomeCalendar.content(
                info.calendar, timeZone: TimeZone(identifier: info.timezone)!, night: info.night,
                now: info.serverTime, locale: enUS))
        #expect(shown.today.rows.map(\.time) == ["All day", "2:00 PM", "6:00 PM"])
        #expect(shown.today.rows.map(\.title) == ["School holiday", "Busy", "Soccer practice"])
        #expect(shown.tomorrow?.rows.map(\.title) == ["Dentist"])
        #expect(shown.tomorrow?.rows.first?.time == "9:30 AM")
    }
}
