import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Bonjour that finds nothing and never gives up: what the iPad saw while the Mac's Bonjour
/// advertisement failed (the Hub's process had no Local Network permission).
final class SilentDiscovery: HubDiscovery {
    private let count = Mutex(0)
    var browseCount: Int { count.withLock { $0 } }

    func browse() -> AsyncStream<HubAddress> {
        count.withLock { $0 += 1 }
        return AsyncStream { _ in }
    }
}

/// Answers with `pin` for hosts that are up; the others can't be reached until `bringUp`.
final class GatedProbe: HubProbe {
    private let answers: [String: HubPin]
    private let up: Mutex<Set<String>>
    let probed = Mutex<[String]>([])

    init(_ answers: [String: HubPin], up: Set<String>) {
        self.answers = answers
        self.up = Mutex(up)
    }

    var hosts: [String] { probed.withLock { $0 } }
    func bringUp(_ host: String) { _ = up.withLock { $0.insert(host) } }

    func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError> {
        probed.withLock { $0.append(address.host) }
        guard up.withLock({ $0.contains(address.host) }), let pin = answers[address.host] else {
            return .failure(HubProbeError("Could not connect to Coco Hub."))
        }
        return .success(pin)
    }
}

private let kitchenMac = HubAddress(host: "192.168.4.32", port: 8765)

private func paired(lastKnown: HubAddress? = kitchenMac) -> HubSettings {
    var settings = HubSettings(manualHost: nil, port: 8765, token: "t", pin: .test)
    settings.lastKnownAddress = lastKnown
    return settings
}

