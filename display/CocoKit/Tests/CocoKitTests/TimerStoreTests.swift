import Foundation
import Testing
@testable import CocoKit

@MainActor
private struct Harness {
    let clock = TestClock()
    let scheduler = FakeAlarmScheduler()
    let player = FakeAlarmPlayer()
    let persistence: InMemoryTimerPersistence
    let store: TimerStore

    init(persistence: InMemoryTimerPersistence = InMemoryTimerPersistence()) {
        self.persistence = persistence
        store = TimerStore(
            now: clock.function, persistence: persistence, scheduler: scheduler, player: player)
    }

    var now: Date { clock.now }

    func event(_ kind: Event.Kind, serverTime: Date? = nil) -> Event {
        Event(serverTime: serverTime ?? clock.now, kind: kind)
    }
}

@MainActor
@Suite struct TimerStoreEventTests {
    @Test func helloReplacesAllHubTimers() {
        let h = Harness()
        let a = TimerInfo.make(id: "a", endsAt: h.now + 60)
        let b = TimerInfo.make(id: "b", name: "eggs", endsAt: h.now + 30)
        h.store.apply(h.event(.timerCreated(a)))
        h.store.apply(h.event(.hello(timers: [b], nowPlaying: nil)))
        #expect(h.store.timers.map(\.id) == ["b"])
    }

    @Test func helloDropsFinishedTimers() {
        let h = Harness()
        let a = TimerInfo.make(id: "a", endsAt: h.now + 60, state: .dismissed)
        let b = TimerInfo.make(id: "b", endsAt: h.now + 60, state: .cancelled)
        h.store.apply(h.event(.hello(timers: [a, b], nowPlaying: nil)))
        #expect(h.store.timers.isEmpty)
    }

    @Test func timersAreSortedBySoonest() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "late", endsAt: h.now + 600))))
        h.store.apply(h.event(.timerCreated(.make(id: "soon", endsAt: h.now + 60))))
        h.store.apply(h.event(.timerCreated(.make(id: "mid", endsAt: h.now + 300))))
        #expect(h.store.timers.map(\.id) == ["soon", "mid", "late"])
    }

    @Test func updatedReplacesTheTimer() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.store.apply(h.event(.timerUpdated(.make(id: "a", duration: 660, endsAt: h.now + 120))))
        #expect(h.store.timers.count == 1)
        #expect(h.store.remaining(of: h.store.timers[0]) == 120)
    }

    @Test func firedRingsAndDismissedStops() {
        let h = Harness()
        let timer = TimerInfo.make(id: "a", endsAt: h.now + 60)
        h.store.apply(h.event(.timerCreated(timer)))
        #expect(!h.player.isRinging)
        var fired = timer
        fired.state = .ringing
        h.store.apply(h.event(.timerFired(fired)))
        #expect(h.store.isAlarmRinging)
        #expect(h.player.isRinging)
        #expect(h.store.ringingTimers.map(\.id) == ["a"])
        var dismissed = timer
        dismissed.state = .dismissed
        h.store.apply(h.event(.timerDismissed(dismissed)))
        #expect(h.store.timers.isEmpty)
        #expect(!h.store.isAlarmRinging)
        #expect(!h.player.isRinging)
    }

    @Test func cancelledRemovesTheTimer() {
        let h = Harness()
        let timer = TimerInfo.make(id: "a", endsAt: h.now + 60)
        h.store.apply(h.event(.timerCreated(timer)))
        var cancelled = timer
        cancelled.state = .cancelled
        h.store.apply(h.event(.timerCancelled(cancelled)))
        #expect(h.store.timers.isEmpty)
        #expect(h.scheduler.scheduled.isEmpty)
    }

    @Test func staleRunningUpdateDoesNotSilenceALocallyRingingTimer() {
        let h = Harness()
        let timer = TimerInfo.make(id: "a", endsAt: h.now + 60)
        h.store.apply(h.event(.timerCreated(timer)))
        h.clock.advance(by: 60)
        h.scheduler.fireDue(at: h.now)
        #expect(h.player.isRinging)
        h.store.apply(h.event(.timerUpdated(timer)))
        #expect(h.store.isAlarmRinging)
        #expect(h.player.isRinging)
    }

    @Test func timersListReplacesLikeHello() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "old", endsAt: h.now + 60))))
        h.store.apply(TimersList(timers: [.make(id: "new", endsAt: h.now + 90)], serverTime: h.now))
        #expect(h.store.timers.map(\.id) == ["new"])
    }

    @Test func otherEventsAreIgnored() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.store.apply(h.event(.nowPlaying(nil)))
        h.store.apply(h.event(.unknown("future.thing")))
        #expect(h.store.timers.map(\.id) == ["a"])
    }

    @Test func changeCallbackRunsOnEveryChange() {
        let h = Harness()
        var calls = 0
        h.store.onTimersChanged = { calls += 1 }
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.store.apply(h.event(.hello(timers: [], nowPlaying: nil)))
        #expect(calls == 2)
    }
}

