import Foundation
import Synchronization
import Testing
@testable import CocoKit

@MainActor
private final class FakeOneShotTimers: OneShotTimers {
    private(set) var active: [String: (date: Date, fire: @MainActor () -> Void)] = [:]
    private(set) var cancels: [String] = []

    func schedule(id: String, at date: Date, fire: @escaping @MainActor () -> Void) {
        active[id] = (date, fire)
    }
    func cancel(id: String) {
        cancels.append(id)
        active[id] = nil
    }
    func fire(_ id: String) { active.removeValue(forKey: id)?.fire() }
}

private final class FakeNotifications: AlarmNotifications {
    struct Log: Equatable {
        var authorizations = 0
        var added: [String] = []
        var removed: [String] = []
    }
    let log = Mutex(Log())
    var snapshot: Log { log.withLock { $0 } }

    func requestAuthorization() async { log.withLock { $0.authorizations += 1 } }
    func add(_ alarm: AlarmRequest) async { log.withLock { $0.added.append(alarm.id) } }
    func remove(ids: [String]) async { log.withLock { $0.removed += ids } }
}

@MainActor
@Suite struct LocalAlarmSchedulerTests {
    let base = Date(timeIntervalSince1970: 1_800_000_000)

    @Test func schedulesBothPathsAndAsksPermissionOnce() async {
        let timers = FakeOneShotTimers()
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: timers, notifications: notifications)
        scheduler.schedule([AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60)])
        scheduler.schedule([
            AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60),
            AlarmRequest(id: "b", title: "Eggs", fireDate: base + 30),
        ])
        await scheduler.flush()
        #expect(Set(timers.active.keys) == ["a", "b"])
        #expect(notifications.snapshot.added == ["a", "b"])
        #expect(notifications.snapshot.authorizations == 1)
    }

    @Test func noPermissionPromptUntilTheFirstTimer() async {
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: FakeOneShotTimers(), notifications: notifications)
        scheduler.schedule([])
        await scheduler.flush()
        #expect(notifications.snapshot.authorizations == 0)
    }

    @Test func cancelsRemovedAlarms() async {
        let timers = FakeOneShotTimers()
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: timers, notifications: notifications)
        scheduler.schedule([AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60)])
        scheduler.schedule([])
        await scheduler.flush()
        #expect(timers.active.isEmpty)
        #expect(timers.cancels == ["a"])
        #expect(notifications.snapshot.removed == ["a"])
    }

    @Test func reschedulesAMovedAlarm() async {
        let timers = FakeOneShotTimers()
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: timers, notifications: notifications)
        scheduler.schedule([AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60)])
        scheduler.schedule([AlarmRequest(id: "a", title: "Pasta", fireDate: base + 120)])
        await scheduler.flush()
        #expect(timers.active["a"]?.date == base + 120)
        #expect(notifications.snapshot.added == ["a", "a"])
        #expect(notifications.snapshot.removed == ["a"])
    }

    @Test func unchangedSetDoesNothing() async {
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: FakeOneShotTimers(), notifications: notifications)
        let alarms = [AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60)]
        scheduler.schedule(alarms)
        scheduler.schedule(alarms)
        await scheduler.flush()
        #expect(notifications.snapshot.added == ["a"])
    }

    @Test func inProcessFireCallsOnFire() {
        let timers = FakeOneShotTimers()
        let scheduler = LocalAlarmScheduler(timers: timers, notifications: FakeNotifications())
        var fired: [String] = []
        scheduler.onFire = { fired.append($0) }
        scheduler.schedule([AlarmRequest(id: "a", title: "Pasta", fireDate: base + 60)])
        timers.fire("a")
        #expect(fired == ["a"])
    }

    @Test func endToEndWithTimerStore() async {
        let clock = TestClock()
        let timers = FakeOneShotTimers()
        let notifications = FakeNotifications()
        let scheduler = LocalAlarmScheduler(timers: timers, notifications: notifications)
        let player = FakeAlarmPlayer()
        let store = TimerStore(now: clock.function, scheduler: scheduler, player: player)
        store.apply(Event(serverTime: clock.now, kind: .timerCreated(.make(id: "a", endsAt: clock.now + 60))))
        clock.advance(by: 60)
        timers.fire("a")
        #expect(player.isRinging)
        await scheduler.flush()
        // Ringing in-app: the backup notification is withdrawn.
        #expect(notifications.snapshot.removed == ["a"])
    }
}

@MainActor
@Suite struct TaskOneShotTimersTests {
    @Test func firesAfterTheDate() async throws {
        let timers = TaskOneShotTimers()
        var fired = false
        timers.schedule(id: "a", at: Date().addingTimeInterval(0.05)) { fired = true }
        #expect(await waitUntil { fired })
    }

    @Test func cancelledNeverFires() async throws {
        let timers = TaskOneShotTimers()
        var fired = false
        timers.schedule(id: "a", at: Date().addingTimeInterval(0.05)) { fired = true }
        timers.cancel(id: "a")
        try await Task.sleep(for: .milliseconds(200))
        #expect(!fired)
    }

    @Test func pastDateFiresImmediately() async throws {
        let timers = TaskOneShotTimers()
        var fired = false
        timers.schedule(id: "a", at: Date().addingTimeInterval(-10)) { fired = true }
        #expect(await waitUntil { fired })
    }
}
