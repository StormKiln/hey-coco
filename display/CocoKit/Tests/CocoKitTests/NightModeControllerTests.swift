import Foundation
import Testing
@testable import CocoKit

@MainActor
private final class NightClock {
    var now: Date
    init(_ now: Date) { self.now = now }
    func advance(_ seconds: TimeInterval) { now = now.addingTimeInterval(seconds) }
}

private let toronto = TimeZone(identifier: "America/Toronto")!

/// A wall-clock time in Toronto.
private func at(_ y: Int, _ mo: Int, _ d: Int, _ h: Int, _ mi: Int) -> Date {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = toronto
    return calendar.date(from: DateComponents(year: y, month: mo, day: d, hour: h, minute: mi))!
}

private let bedtime = Night(start: "21:30", end: "06:30", darkAfterIdleS: 300)

@MainActor
private struct Harness {
    let clock: NightClock
    let brightness = FakeScreenBrightness(level: 0.8)
    let sleeper = ManualSleeper()
    let night: NightModeController

    init(_ start: Date, schedule: Night? = bedtime) {
        clock = NightClock(start)
        night = NightModeController(
            brightness: brightness, timeZone: toronto, tickInterval: .seconds(15),
            sleep: sleeper.sleep, now: { [clock] in clock.now })
        if let schedule { night.update(night: schedule) }
    }
}

@MainActor
@Suite struct NightScheduleTests {
    @Test func parsesHubTimes() {
        let schedule = NightSchedule(bedtime)
        #expect(schedule?.startMinute == 21 * 60 + 30)
        #expect(schedule?.endMinute == 6 * 60 + 30)
        #expect(schedule?.darkAfterIdle == 300)
    }

    @Test func rejectsMalformedTimes() {
        #expect(NightSchedule(Night(start: "9pm", end: "06:30", darkAfterIdleS: 60)) == nil)
        #expect(NightSchedule(Night(start: "24:00", end: "06:30", darkAfterIdleS: 60)) == nil)
        #expect(NightSchedule(Night(start: "21:60", end: "06:30", darkAfterIdleS: 60)) == nil)
        #expect(NightSchedule(Night(start: "21:30", end: "6", darkAfterIdleS: 60)) == nil)
    }

    @Test func windowCrossingMidnight() {
        let schedule = NightSchedule(bedtime)!
        #expect(!schedule.contains(at(2026, 11, 14, 21, 29), in: toronto))
        #expect(schedule.contains(at(2026, 11, 14, 21, 30), in: toronto))
        #expect(schedule.contains(at(2026, 11, 14, 23, 59), in: toronto))
        #expect(schedule.contains(at(2026, 11, 15, 0, 0), in: toronto))
        #expect(schedule.contains(at(2026, 11, 15, 6, 29), in: toronto))
        #expect(!schedule.contains(at(2026, 11, 15, 6, 30), in: toronto))
        #expect(!schedule.contains(at(2026, 11, 15, 12, 0), in: toronto))
    }

    @Test func windowWithinOneDay() {
        let schedule = NightSchedule(Night(start: "13:00", end: "15:00", darkAfterIdleS: 60))!
        #expect(!schedule.contains(at(2026, 11, 14, 12, 59), in: toronto))
        #expect(schedule.contains(at(2026, 11, 14, 13, 0), in: toronto))
        #expect(!schedule.contains(at(2026, 11, 14, 15, 0), in: toronto))
        #expect(!schedule.contains(at(2026, 11, 14, 23, 0), in: toronto))
    }

    @Test func equalStartAndEndIsNoWindow() {
        let schedule = NightSchedule(Night(start: "22:00", end: "22:00", darkAfterIdleS: 60))!
        #expect(!schedule.contains(at(2026, 11, 14, 22, 0), in: toronto))
        #expect(!schedule.contains(at(2026, 11, 14, 3, 0), in: toronto))
    }

    @Test func usesTheHubTimezoneNotTheDevices() {
        let schedule = NightSchedule(bedtime)!
        // 22:00 in Toronto is 12:00 next day in Tokyo: inside the window only by Toronto time.
        let date = at(2026, 11, 14, 22, 0)
        #expect(schedule.contains(date, in: toronto))
        #expect(!schedule.contains(date, in: TimeZone(identifier: "Asia/Tokyo")!))
    }

    @Test func springForwardDay() {
        // 2026-03-08: Toronto clocks jump from 02:00 to 03:00.
        let schedule = NightSchedule(Night(start: "01:30", end: "02:30", darkAfterIdleS: 60))!
        let before = at(2026, 3, 8, 1, 45)
        #expect(schedule.contains(before, in: toronto))
        // 20 real minutes later the wall clock reads 03:05: the window is over.
        #expect(!schedule.contains(before.addingTimeInterval(20 * 60), in: toronto))
        let bedtimeSchedule = NightSchedule(bedtime)!
        #expect(bedtimeSchedule.contains(at(2026, 3, 8, 6, 29), in: toronto))
        #expect(!bedtimeSchedule.contains(at(2026, 3, 8, 6, 30), in: toronto))
    }

