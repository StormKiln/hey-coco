import Foundation
import Network
import Synchronization
import Testing
@testable import CocoKit

/// Each `browse()` yields the next batch of results (the last batch repeats) and then finishes.
final class ScriptedDiscovery: HubDiscovery {
    private let batches: [[HubAddress]]
    private let count = Mutex(0)

    init(_ batches: [[HubAddress]]) { self.batches = batches }

    var browseCount: Int { count.withLock { $0 } }

    func browse() -> AsyncStream<HubAddress> {
        let index = count.withLock { n in
            defer { n += 1 }
            return n
        }
        let batch = batches.isEmpty ? [] : batches[min(index, batches.count - 1)]
        return AsyncStream { continuation in
            for address in batch { continuation.yield(address) }
            continuation.finish()
        }
    }
}

/// A wall clock that moves `step` seconds forward each time it is read.
final class SteppingClock: Sendable {
    private let current = Mutex(Date(timeIntervalSince1970: 1_000_000))
    private let step: TimeInterval

    init(step: TimeInterval = 0) { self.step = step }

    var now: Date {
        current.withLock { date in
            defer { date += step }
            return date
        }
    }
}

/// Probe answers per host; a host listed in `down` fails until `bringUp` is called.
final class SwitchableProbe: HubProbe {
    private let answers: [String: HubPin]
    private let downHosts: Mutex<Set<String>>
    let probed = Mutex<[String]>([])

    init(_ answers: [String: HubPin], down: Set<String> = []) {
        self.answers = answers
        self.downHosts = Mutex(down)
    }

    var hosts: [String] { probed.withLock { $0 } }

    func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError> {
        probed.withLock { $0.append(address.host) }
        if downHosts.withLock({ $0.contains(address.host) }) {
            return .failure(HubProbeError("Could not connect to Coco Hub."))
        }
        guard let pin = answers[address.host] else {
            return .failure(HubProbeError("Could not connect to Coco Hub."))
        }
        return .success(pin)
    }
}

/// The event stream per host: a scripted sequence, left open afterwards (like a live socket).
final class ScriptedEvents: Sendable {
    let scripts: [String: [EventStreamUpdate]]
    let opened = Mutex<[String]>([])

    init(_ scripts: [String: [EventStreamUpdate]]) { self.scripts = scripts }

    var hosts: [String] { opened.withLock { $0 } }

    func stream(for endpoint: HubEndpoint) -> AsyncStream<EventStreamUpdate> {
        opened.withLock { $0.append(endpoint.host) }
        let updates = scripts[endpoint.host] ?? [.connected]
        return AsyncStream { continuation in
            for update in updates { continuation.yield(update) }
        }
    }
}

private let waiting = EventStreamUpdate.waiting(retryIn: .seconds(1), reason: "Lost connection to Coco Hub.")