@MainActor
@Suite struct TimerStoreSkewTests {
    @Test func remainingCorrectsForHubClockAhead() {
        let h = Harness()
        // The Hub's clock is 90 s ahead of the iPad's.
        let serverNow = h.now + 90
        let timer = TimerInfo.make(id: "a", endsAt: serverNow + 600)
        h.store.apply(h.event(.timerCreated(timer), serverTime: serverNow))
        #expect(h.store.clockOffset == 90)
        #expect(h.store.remaining(of: h.store.timers[0]) == 600)
        #expect(h.scheduler.scheduled.first?.fireDate == h.now + 600)
    }

    @Test func remainingCorrectsForHubClockBehind() {
        let h = Harness()
        let serverNow = h.now - 45
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: serverNow + 300)), serverTime: serverNow))
        #expect(h.store.remaining(of: h.store.timers[0]) == 300)
        h.clock.advance(by: 100)
        #expect(h.store.remaining(of: h.store.timers[0]) == 200)
    }

    @Test func remainingNeverGoesNegative() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 5))))
        h.clock.advance(by: 30)
        #expect(h.store.remaining(of: h.store.timers[0]) == 0)
    }
}

@MainActor
@Suite struct TimerStoreSchedulingTests {
    @Test func schedulesEveryRunningTimer() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.store.apply(h.event(.timerCreated(.make(id: "b", name: nil, endsAt: h.now + 30))))
        #expect(h.scheduler.scheduled.map(\.id) == ["b", "a"])
        #expect(h.scheduler.scheduled.map(\.fireDate) == [h.now + 30, h.now + 60])
        #expect(h.scheduler.scheduled[1].title == "Pasta")
    }

    @Test func ringingAndRemovedTimersAreUnscheduled() {
        let h = Harness()
        let a = TimerInfo.make(id: "a", endsAt: h.now + 60)
        let b = TimerInfo.make(id: "b", endsAt: h.now + 90)
        h.store.apply(h.event(.hello(timers: [a, b], nowPlaying: nil)))
        var ringing = a
        ringing.state = .ringing
        h.store.apply(h.event(.timerFired(ringing)))
        var cancelled = b
        cancelled.state = .cancelled
        h.store.apply(h.event(.timerCancelled(cancelled)))
        #expect(h.scheduler.scheduled.isEmpty)
    }

    @Test func localFireRingsWithoutTheHub() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.clock.advance(by: 60)
        h.scheduler.fireDue(at: h.now)
        #expect(h.store.timers.first?.state == .ringing)
        #expect(h.player.isRinging)
    }

    @Test func fireDueAlarmsCatchesUpMissedFires() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.store.apply(h.event(.timerCreated(.make(id: "b", endsAt: h.now + 600))))
        h.clock.advance(by: 120)
        h.store.fireDueAlarms()
        #expect(h.store.ringingTimers.map(\.id) == ["a"])
        #expect(h.player.isRinging)
    }

    @Test func earlyFireCallbackIsIgnored() {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 60))))
        h.scheduler.onFire?("a")
        #expect(!h.store.isAlarmRinging)
    }
}

@MainActor
@Suite struct TimerStoreHubDownTests {
    /// Anti-goal: a timer that fails silently. The Hub goes away mid-timer; the iPad still rings.
    @Test func alarmStillRingsWithHubStoppedMidTimer() async {
        let h = Harness()
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        await h.store.create(durationS: 300)
        #expect(hub.created.count == 1)
        #expect(h.store.timers.first?.isLocalOnly == false)

        // Hub stops two minutes in: connection drops, every call fails.
        h.clock.advance(by: 120)
        hub.reachable = false
        h.store.hubDisconnected()

        h.clock.advance(by: 180)
        h.scheduler.fireDue(at: h.now)
        #expect(h.store.isAlarmRinging)
        #expect(h.player.isRinging)

        // Stop works with the Hub still down, and the dismissal is queued for later.
        await h.store.stopAlarms()
        #expect(!h.player.isRinging)
        #expect(h.store.timers.isEmpty)
        #expect(h.store.hasPendingSync)

        // Hub comes back: the dismissal is delivered.
        hub.reachable = true
        h.store.hubConnected(hub)
        h.store.apply(h.event(.hello(timers: [], nowPlaying: nil)))
        await h.store.syncPending()
        #expect(hub.dismissed.count == 1)
        #expect(!h.store.hasPendingSync)
    }

