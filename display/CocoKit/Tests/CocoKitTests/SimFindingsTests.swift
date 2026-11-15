import Foundation
import Testing
@testable import CocoKit

private let toronto = TimeZone(identifier: "America/Toronto")!
nonisolated(unsafe) private let iso = ISO8601DateFormatter()

private func hour(_ time: String, precip: Int = 0) -> WeatherHour {
    WeatherHour(time: iso.date(from: time)!, temp: 3, unit: .celsius, code: 3, precipProbability: precip)
}

private func day(_ date: String) -> WeatherDay {
    WeatherDay(date: date, high: 5, low: -1, unit: .celsius, code: 61, precipProbability: 40)
}

// MARK: - Weather screen

@Suite struct WeatherForecastTests {
    // 2026-11-14 10:30 in Toronto (Saturday).
    let now = iso.date(from: "2026-11-14T15:30:00Z")!

    @Test func precipShownFromTwentyPercent() {
        #expect(WeatherForecast.precipText(0) == nil)
        #expect(WeatherForecast.precipText(19) == nil)
        #expect(WeatherForecast.precipText(20) == "20%")
        #expect(WeatherForecast.precipText(85) == "85%")
    }

    @Test func hoursStartWithTheHourInProgressAndAreCapped() {
        let hourly = (0..<30).map { i in
            hour(iso.string(from: iso.date(from: "2026-11-14T12:00:00Z")!.addingTimeInterval(Double(i) * 3600)))
        }.shuffled()
        let shown = WeatherForecast.upcomingHours(hourly, at: now)
        #expect(shown.count == 12)
        #expect(shown.first?.time == iso.date(from: "2026-11-14T15:00:00Z"))
        #expect(shown == shown.sorted { $0.time < $1.time })
    }

    @Test func fewerHoursThanTheStripIsFine() {
        let shown = WeatherForecast.upcomingHours([hour("2026-11-14T16:00:00Z")], at: now)
        #expect(shown.count == 1)
    }

    @Test func daysFromTodayInTheHubsTimezone() {
        // 2026-11-15 03:00Z is still the 14th in Toronto.
        let lateEvening = iso.date(from: "2026-11-15T03:00:00Z")!
        let daily = ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16", "bad"].map(day)
        let shown = WeatherForecast.upcomingDays(daily, at: lateEvening, timeZone: toronto)
        #expect(shown.map(\.date) == ["2026-11-14", "2026-11-15", "2026-11-16"])
        let utc = WeatherForecast.upcomingDays(daily, at: lateEvening, timeZone: TimeZone(identifier: "UTC")!)
        #expect(utc.map(\.date) == ["2026-11-15", "2026-11-16"])
    }

    @Test func atMostSevenDays() {
        let daily = (14...28).map { day("2026-11-\($0)") }
        #expect(WeatherForecast.upcomingDays(daily, at: now, timeZone: toronto).count == 7)
    }

    @Test func dayLabels() {
        let locale = Locale(identifier: "en_US")
        func label(_ date: String) -> String {
            WeatherForecast.dayLabel(day(date), at: now, timeZone: toronto, locale: locale)
        }
        #expect(label("2026-11-14") == "Today")
        #expect(label("2026-11-15") == "Tomorrow")
        #expect(label("2026-11-16") == "Monday")
        #expect(label("2026-11-20") == "Friday")
        #expect(label("nonsense") == "nonsense")
    }

    @Test func dayLabelsFollowTheHubsDateNotUTC() {
        // 03:00Z on the 15th is the evening of the 14th in Toronto: the 15th is "Tomorrow".
        let lateEvening = iso.date(from: "2026-11-15T03:00:00Z")!
        let label = WeatherForecast.dayLabel(
            day("2026-11-15"), at: lateEvening, timeZone: toronto, locale: Locale(identifier: "en_US"))
        #expect(label == "Tomorrow")
    }

    @Test func everyWeatherSymbolHasTintsThatAreNotAllWhite() {
        let codes = [0, 1, 2, 3, 45, 51, 56, 61, 65, 71, 80, 82, 95, 9999]
        for code in codes {
            for isDay in [true, false] {
                let symbol = WeatherFormat.symbolName(code: code, isDay: isDay)
                let tints = WeatherFormat.tints(forSymbol: symbol)
                #expect(!tints.isEmpty, "\(symbol)")
                if symbol.hasPrefix("cloud") { #expect(tints.first == .cloud, "\(symbol)") }
            }
        }
        #expect(WeatherFormat.tints(forSymbol: "sun.max.fill") == [.sun])
        #expect(WeatherFormat.tints(forSymbol: "cloud.sun.rain.fill") == [.cloud, .sun, .rain])
    }
}

// MARK: - Picture captions

@Suite struct PictureCaptionTests {
    struct Case: Sendable, CustomTestStringConvertible {
        var title: String
        var author: String?
        var query: String
        var expected: String
        var testDescription: String { title }
    }