@MainActor
private func makeModel(
    settings: HubSettings,
    discovery: any HubDiscovery = SilentDiscovery(),
    probe: any HubProbe,
    events: ScriptedEvents = ScriptedEvents([:]),
    searchSleeper: ManualSleeper = ManualSleeper(),
    recorder: EndpointRecorder = EndpointRecorder(),
    redeemer: any PairingCodeRedeemer = PinnedPairingCodeRedeemer()
) -> (AppModel, UserDefaultsHubConfigStore) {
    let store = UserDefaultsHubConfigStore(
        defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
        tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
    try! store.save(settings)
    let model = AppModel(
        config: store, discovery: discovery, probe: probe, sleeper: InstantSleeper(),
        makeClient: { endpoint in
            recorder.endpoints.withLock { $0.append(endpoint) }
            return FakeHubAPI()
        },
        makeEvents: { events.stream(for: $0) },
        redeemer: redeemer,
        searchSleep: searchSleeper.sleep)
    return (model, store)
}

/// #91: find the paired Hub at its last known address when Bonjour finds nothing, and don't
/// search silently forever.
@MainActor
@Suite struct AppModelLastKnownAddressTests {
    @Test func withBonjourSilentItConnectsToTheLastKnownAddressAtOnce() async throws {
        let recorder = EndpointRecorder()
        let (model, _) = makeModel(
            settings: paired(), probe: GatedProbe([kitchenMac.host: .test], up: [kitchenMac.host]),
            recorder: recorder)
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        #expect(recorder.all == [HubEndpoint(host: kitchenMac.host, port: 8765, token: "t")])
        model.stop()
    }

    /// At launch the scene becomes active while still searching, which restarts the search:
    /// that restart must not hold the last known address back (found in the simulator).
    @Test func becomingActiveWhileSearchingStillTriesTheLastKnownAddressAtOnce() async throws {
        let sleeper = ManualSleeper()
        let (model, _) = makeModel(
            settings: paired(), probe: GatedProbe([kitchenMac.host: .test], up: [kitchenMac.host]),
            searchSleeper: sleeper)
        model.start()
        model.appBecameActive()
        // No sleep is ever fired here: only an immediate probe can connect.
        #expect(await waitUntil { model.connection == .connected })
        #expect(model.endpoint?.host == kitchenMac.host)
        model.stop()
    }

    @Test func anotherCertificateAtTheLastKnownAddressNeverGetsTheToken() async throws {
        let recorder = EndpointRecorder()
        let probe = GatedProbe([kitchenMac.host: .other], up: [kitchenMac.host])
        let (model, _) = makeModel(settings: paired(), probe: probe, recorder: recorder)
        model.start()
        #expect(await waitUntil { probe.hosts.contains(kitchenMac.host) })
        try await Task.sleep(for: .milliseconds(50))
        #expect(recorder.all.isEmpty)
        #expect(model.endpoint == nil)
        // A stale hint, not a Hub the person chose: still searching, no "unknown Hub" alarm.
        #expect(model.connection == .searching)
        model.stop()
    }

    @Test func aManualHostIsUsedInsteadOfTheLastKnownAddress() async throws {
        var settings = paired()
        settings.manualHost = "10.0.0.5"
        let probe = GatedProbe(["10.0.0.5": .test, kitchenMac.host: .test], up: ["10.0.0.5", kitchenMac.host])
        let recorder = EndpointRecorder()
        let (model, _) = makeModel(settings: settings, probe: probe, recorder: recorder)
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        #expect(recorder.all.map(\.host) == ["10.0.0.5"])
        #expect(!probe.hosts.contains(kitchenMac.host))
        model.stop()
    }

    @Test func aConnectionFoundWithBonjourIsRememberedAsTheLastKnownAddress() async throws {
        let found = HubAddress(host: "10.0.0.7", port: 8765, advertisedHubID: HubPin.test.hubID)
        let (model, store) = makeModel(
            settings: paired(lastKnown: nil), discovery: ScriptedDiscovery([[found]]),
            probe: GatedProbe(["10.0.0.7": .test], up: ["10.0.0.7"]))
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        let remembered = HubAddress(host: "10.0.0.7", port: 8765)
        #expect(await waitUntil { store.load().lastKnownAddress == remembered })
        #expect(model.settings.lastKnownAddress == remembered)
        #expect(store.load().token == "t")
        model.stop()
    }

    @Test func scanningAQRCodeRemembersTheAddressThatAnswered() async throws {
        let uri = PairingURI(
            host: kitchenMac.host, port: 8765, token: "tok", certSHA256: HubPin.test.certSHA256)
        let (model, store) = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: nil),
            probe: GatedProbe([kitchenMac.host: .test], up: [kitchenMac.host]))
        try await model.pair(scanned: uri)
        #expect(store.load().lastKnownAddress == kitchenMac)
        #expect(store.load().pin == .test)
        model.stop()
    }

    @Test func confirmingAFingerprintRemembersTheCandidatesAddress() throws {
        let (model, store) = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: "t"),
            probe: GatedProbe([:], up: []))
        try model.pair(with: PairingCandidate(address: kitchenMac, pin: .test))
        #expect(store.load().lastKnownAddress == kitchenMac)
        model.stop()
    }

    @Test func forgettingTheHubForgetsItsAddress() throws {
        let (model, store) = makeModel(settings: paired(), probe: GatedProbe([:], up: []))
        try model.forgetHub()
        #expect(store.load().lastKnownAddress == nil)
        model.stop()
    }

    @Test func afterThirtySecondsOfSearchingItSaysWhatToCheckAndKeepsTrying() async throws {
        let sleeper = ManualSleeper()
        let probe = GatedProbe([kitchenMac.host: .test], up: [])
        let (model, _) = makeModel(settings: paired(), probe: probe, searchSleeper: sleeper)
        model.start()
        #expect(await waitUntil { sleeper.pending.contains(.seconds(30)) })
        #expect(model.connection == .searching)
        #expect(!model.searchTakingLong)
        sleeper.fire(.seconds(30))
        #expect(await waitUntil { model.searchTakingLong })
        #expect(model.connection == .searching)
        // The last known address is still retried in the background; once the Hub answers
        // there, it connects and the guidance goes away.
        probe.bringUp(kitchenMac.host)
        #expect(await waitUntil {
            sleeper.fire()
            return model.connection == .connected
        })
        #expect(!model.searchTakingLong)
        model.stop()
    }

    @Test func searchingWithoutAnyAddressAlsoTimesOutIntoGuidance() async throws {
        let sleeper = ManualSleeper()
        let (model, _) = makeModel(
            settings: paired(lastKnown: nil), probe: GatedProbe([:], up: []), searchSleeper: sleeper)
        model.start()
        #expect(await waitUntil { sleeper.pending == [.seconds(30)] })
        sleeper.fire()
        #expect(await waitUntil { model.searchTakingLong })
        #expect(ConnectionState.searchTakingLongMessage.contains("Local Network"))
        model.stop()
        #expect(!model.searchTakingLong)
    }
}