    @Test func touchTimerCreatedOfflineRingsLocallyAndSyncsOnReconnect() async {
        let h = Harness()
        await h.store.create(durationS: 600)  // no Hub at all
        #expect(h.store.timers.count == 1)
        let local = h.store.timers[0]
        #expect(local.isLocalOnly)
        #expect(h.scheduler.scheduled.first?.fireDate == h.now + 600)

        h.clock.advance(by: 100)
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        h.store.apply(h.event(.hello(timers: [], nowPlaying: nil)))
        await h.store.syncPending()

        #expect(hub.created == [TimerCreateRequest(name: nil, durationS: 500)])
        #expect(h.store.timers.count == 1)
        #expect(h.store.timers[0].isLocalOnly == false)
        #expect(h.store.timers[0].id == "t_1")
        #expect(h.store.remaining(of: h.store.timers[0]) == 500)
        #expect(!h.store.hasPendingSync)
    }

    @Test func createWithHubUnreachableFallsBackToLocal() async {
        let h = Harness()
        let hub = FakeTimerHub(serverNow: h.clock.function)
        hub.reachable = false
        h.store.hubConnected(hub)
        await h.store.create(durationS: 60)
        #expect(h.store.timers.first?.isLocalOnly == true)
        #expect(h.store.hasPendingSync)
        #expect(h.scheduler.scheduled.count == 1)
    }

    @Test func syncDoesNotDuplicateATimerTheHubAlreadyHas() async {
        // The create reached the Hub but its reply was lost; hello shows the Hub's copy.
        let h = Harness()
        await h.store.create(durationS: 600)
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        let hubCopy = TimerInfo.make(id: "t_9", name: "timer", endsAt: h.now + 601)
        h.store.apply(h.event(.hello(timers: [hubCopy], nowPlaying: nil)))
        await h.store.syncPending()
        #expect(hub.created.isEmpty)
        #expect(h.store.timers.map(\.id) == ["t_9"])
        #expect(!h.store.hasPendingSync)
    }

    @Test func createdEventBeforeRestReplyDoesNotDuplicate() async {
        let h = Harness()
        await h.store.create(durationS: 600)
        let echo = TimerInfo.make(id: "t_1", name: "timer", endsAt: h.now + 600)
        h.store.apply(h.event(.timerCreated(echo)))
        #expect(h.store.timers.map(\.id) == ["t_1"])
    }

    @Test func helloKeepsLocalOnlyTimers() async {
        let h = Harness()
        await h.store.create(durationS: 600)
        h.store.apply(h.event(.hello(timers: [.make(id: "x", name: "eggs", endsAt: h.now + 30)], nowPlaying: nil)))
        #expect(h.store.timers.count == 2)
        #expect(h.store.timers.contains { $0.isLocalOnly })
    }

    @Test func dismissedOfflineIsNotResurrectedByHello() async {
        let h = Harness()
        let timer = TimerInfo.make(id: "a", endsAt: h.now + 10, state: .ringing)
        h.store.apply(h.event(.timerFired(timer)))
        await h.store.dismiss(id: "a")
        h.store.apply(h.event(.hello(timers: [timer], nowPlaying: nil)))
        #expect(h.store.timers.isEmpty)
        #expect(!h.player.isRinging)
    }

    @Test func cancelOfflineQueuesAndSyncs() async {
        let h = Harness()
        h.store.apply(h.event(.timerCreated(.make(id: "a", endsAt: h.now + 600))))
        await h.store.cancel(id: "a")
        #expect(h.store.timers.isEmpty)
        #expect(h.scheduler.scheduled.isEmpty)
        let hub = FakeTimerHub(serverNow: h.clock.function)
        hub.state.withLock { $0.timers["a"] = .make(id: "a", endsAt: h.now + 600) }
        h.store.hubConnected(hub)
        await h.store.syncPending()
        #expect(hub.cancelled == ["a"])
        #expect(!h.store.hasPendingSync)
    }

    @Test func cancellingALocalOnlyTimerNeverTouchesTheHub() async {
        let h = Harness()
        await h.store.create(durationS: 600)
        await h.store.cancel(id: h.store.timers[0].id)
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        await h.store.syncPending()
        #expect(hub.created.isEmpty)
        #expect(hub.cancelled.isEmpty)
        #expect(!h.store.hasPendingSync)
    }

    @Test func localTimerThatRangOfflineIsNotSentToTheHub() async {
        let h = Harness()
        await h.store.create(durationS: 60)
        h.clock.advance(by: 61)
        h.scheduler.fireDue(at: h.now)
        await h.store.stopAlarms()
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        await h.store.syncPending()
        #expect(hub.created.isEmpty)
        #expect(hub.dismissed.isEmpty)
    }