    @Test func fallBackDay() {
        // 2026-11-01: Toronto clocks go from 02:00 back to 01:00; 01:00–02:00 happens twice.
        let schedule = NightSchedule(Night(start: "01:15", end: "01:45", darkAfterIdleS: 60))!
        let first = at(2026, 11, 1, 1, 30)
        #expect(schedule.contains(first, in: toronto))
        // One real hour later it is 01:30 again (standard time): still inside by the wall clock.
        #expect(schedule.contains(first.addingTimeInterval(3600), in: toronto))
        #expect(!schedule.contains(first.addingTimeInterval(3600 + 20 * 60), in: toronto))
    }
}

@MainActor
@Suite struct NightModeControllerTests {
    @Test func dayOutsideTheWindow() {
        let h = Harness(at(2026, 11, 14, 12, 0))
        #expect(h.night.phase == .day)
        #expect(h.brightness.level == 0.8)
    }

    @Test func noScheduleIsAlwaysDay() {
        let h = Harness(at(2026, 11, 14, 23, 0), schedule: nil)
        h.clock.advance(3600)
        h.night.tick()
        #expect(h.night.phase == .day)
    }

    @Test func bedtimeDimsThenGoesDarkAfterIdle() {
        let h = Harness(at(2026, 11, 14, 21, 0))
        h.clock.now = at(2026, 11, 14, 21, 30)
        h.night.tick()
        #expect(h.night.phase == .dim)
        #expect(h.brightness.level == 0.3)

        // The idle countdown starts at bedtime, not at the last daytime touch.
        h.clock.advance(299)
        h.night.tick()
        #expect(h.night.phase == .dim)
        h.clock.advance(1)
        h.night.tick()
        #expect(h.night.phase == .dark)
        #expect(h.brightness.level == 0)
    }

    @Test func dimNeverRaisesALowDaytimeBrightness() {
        let h = Harness(at(2026, 11, 14, 21, 0))
        h.brightness.level = 0.1
        h.clock.now = at(2026, 11, 14, 22, 0)
        h.night.tick()
        #expect(h.night.phase == .dim)
        #expect(h.brightness.level == 0.1)
    }

    @Test func launchingInsideTheWindowStartsDim() {
        let h = Harness(at(2026, 11, 15, 2, 0))
        #expect(h.night.phase == .dim)
    }

