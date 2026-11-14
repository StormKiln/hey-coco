import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// A Hub whose `/v1/idle` answer can be changed between calls.
private final class FakeIdleHub: HubAPI {
    let result: Mutex<Result<IdleInfo, HubError>>
    let calls = Mutex(0)

    init(_ result: Result<IdleInfo, HubError>) {
        self.result = Mutex(result)
    }

    func set(_ new: Result<IdleInfo, HubError>) { result.withLock { $0 = new } }
    var callCount: Int { calls.withLock { $0 } }

    func idle() async throws(HubError) -> IdleInfo {
        calls.withLock { $0 += 1 }
        return try result.withLock { $0 }.get()
    }
    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action { throw .unreachable("") }
    func timers() async throws(HubError) -> TimersList { throw .unreachable("") }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo { throw .unreachable("") }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("") }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("") }
}

/// A sleeper that never returns until cancelled, so the refresh loop runs exactly once.
private struct ParkedSleeper: Sleeper {
    func sleep(for duration: Duration) async throws {
        try await Task.sleep(for: .seconds(3600))
    }
}

@MainActor
@Suite struct IdleModelTests {
    let idle = try! Fixtures.decode(IdleInfo.self, "idle_info")
    // 2026-11-14T15:04:05Z, the fixture's server_time.
    var fixtureNow: Date { idle.serverTime }

    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    @Test func startsLoadingThenFresh() async {
        let clock = TestClock(fixtureNow)
        let model = IdleModel(now: clock.function, sleeper: ParkedSleeper())
        #expect(model.status() == .loading)
        await model.refresh(using: FakeIdleHub(.success(idle)))
        #expect(model.info == idle)
        #expect(model.status() == .fresh)
    }

    @Test func failureKeepsLastGoodValueAndGoesStale() async {
        let clock = TestClock(fixtureNow)
        let hub = FakeIdleHub(.success(idle))
        let model = IdleModel(now: clock.function, sleeper: ParkedSleeper())
        await model.refresh(using: hub)
        hub.set(.failure(.unreachable("down")))
        clock.advance(by: 600)
        await model.refresh(using: hub)
        #expect(model.info == idle)
        #expect(model.status() == .stale)
    }

    @Test func hubReportedStaleWeatherIsStale() async {
        var stale = idle
        stale.weather?.stale = true
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        await model.refresh(using: FakeIdleHub(.success(stale)))
        #expect(model.status() == .stale)
    }

    @Test func noWeatherIsUnavailable() async throws {
        let none = try Fixtures.decode(IdleInfo.self, "idle_info_no_weather")
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        await model.refresh(using: FakeIdleHub(.success(none)))
        #expect(model.status() == .unavailable)
    }

    @Test func neverReachedIsUnavailableAfterAFailure() async {
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        await model.refresh(using: FakeIdleHub(.failure(.unreachable("down"))))
        #expect(model.info == nil)
        #expect(model.status() == .unavailable)
    }

    @Test func veryOldForecastBecomesUnavailable() async {
        let clock = TestClock(fixtureNow)
        let model = IdleModel(now: clock.function, sleeper: ParkedSleeper())
        await model.refresh(using: FakeIdleHub(.success(idle)))
        clock.advance(by: 7 * 3600)
        #expect(model.status() == .unavailable)
    }

    @Test func connectRefreshesImmediately() async {
        let hub = FakeIdleHub(.success(idle))
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        model.connect(hub)
        #expect(await eventually { model.info == idle })
        #expect(hub.callCount == 1)
        model.disconnect()
    }

    @Test func reconnectRefreshesAgain() async {
        let hub = FakeIdleHub(.success(idle))
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        model.connect(hub)
        #expect(await eventually { hub.callCount == 1 })
        model.connect(hub)
        #expect(await eventually { hub.callCount == 2 })
        model.disconnect()
    }

    @Test func reconnectAfterAFailureRefreshesAtOnce() async {
        let hub = FakeIdleHub(.failure(.unreachable("down")))
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        model.connect(hub)
        #expect(await eventually { hub.callCount == 1 })
        hub.set(.success(idle))
        model.reconnected(hub)
        #expect(await eventually { model.info == idle })
        model.disconnect()
    }