@MainActor
private func makeModel(
    settings: HubSettings = HubSettings(manualHost: nil, port: 8765, token: "t", pin: .test),
    discovery: any HubDiscovery,
    probe: any HubProbe,
    events: ScriptedEvents,
    clock: SteppingClock = SteppingClock(),
    policy: RediscoveryPolicy = RediscoveryPolicy(maxFailures: 3, maxOutage: 30),
    recorder: EndpointRecorder = EndpointRecorder()
) -> AppModel {
    let store = UserDefaultsHubConfigStore(
        defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
        tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
    try! store.save(settings)
    return AppModel(
        config: store, discovery: discovery, probe: probe, sleeper: InstantSleeper(),
        makeClient: { endpoint in
            recorder.endpoints.withLock { $0.append(endpoint) }
            return FakeHubAPI()
        },
        makeEvents: { events.stream(for: $0) },
        rediscovery: policy,
        now: { clock.now })
}

private func address(_ host: String, id: String? = HubPin.test.hubID) -> HubAddress {
    HubAddress(host: host, port: 8765, advertisedHubID: id)
}

/// Finding the Hub again when its IP address changes (DHCP).
@MainActor
@Suite struct AppModelRediscoveryTests {
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    @Test func lostConnectionRebrowsesAndReconnectsToTheNewAddress() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")], [address("10.0.0.9")]])
        let events = ScriptedEvents([
            "10.0.0.5": [.connecting(attempt: 1), .connected, waiting, waiting, waiting],
            "10.0.0.9": [.connecting(attempt: 1), .connected],
        ])
        let recorder = EndpointRecorder()
        let model = makeModel(
            discovery: discovery,
            probe: SwitchableProbe(["10.0.0.5": .test, "10.0.0.9": .test]),
            events: events, recorder: recorder)
        model.start()
        #expect(await eventually { model.endpoint?.host == "10.0.0.9" && model.connection == .connected })
        #expect(recorder.all.map(\.host) == ["10.0.0.5", "10.0.0.9"])
        #expect(discovery.browseCount == 2)
        model.stop()
    }

    @Test func aFewDroppedConnectionsDoNotRebrowse() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")]])
        let events = ScriptedEvents([
            "10.0.0.5": [.connected, waiting, waiting, .connected, waiting, waiting, .connected]
        ])
        let model = makeModel(
            discovery: discovery, probe: SwitchableProbe(["10.0.0.5": .test]), events: events)
        model.start()
        #expect(await eventually { model.connection == .connected && events.hosts.count == 1 })
        try await Task.sleep(for: .milliseconds(50))
        #expect(discovery.browseCount == 1)
        #expect(model.endpoint?.host == "10.0.0.5")
        model.stop()
    }

    @Test func outageLongerThanTheLimitRebrowsesEvenWithFewFailures() async throws {
        // Every reading of the clock is 20 s after the last: the third failure is 40 s into the
        // outage, past the 30 s limit, long before 10 failures.
        let clock = SteppingClock(step: 20)
        let discovery = ScriptedDiscovery([[address("10.0.0.5")], [address("10.0.0.9")]])
        let events = ScriptedEvents([
            "10.0.0.5": [.connected, waiting, waiting, waiting],
            "10.0.0.9": [.connected],
        ])
        let model = makeModel(
            discovery: discovery,
            probe: SwitchableProbe(["10.0.0.5": .test, "10.0.0.9": .test]),
            events: events, clock: clock, policy: RediscoveryPolicy(maxFailures: 10, maxOutage: 30))
        model.start()
        #expect(await eventually { model.endpoint?.host == "10.0.0.9" && model.connection == .connected })
        #expect(discovery.browseCount == 2)
        model.stop()
    }

    @Test func unreachableAddressBeforeConnectingRebrowses() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")], [address("10.0.0.9")]])
        let probe = SwitchableProbe(["10.0.0.5": .test, "10.0.0.9": .test], down: ["10.0.0.5"])
        let recorder = EndpointRecorder()
        let model = makeModel(
            discovery: discovery, probe: probe, events: ScriptedEvents([:]), recorder: recorder)
        model.start()
        #expect(await eventually { model.connection == .connected })
        #expect(recorder.all.map(\.host) == ["10.0.0.9"])
        #expect(probe.hosts.filter { $0 == "10.0.0.5" }.count == 3)
        #expect(discovery.browseCount == 2)
        model.stop()
    }

    @Test func aDifferentHubIDAtTheNewAddressIsIgnored() async throws {
        let discovery = ScriptedDiscovery([
            [address("10.0.0.5")],
            // Advertises another Hub ID: skipped without even probing it.
            [address("10.0.0.7", id: HubPin.other.hubID),
             // Claims our ID in TXT, but its certificate is another Hub's: untrusted.
             address("10.0.0.8"),
             address("10.0.0.9")],
        ])
        let probe = SwitchableProbe(["10.0.0.5": .test, "10.0.0.7": .test, "10.0.0.8": .other, "10.0.0.9": .test])
        let events = ScriptedEvents(["10.0.0.5": [.connected, waiting, waiting, waiting]])
        let recorder = EndpointRecorder()
        let model = makeModel(discovery: discovery, probe: probe, events: events, recorder: recorder)
        model.start()
        #expect(await eventually { model.endpoint?.host == "10.0.0.9" && model.connection == .connected })
        #expect(recorder.all.map(\.host) == ["10.0.0.5", "10.0.0.9"])
        #expect(!probe.hosts.contains("10.0.0.7"))
        #expect(probe.hosts.contains("10.0.0.8"))
        model.stop()
    }

    @Test func onlyAnotherHubAfterRebrowseNeverGetsTheToken() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")], [address("10.0.0.7", id: HubPin.other.hubID)]])
        let events = ScriptedEvents(["10.0.0.5": [.connected, waiting, waiting, waiting]])
        let recorder = EndpointRecorder()
        let model = makeModel(
            discovery: discovery, probe: SwitchableProbe(["10.0.0.5": .test, "10.0.0.7": .other]),
            events: events, recorder: recorder)
        model.start()
        #expect(await eventually { discovery.browseCount >= 2 })
        try await Task.sleep(for: .milliseconds(50))
        #expect(recorder.all.map(\.host) == ["10.0.0.5"])
        #expect(model.endpoint == nil)
        model.stop()
    }

    @Test func manualHostNeverRebrowses() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.9")]])
        let probe = SwitchableProbe(["10.0.0.5": .test])
        let events = ScriptedEvents(["10.0.0.5": [.connected, waiting, waiting, waiting, waiting, waiting]])
        let model = makeModel(
            settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test),
            discovery: discovery, probe: probe, events: events)
        model.start()
        #expect(await eventually { model.unreachableManualHost == "10.0.0.5" })
        model.networkChanged()
        model.appBecameActive()
        try await Task.sleep(for: .milliseconds(50))
        #expect(discovery.browseCount == 0)
        #expect(events.hosts == ["10.0.0.5"])
        #expect(model.unreachableManualHost == "10.0.0.5")
        model.stop()
    }

    @Test func unreachableManualHostKeepsRetryingAndNamesTheHost() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.9")]])
        let probe = SwitchableProbe([:])
        let model = makeModel(
            settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test),
            discovery: discovery, probe: probe, events: ScriptedEvents([:]))
        model.start()
        #expect(await eventually { probe.hosts.count > 6 })
        #expect(model.unreachableManualHost == "10.0.0.5")
        #expect(discovery.browseCount == 0)
        model.stop()
        #expect(ConnectionState.manualHostUnreachableMessage(host: "10.0.0.5")
            == "Can't reach 10.0.0.5 — check the Mac. Tap for Settings.")
    }

    @Test func bonjourHubIsNotAManualHostWhenUnreachable() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")]])
        let events = ScriptedEvents(["10.0.0.5": [.connected, waiting]])
        let model = makeModel(
            discovery: discovery, probe: SwitchableProbe(["10.0.0.5": .test]), events: events)
        model.start()
        #expect(await eventually { model.connection == .unreachable(reason: "Lost connection to Coco Hub.") })
        #expect(model.unreachableManualHost == nil)
        model.stop()
    }

    @Test func foregroundOrNetworkChangeWhileConnectedKeepsTheConnection() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")]])
        let events = ScriptedEvents(["10.0.0.5": [.connected]])
        let model = makeModel(
            discovery: discovery, probe: SwitchableProbe(["10.0.0.5": .test]), events: events)
        model.start()
        #expect(await eventually { model.connection == .connected })
        model.appBecameActive()
        model.networkChanged()
        try await Task.sleep(for: .milliseconds(50))
        #expect(discovery.browseCount == 1)
        #expect(events.hosts == ["10.0.0.5"])
        model.stop()
    }

    @Test func foregroundWhileUnreachableRebrowsesAtOnce() async throws {
        let discovery = ScriptedDiscovery([[address("10.0.0.5")], [address("10.0.0.9")]])
        let events = ScriptedEvents(["10.0.0.5": [.connected, waiting], "10.0.0.9": [.connected]])
        let model = makeModel(
            discovery: discovery,
            probe: SwitchableProbe(["10.0.0.5": .test, "10.0.0.9": .test]), events: events)
        model.start()
        #expect(await eventually { model.connection == .unreachable(reason: "Lost connection to Coco Hub.") })
        model.appBecameActive()
        #expect(await eventually { model.endpoint?.host == "10.0.0.9" && model.connection == .connected })
        #expect(discovery.browseCount == 2)
        model.stop()
    }
}