    @Test func activityResetsTheIdleCountdown() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.clock.advance(200)
        h.night.noteActivity()
        h.clock.advance(200)
        h.night.tick()
        #expect(h.night.phase == .dim)
        h.clock.advance(100)
        h.night.tick()
        #expect(h.night.phase == .dark)
    }

    @Test func tapAlwaysWakesAndRestoresDimBrightness() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.clock.advance(400)
        h.night.tick()
        #expect(h.night.phase == .dark)

        h.night.wake()
        #expect(h.night.phase == .dim)
        #expect(h.brightness.level == 0.3)

        // ...and goes dark again after another idle stretch.
        h.clock.advance(300)
        h.night.tick()
        #expect(h.night.phase == .dark)
    }

    @Test func zeroIdleTimeoutStillStaysAwakeBrieflyAfterATap() {
        let h = Harness(at(2026, 11, 14, 22, 0), schedule: Night(start: "21:30", end: "06:30", darkAfterIdleS: 0))
        h.night.tick()
        h.night.wake()
        #expect(h.night.phase == .dim)
        h.clock.advance(1)
        h.night.tick()
        #expect(h.night.phase == .dim)
        h.clock.advance(NightModeController.minimumAwake)
        h.night.tick()
        #expect(h.night.phase == .dark)
    }

    @Test func alarmBlocksDarkAndWakesIt() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.clock.advance(400)
        h.night.tick()
        #expect(h.night.phase == .dark)

        h.night.setHold(.alarm, true)
        #expect(h.night.phase == .dim)
        h.clock.advance(3600)
        h.night.tick()
        #expect(h.night.phase == .dim)

        // Released: a full idle stretch from the moment it stopped.
        h.night.setHold(.alarm, false)
        h.clock.advance(299)
        h.night.tick()
        #expect(h.night.phase == .dim)
        h.clock.advance(1)
        h.night.tick()
        #expect(h.night.phase == .dark)
    }

    @Test func mediaPlayingBlocksDark() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.night.setHold(.media, true)
        h.clock.advance(3600)
        h.night.tick()
        #expect(h.night.phase == .dim)
        h.night.sleepNow()
        #expect(h.night.phase == .dim)
    }

    @Test func conversationBlocksDark() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.night.setHold(.conversation, true)
        h.clock.advance(3600)
        h.night.tick()
        #expect(h.night.phase == .dim)
    }

    @Test func sleepCommandGoesDarkOnlyAtNight() {
        let day = Harness(at(2026, 11, 14, 12, 0))
        day.night.sleepNow()
        #expect(day.night.phase == .day)

        let h = Harness(at(2026, 11, 14, 22, 0))
        h.night.sleepNow()
        #expect(h.night.phase == .dark)
        #expect(h.brightness.level == 0)
        h.night.wake()
        #expect(h.night.phase == .dim)
    }

    @Test func endOfWindowRestoresDaytimeBrightness() {
        let h = Harness(at(2026, 11, 15, 5, 0))
        h.clock.advance(400)
        h.night.tick()
        #expect(h.night.phase == .dark)
        #expect(h.brightness.level == 0)

        h.clock.now = at(2026, 11, 15, 6, 30)
        h.night.tick()
        #expect(h.night.phase == .day)
        #expect(h.brightness.level == 0.8)
    }

    @Test func backgroundRestoresBrightnessAndForegroundReapplies() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.clock.advance(400)
        h.night.tick()
        #expect(h.brightness.level == 0)

        h.night.didEnterBackground()
        #expect(h.brightness.level == 0.8)
        // Nothing touches the screen while in the background.
        h.night.wake()
        #expect(h.brightness.level == 0.8)

        h.night.didBecomeActive()
        #expect(h.night.phase == .dim)
        #expect(h.brightness.level == 0.3)
    }

    @Test func scheduleChangeAppliesImmediately() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        #expect(h.night.phase == .dim)
        h.night.update(night: Night(start: "23:00", end: "06:00", darkAfterIdleS: 300))
        #expect(h.night.phase == .day)
        #expect(h.brightness.level == 0.8)
        h.night.update(night: nil)
        #expect(h.night.phase == .day)
    }

    @Test func malformedScheduleFallsBackToDay() {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.night.update(night: Night(start: "late", end: "06:30", darkAfterIdleS: 300))
        #expect(h.night.phase == .day)
        #expect(h.brightness.level == 0.8)
    }

    @Test func idleInfoSetsScheduleAndTimezone() throws {
        let h = Harness(at(2026, 11, 14, 22, 0), schedule: nil)
        let json = """
            {"server_time": "2026-11-15T03:00:00Z", "timezone": "Asia/Tokyo", "weather": null,
             "night": {"start": "21:30", "end": "06:30", "dark_after_idle_s": 300}}
            """
        let info = try CocoJSON.decoder.decode(IdleInfo.self, from: Data(json.utf8))
        h.night.apply(info)
        // 22:00 Toronto is 12:00 Tokyo: day by the Hub's clock.
        #expect(h.night.timeZone.identifier == "Asia/Tokyo")
        #expect(h.night.phase == .day)
        h.night.update(timeZoneIdentifier: "Not/AZone")
        #expect(h.night.timeZone.identifier == "Asia/Tokyo")
    }

    @Test func tickLoopEvaluatesPeriodically() async {
        let h = Harness(at(2026, 11, 14, 22, 0))
        h.night.start()
        #expect(await waitUntil { h.sleeper.pending == [.seconds(15)] })
        h.clock.advance(400)
        h.sleeper.fire()
        #expect(await waitUntil { h.night.phase == .dark })
        h.night.stop()
        #expect(await waitUntil { h.sleeper.pending.isEmpty })
    }
}

@MainActor
@Suite struct AppModelNightTests {
    private func model() -> AppModel {
        AppModel(
            config: UserDefaultsHubConfigStore(
                defaults: UserDefaults(suiteName: "coco-night-\(UUID().uuidString)")!,
                tokens: InMemoryTokenStore()),
            discovery: FakeDiscovery(results: []),
            makeClient: { _ in FakeHubAPI() },
            makeEvents: { _ in AsyncStream { _ in } },
            night: NightModeController(
                brightness: FakeScreenBrightness(level: 0.8), timeZone: toronto,
                now: { at(2026, 11, 14, 22, 0) }))
    }

    @Test func screenCommandsDriveNightMode() {
        let model = model()
        model.night.update(night: bedtime)
        #expect(model.night.phase == .dim)
        model.perform(.screenSleep)
        #expect(model.night.phase == .dark)
        model.perform(.screenWake)
        #expect(model.night.phase == .dim)
    }
}