    static let cases: [Case] = [
        Case(
            title: "File:006 Giraffe portrait in the Lake Manyara National Park Photo by Giles Laurent.jpg",
            author: "Giles Laurent", query: "giraffe",
            expected: "Giraffe portrait in the Lake Manyara National Park"),
        Case(title: "006 Giraffe portrait Photo by Giles Laurent", author: nil, query: "giraffe",
             expected: "Giraffe portrait"),
        Case(title: "File:Red_fox_(Vulpes_vulpes).jpg", author: nil, query: "fox",
             expected: "Red fox (Vulpes vulpes)"),
        Case(title: "File:IMG_1234.JPG", author: "Jane", query: "giraffe", expected: "Giraffe"),
        Case(title: "DSC01234.jpg", author: nil, query: "cow", expected: "Cow"),
        Case(title: "File:PXL_20230101_123456789.jpg", author: nil, query: "otter", expected: "Otter"),
        Case(title: "File:IMG_1234 Sleeping cat.jpg", author: nil, query: "cat", expected: "Sleeping cat"),
        Case(title: "File:Highland cow by Jane Doe.jpeg", author: "Jane Doe", query: "cow",
             expected: "Highland cow"),
        Case(title: "File:Highland cow Jane Doe.png", author: "Jane Doe", query: "cow",
             expected: "Highland cow"),
        Case(title: "Heron standing by the lake.jpg", author: "Someone Else", query: "heron",
             expected: "Heron standing by the lake"),
        Case(title: "File:Puffin (photo: A. Person).jpg", author: nil, query: "puffin", expected: "Puffin"),
        Case(title: "File:Eiffel Tower © Someone.jpg", author: nil, query: "eiffel tower", expected: "Eiffel Tower"),
        Case(title: "File:Elephant 2019-05-04.jpg", author: nil, query: "elephant", expected: "Elephant"),
        Case(title: "File:Photo by Giles Laurent.jpg", author: "Giles Laurent", query: "lion", expected: "Lion"),
        Case(title: "", author: nil, query: "zebra", expected: "Zebra"),
        Case(
            title: "File:A very long description of a herd of African elephants crossing the dusty plains at sunset in Kenya.jpg",
            author: nil, query: "elephant",
            expected: "A very long description of a herd of African elephants…"),
    ]

    @Test(arguments: cases) func tidies(_ c: Case) {
        #expect(PictureCaption.tidy(c.title, author: c.author, fallback: c.query) == c.expected)
    }

    @Test func longCaptionsStayWithinTheLimit() {
        let long = String(repeating: "word ", count: 40)
        let caption = PictureCaption.tidy(long, fallback: "x")
        #expect(caption.count <= PictureCaption.maxLength)
        #expect(caption.hasSuffix("…"))
        #expect(!caption.contains(" …"))
    }
}

// MARK: - Night mode wakes for new screens

@MainActor
@Suite struct AppModelNightWakeTests {
    final class Clock: @unchecked Sendable {
        var now: Date
        init(_ now: Date) { self.now = now }
    }

    private func makeModel() -> (AppModel, NightModeController) {
        // 23:00 in Toronto: inside the night window.
        let clock = Clock(iso.date(from: "2026-11-15T04:00:00Z")!)
        let night = NightModeController(
            brightness: FakeScreenBrightness(), timeZone: toronto,
            sleep: { _ in try await Task.sleep(for: .seconds(3600)) }, now: { clock.now })
        night.update(night: Night(start: "21:30", end: "06:30", darkAfterIdleS: 300))
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-night-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        let model = AppModel(
            config: config, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
            makeClient: { _ in FakeHubAPI() },
            makeEvents: { _ in AsyncStream { _ in } },
            night: night)
        night.sleepNow()
        return (model, night)
    }

    private func action(say: String?, view: ViewSpec?) -> Action {
        Action(say: say, view: view, commands: [], intent: "test", matchedBy: .rules)
    }

    @Test func aNewScreenWakesTheDarkScreen() {
        let (model, night) = makeModel()
        #expect(night.phase == .dark)
        model.apply(action(say: nil, view: .timers))
        #expect(night.phase == .dim)
    }

    @Test func aSpokenReplyWakesTheDarkScreen() {
        let (model, night) = makeModel()
        model.apply(action(say: "It's 4 degrees.", view: nil))
        #expect(night.phase == .dim)
    }

    @Test func aSilentActionWithoutAViewStaysDark() {
        let (model, night) = makeModel()
        model.apply(action(say: nil, view: nil))
        #expect(night.phase == .dark)
        model.apply(action(say: nil, view: .unknown("future")))
        #expect(night.phase == .dark)
    }
}

// MARK: - Notification permission

@MainActor
@Suite struct NotificationPermissionTimingTests {
    final class CountingNotifications: AlarmNotifications, @unchecked Sendable {
        private let lock = NSLock()
        private var _authorizations = 0
        var authorizations: Int { lock.withLock { _authorizations } }
        func requestAuthorization() async { lock.withLock { _authorizations += 1 } }
        func add(_ alarm: AlarmRequest) async {}
        func remove(ids: [String]) async {}
    }

    final class NoTimers: OneShotTimers {
        func schedule(id: String, at date: Date, fire: @escaping @MainActor () -> Void) {}
        func cancel(id: String) {}
    }

    @Test func launchingWithNoTimersNeverAsks() async {
        let notifications = CountingNotifications()
        let scheduler = LocalAlarmScheduler(timers: NoTimers(), notifications: notifications)
        // What the app does at launch and on connecting: an empty store, then an empty hello.
        let store = TimerStore(scheduler: scheduler)
        store.apply(TimersList(timers: [], serverTime: Date()))
        await scheduler.flush()
        #expect(notifications.authorizations == 0)
    }

    @Test func asksOnTheFirstTimerAndNeverAgain() async {
        let notifications = CountingNotifications()
        let scheduler = LocalAlarmScheduler(timers: NoTimers(), notifications: notifications)
        let store = TimerStore(scheduler: scheduler)
        await store.create(durationS: 60, name: "eggs")
        await scheduler.flush()
        #expect(notifications.authorizations == 1)
        await store.cancel(id: store.timers[0].id)
        await store.create(durationS: 120, name: "pasta")
        await scheduler.flush()
        #expect(notifications.authorizations == 1)
    }
}