@Suite struct OutageTrackerTests {
    private let t0 = Date(timeIntervalSince1970: 0)

    @Test func staleAfterMaxFailures() {
        var tracker = OutageTracker(policy: RediscoveryPolicy(maxFailures: 3, maxOutage: 30))
        let results = [0.0, 1, 2].map { tracker.failed(at: t0.addingTimeInterval($0)) }
        #expect(results == [false, false, true])
    }

    @Test func staleAfterMaxOutageMeasuredFromTheFirstFailure() {
        var tracker = OutageTracker(policy: RediscoveryPolicy(maxFailures: 10, maxOutage: 30))
        let first = tracker.failed(at: t0)
        #expect(!first)
        #expect(!tracker.isStale(at: t0.addingTimeInterval(29)))
        #expect(tracker.isStale(at: t0.addingTimeInterval(30)))
        let later = tracker.failed(at: t0.addingTimeInterval(31))
        #expect(later)
    }

    @Test func successResets() {
        var tracker = OutageTracker(policy: RediscoveryPolicy(maxFailures: 2, maxOutage: 30))
        _ = tracker.failed(at: t0)
        tracker.reset()
        let afterReset = tracker.failed(at: t0.addingTimeInterval(40))
        #expect(!afterReset)
        #expect(!tracker.isStale(at: t0.addingTimeInterval(41)))
    }

    @Test func bonjourTXTRecordIDIsRead() {
        let metadata = NWBrowser.Result.Metadata.bonjour(
            NWTXTRecord(["api": "v1", "id": "3bfe1189a1aea79d"]))
        #expect(BonjourHubDiscovery.hubID(in: metadata) == "3bfe1189a1aea79d")
        #expect(BonjourHubDiscovery.hubID(in: .bonjour(NWTXTRecord(["api": "v1"]))) == nil)
        #expect(BonjourHubDiscovery.hubID(in: .none) == nil)
    }
}