    @Test func dismissOnlineCallsTheHub() async {
        let h = Harness()
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        await h.store.create(durationS: 60)
        h.clock.advance(by: 60)
        h.store.fireDueAlarms()
        await h.store.stopAlarms()
        #expect(hub.dismissed == ["t_1"])
        #expect(!h.store.hasPendingSync)
    }

    @Test func hubRejectingADismissIsNotRetriedForever() async {
        let h = Harness()
        let hub = FakeTimerHub(serverNow: h.clock.function)
        h.store.hubConnected(hub)
        h.store.apply(h.event(.timerFired(.make(id: "gone", endsAt: h.now, state: .ringing))))
        await h.store.dismiss(id: "gone")  // the fake Hub answers 404
        #expect(!h.store.hasPendingSync)
    }

    @Test func addTimeToLocalTimerExtendsIt() async {
        let h = Harness()
        await h.store.create(durationS: 60)
        let id = h.store.timers[0].id
        h.store.addTimeLocally(id: id, seconds: 60)
        #expect(h.store.remaining(of: h.store.timers[0]) == 120)
        #expect(h.scheduler.scheduled.first?.fireDate == h.now + 120)
    }
}

@MainActor
@Suite struct TimerStorePersistenceTests {
    @Test func relaunchRestoresTimersAndSchedulesThem() async {
        let persistence = InMemoryTimerPersistence()
        let first = Harness(persistence: persistence)
        first.store.apply(first.event(.timerCreated(.make(id: "a", endsAt: first.now + 600))))
        await first.store.create(durationS: 120)

        let second = Harness(persistence: persistence)
        #expect(second.store.timers.map(\.id) == first.store.timers.map(\.id))
        #expect(second.store.hasPendingSync)
        #expect(second.scheduler.scheduled.count == 2)
    }

    @Test func relaunchAfterEndTimeRingsImmediately() {
        let persistence = InMemoryTimerPersistence()
        let first = Harness(persistence: persistence)
        first.store.apply(first.event(.timerCreated(.make(id: "a", endsAt: first.now - 1))))
        let second = Harness(persistence: persistence)
        #expect(second.store.isAlarmRinging)
        #expect(second.player.isRinging)
    }

    @Test func fileRoundTrip() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("coco-tests-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = FileTimerPersistence(url: dir.appendingPathComponent("timers.json"))
        #expect(file.load() == nil)
        let snapshot = TimerSnapshot(
            timers: [
                DisplayTimer(
                    id: "a", name: "pasta", durationS: 600,
                    endsAt: Date(timeIntervalSince1970: 1_800_000_600), state: .running,
                    isLocalOnly: false),
                DisplayTimer(
                    id: "local-1", name: nil, durationS: 60,
                    endsAt: Date(timeIntervalSince1970: 1_800_000_060), state: .ringing,
                    isLocalOnly: true),
            ],
            pending: [.create(localID: "local-1"), .dismiss(id: "b"), .cancel(id: "c")],
            clockOffset: 12.5)
        try file.save(snapshot)
        #expect(file.load() == snapshot)
    }

    @Test func corruptFileLoadsAsEmpty() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("coco-tests-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("timers.json")
        try Data("not json".utf8).write(to: url)
        #expect(FileTimerPersistence(url: url).load() == nil)
    }
}

@Suite struct TimerFormatTests {
    @Test func countdown() {
        #expect(TimerFormat.countdown(0) == "0:00")
        #expect(TimerFormat.countdown(0.2) == "0:01")
        #expect(TimerFormat.countdown(59) == "0:59")
        #expect(TimerFormat.countdown(600) == "10:00")
        #expect(TimerFormat.countdown(3723) == "1:02:03")
        #expect(TimerFormat.countdown(-5) == "0:00")
    }

    @Test func displayName() {
        let end = Date(timeIntervalSince1970: 0)
        #expect(DisplayTimer(id: "a", name: "pasta", durationS: 600, endsAt: end, state: .running, isLocalOnly: false).displayName == "Pasta")
        #expect(DisplayTimer(id: "a", name: nil, durationS: 300, endsAt: end, state: .running, isLocalOnly: false).displayName == "5 min timer")
        #expect(DisplayTimer(id: "a", name: nil, durationS: 90, endsAt: end, state: .running, isLocalOnly: false).displayName == "1 min 30 s timer")
        #expect(DisplayTimer(id: "a", name: nil, durationS: 3600, endsAt: end, state: .running, isLocalOnly: false).displayName == "1 h timer")
    }
}
