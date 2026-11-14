import Foundation
import Testing
@testable import CocoKit

@MainActor
@Suite struct AppModelTimerTests {
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    private func makeModel(
        hub: any HubAPI, updates: [EventStreamUpdate], store: TimerStore
    ) -> AppModel {
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore())
        try! config.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t"))
        return AppModel(
            config: config, discovery: FakeDiscovery(results: []),
            makeClient: { _ in hub },
            makeEvents: { _ in
                AsyncStream { continuation in
                    for update in updates { continuation.yield(update) }
                }
            },
            timerStore: store)
    }

    @Test func helloFromTheStreamReachesTheTimerStore() async throws {
        let hello = try Fixtures.decode(Event.self, "event_hello")
        let clock = TestClock(hello.serverTime)
        let store = TimerStore(now: clock.function)
        let model = makeModel(
            hub: FakeTimerHub(serverNow: clock.function),
            updates: [.connecting(attempt: 1), .connected, .event(hello)], store: store)
        model.start()
        #expect(await eventually { model.timerStore.timers.map(\.id) == ["t_7f3a"] })
        model.stop()
    }

    @Test func offlineTouchTimerSyncsWhenTheStreamConnects() async throws {
        let clock = TestClock()
        let store = TimerStore(now: clock.function)
        await store.create(durationS: 300)
        let hub = FakeTimerHub(serverNow: clock.function)
        let hello = Event(serverTime: clock.now, kind: .hello(timers: [], nowPlaying: nil))
        let model = makeModel(
            hub: hub, updates: [.connecting(attempt: 1), .connected, .event(hello)], store: store)
        model.start()
        #expect(await eventually { hub.created.count == 1 && !store.hasPendingSync })
        model.stop()
    }

    @Test func timersScreenReturnsToIdleWhenTheLastTimerEnds() {
        let clock = TestClock()
        let store = TimerStore(now: clock.function)
        let model = makeModel(hub: FakeTimerHub(serverNow: clock.function), updates: [], store: store)
        let timer = TimerInfo.make(id: "a", endsAt: clock.now + 60)
        store.apply(Event(serverTime: clock.now, kind: .timerCreated(timer)))
        model.show(.timers)
        var cancelled = timer
        cancelled.state = .cancelled
        store.apply(Event(serverTime: clock.now, kind: .timerCancelled(cancelled)))
        #expect(model.currentView == .idle)
    }

    @Test func otherScreensAreLeftAloneWhenTimersEnd() {
        let clock = TestClock()
        let store = TimerStore(now: clock.function)
        let model = makeModel(hub: FakeTimerHub(serverNow: clock.function), updates: [], store: store)
        model.show(.answer(text: "hi"))
        store.apply(Event(serverTime: clock.now, kind: .hello(timers: [], nowPlaying: nil)))
        #expect(model.currentView == .answer(text: "hi"))
    }

    @Test func addTimeToALocalTimerWorksWithoutTheHub() async {
        let clock = TestClock()
        let store = TimerStore(now: clock.function)
        let model = makeModel(hub: FakeTimerHub(serverNow: clock.function), updates: [], store: store)
        await store.create(durationS: 60)
        await model.addTime(to: store.timers[0], seconds: 60)
        #expect(store.remaining(of: store.timers[0]) == 120)
    }

    @Test func addTimePhrase() {
        let end = Date()
        let named = DisplayTimer(id: "a", name: "pasta", durationS: 60, endsAt: end, state: .running, isLocalOnly: false)
        let unnamed = DisplayTimer(id: "b", name: nil, durationS: 60, endsAt: end, state: .running, isLocalOnly: false)
        #expect(AppModel.addTimePhrase(for: named, seconds: 60) == "add 1 minute to the pasta timer")
        #expect(AppModel.addTimePhrase(for: unnamed, seconds: 300) == "add 5 minutes to the timer")
    }
}