    @Test func reconnectWhileFreshDoesNotRefetch() async throws {
        let hub = FakeIdleHub(.success(idle))
        let model = IdleModel(now: TestClock(fixtureNow).function, sleeper: ParkedSleeper())
        model.connect(hub)
        #expect(await eventually { model.info == idle })
        model.reconnected(hub)
        try await Task.sleep(for: .milliseconds(50))
        #expect(hub.callCount == 1)
        model.disconnect()
    }

    @Test func upcomingHoursSkipsPastHoursAndLimits() async {
        let clock = TestClock(fixtureNow)
        let model = IdleModel(now: clock.function, sleeper: ParkedSleeper())
        await model.refresh(using: FakeIdleHub(.success(idle)))
        #expect(model.upcomingHours(count: 6).count == 6)
        clock.advance(by: 2 * 3600)  // 17:04Z: the 16:00 hour is past
        let hours = model.upcomingHours(count: 3)
        #expect(hours.map(\.time) == [
            Date(timeIntervalSince1970: idle.weather!.hourly[1].time.timeIntervalSince1970),
            idle.weather!.hourly[2].time, idle.weather!.hourly[3].time,
        ])
    }
}

@Suite struct WeatherFormatTests {
    @Test func temperatureRounds() {
        #expect(WeatherFormat.temperature(4.5) == "5°")
        #expect(WeatherFormat.temperature(4.4) == "4°")
        #expect(WeatherFormat.temperature(-0.4) == "0°")
        #expect(WeatherFormat.temperature(-3.6) == "-4°")
    }

    @Test func hourLabel() {
        let tz = TimeZone(identifier: "America/Toronto")!
        let locale = Locale(identifier: "en_US")
        let t = ISO8601DateFormatter().date(from: "2026-11-14T20:00:00Z")!  // 3 PM EST
        #expect(WeatherFormat.hourLabel(t, timeZone: tz, locale: locale) == "3 PM")
        let midnight = ISO8601DateFormatter().date(from: "2026-11-15T05:00:00Z")!
        #expect(WeatherFormat.hourLabel(midnight, timeZone: tz, locale: locale) == "12 AM")
    }

    @Test func symbolsByWMOCode() {
        #expect(WeatherFormat.symbolName(code: 0, isDay: true) == "sun.max.fill")
        #expect(WeatherFormat.symbolName(code: 0, isDay: false) == "moon.stars.fill")
        #expect(WeatherFormat.symbolName(code: 2, isDay: true) == "cloud.sun.fill")
        #expect(WeatherFormat.symbolName(code: 2, isDay: false) == "cloud.moon.fill")
        #expect(WeatherFormat.symbolName(code: 3, isDay: true) == "cloud.fill")
        #expect(WeatherFormat.symbolName(code: 45, isDay: true) == "cloud.fog.fill")
        #expect(WeatherFormat.symbolName(code: 53, isDay: true) == "cloud.drizzle.fill")
        #expect(WeatherFormat.symbolName(code: 61, isDay: true) == "cloud.rain.fill")
        #expect(WeatherFormat.symbolName(code: 65, isDay: true) == "cloud.heavyrain.fill")
        #expect(WeatherFormat.symbolName(code: 67, isDay: true) == "cloud.sleet.fill")
        #expect(WeatherFormat.symbolName(code: 73, isDay: true) == "cloud.snow.fill")
        #expect(WeatherFormat.symbolName(code: 80, isDay: true) == "cloud.sun.rain.fill")
        #expect(WeatherFormat.symbolName(code: 80, isDay: false) == "cloud.moon.rain.fill")
        #expect(WeatherFormat.symbolName(code: 95, isDay: true) == "cloud.bolt.rain.fill")
        #expect(WeatherFormat.symbolName(code: 1234, isDay: true) == "thermometer.medium")
    }

    @Test func daytimeHeuristic() {
        let tz = TimeZone(identifier: "America/Toronto")!
        let noon = ISO8601DateFormatter().date(from: "2026-11-14T17:00:00Z")!
        let night = ISO8601DateFormatter().date(from: "2026-11-15T03:00:00Z")!
        #expect(WeatherFormat.isDaytime(noon, timeZone: tz))
        #expect(!WeatherFormat.isDaytime(night, timeZone: tz))
    }
}
