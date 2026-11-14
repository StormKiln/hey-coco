import Foundation
import Synchronization
import Testing
@testable import CocoKit

private func action(view: ViewSpec?, say: String? = nil) -> Action {
    Action(say: say, view: view, commands: [], intent: "test", matchedBy: .rules)
}

/// A `HubAPI` whose `idle()` result is fixed; everything else is unused here.
final class FakeHubAPI: HubAPI {
    let idleResult: Result<IdleInfo, HubError>
    let utteranceResult: Result<Action, HubError>
    let utterances = Mutex<[UtteranceRequest]>([])

    init(
        idle: Result<IdleInfo, HubError> = .failure(.unreachable("down")),
        utterance: Result<Action, HubError> = .failure(.unreachable("down"))
    ) {
        idleResult = idle
        utteranceResult = utterance
    }

    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        utterances.withLock { $0.append(request) }
        return try utteranceResult.get()
    }
    func idle() async throws(HubError) -> IdleInfo { try idleResult.get() }
    func timers() async throws(HubError) -> TimersList { throw .unreachable("unused") }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo {
        throw .unreachable("unused")
    }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
}

@MainActor
private func makeModel(
    settings: HubSettings = HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test),
    discovery: any HubDiscovery = FakeDiscovery(results: []),
    api: FakeHubAPI = FakeHubAPI(),
    updates: [EventStreamUpdate] = []
) -> AppModel {
    let store = UserDefaultsHubConfigStore(
        defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
        tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
    try! store.save(settings)
    return AppModel(
        config: store, discovery: discovery, probe: FakeHubProbe(),
        makeClient: { _ in api },
        makeEvents: { _ in
            AsyncStream { continuation in
                for update in updates { continuation.yield(update) }
                // Left open, like a live connection.
            }
        })
}

@MainActor
@Suite struct AppModelRouterTests {
    @Test func startsOnIdle() {
        #expect(makeModel().currentView == .idle)
    }

    @Test func actionWithViewReplacesCurrentView() {
        let model = makeModel()
        model.apply(action(view: .answer(text: "42")))
        #expect(model.currentView == .answer(text: "42"))
        model.apply(action(view: .timers))
        #expect(model.currentView == .timers)
    }

    @Test func actionWithoutViewKeepsCurrentView() {
        let model = makeModel()
        model.apply(action(view: .timers))
        model.apply(action(view: nil, say: "ok"))
        #expect(model.currentView == .timers)
        #expect(model.lastSay == "ok")
    }

    @Test func unknownViewIsIgnored() {
        let model = makeModel()
        model.apply(action(view: .nowPlaying))
        model.apply(action(view: .unknown("hologram")))
        #expect(model.currentView == .nowPlaying)
    }
}

@MainActor
@Suite struct AppModelConnectionTests {
    /// Polls (yielding the main actor) until `condition` holds or ~2s pass.
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    @Test func connectsUsingManualHostAndMapsStreamUpdates() async throws {
        let idle = try Fixtures.decode(IdleInfo.self, "idle_info")
        let hello = try Fixtures.decode(Event.self, "event_hello")
        let model = makeModel(
            api: FakeHubAPI(idle: .success(idle)),
            updates: [.connecting(attempt: 1), .connected, .event(hello)])
        #expect(model.connection == .searching)
        model.start()
        #expect(await eventually { model.connection == .connected && model.idleInfo == idle })
        #expect(model.endpoint == HubEndpoint(host: "10.0.0.5", port: 8765, token: "t"))
        model.stop()
    }

    @Test func waitingMapsToUnreachable() async throws {
        let model = makeModel(updates: [
            .connecting(attempt: 1), .waiting(retryIn: .seconds(1), reason: "Could not connect to Coco Hub."),
        ])
        model.start()
        #expect(await eventually { model.connection == .unreachable(reason: "Could not connect to Coco Hub.") })
        model.stop()
    }

    @Test func unauthorizedStreamMapsToUnauthorized() async throws {
        let model = makeModel(updates: [.connecting(attempt: 1), .unauthorized])
        model.start()
        #expect(await eventually { model.connection == .unauthorized })
        model.stop()
    }

    @Test func missingTokenIsUnauthorizedWithoutConnecting() async throws {
        let model = makeModel(
            settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: nil),
            updates: [.connecting(attempt: 1), .connected])
        model.start()
        #expect(await eventually { model.connection == .unauthorized })
        try await Task.sleep(for: .milliseconds(50))
        #expect(model.connection == .unauthorized)
        model.stop()
    }

    @Test func discoveredHubIsUsedWithoutManualHost() async throws {
        let model = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: "t", pin: .test),
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            updates: [.connecting(attempt: 1), .connected])
        model.start()
        #expect(await eventually { model.connection == .connected })
        #expect(model.endpoint?.host == "mac.local")
        model.stop()
    }

    @Test func submitAppliesTheReturnedAction() async throws {
        let api = FakeHubAPI(utterance: .success(action(view: .answer(text: "Paris"), say: "Paris")))
        let model = makeModel(api: api, updates: [.connecting(attempt: 1), .connected])
        model.start()
        #expect(await eventually { model.connection == .connected })
        await model.submit(text: "capital of france", source: .voice)
        #expect(model.currentView == .answer(text: "Paris"))
        let sent = api.utterances.withLock { $0 }
        #expect(sent.first?.context.view == .idle)
        model.stop()
    }

    @Test func savingSettingsReconnects() async throws {
        let model = makeModel(
            settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: nil),
            updates: [.connecting(attempt: 1), .connected])
        model.start()
        #expect(await eventually { model.connection == .unauthorized })
        try model.saveSettings(HubSettings(manualHost: "10.0.0.6", port: 8765, token: "new", pin: .test))
        #expect(await eventually { model.connection == .connected })
        #expect(model.endpoint == HubEndpoint(host: "10.0.0.6", port: 8765, token: "new"))
        model.stop()
    }
}
