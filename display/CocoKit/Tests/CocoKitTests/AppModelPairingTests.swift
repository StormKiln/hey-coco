import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Records the endpoints `AppModel` built a client for: a client is what carries the token.
final class EndpointRecorder: Sendable {
    let endpoints = Mutex<[HubEndpoint]>([])
    var all: [HubEndpoint] { endpoints.withLock { $0 } }
}

/// Returns `results` in order, then repeats the last one.
final class SequenceProbe: HubProbe {
    private let results: [Result<HubPin, HubProbeError>]
    private let count = Mutex(0)

    init(_ results: [Result<HubPin, HubProbeError>]) { self.results = results }

    var calls: Int { count.withLock { $0 } }

    func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError> {
        let index = count.withLock { n in
            defer { n += 1 }
            return n
        }
        return results[min(index, results.count - 1)]
    }
}

@MainActor
private func makeModel(
    settings: HubSettings = HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test),
    discovery: any HubDiscovery = FakeDiscovery(results: []),
    probe: any HubProbe = FakeHubProbe(),
    updates: [EventStreamUpdate] = [.connected],
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
        makeEvents: { _ in
            AsyncStream { continuation in
                for update in updates { continuation.yield(update) }
            }
        })
}

/// Trust on first use (#54): pairing, untrusted Hubs, forgetting the Hub.
@MainActor
@Suite struct AppModelPairingTests {
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    @Test func unpairedHubAsksToPairAndSendsNoToken() async throws {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t"), recorder: recorder)
        model.start()
        let candidate = PairingCandidate(address: HubAddress(host: "10.0.0.5", port: 8765), pin: .test)
        #expect(await eventually { model.connection == .needsPairing(candidate) })
        #expect(candidate.pin.fingerprint == TestCerts.hubAShort)
        try await Task.sleep(for: .milliseconds(50))
        #expect(recorder.all.isEmpty)
        #expect(model.endpoint == nil)
        model.stop()
    }

    @Test func confirmingPairingPinsTheHubThenConnects() async throws {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: "t"),
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            recorder: recorder)
        model.start()
        let waiting = await eventually {
            if case .needsPairing = model.connection { true } else { false }
        }
        guard waiting, case .needsPairing(let candidate) = model.connection else {
            Issue.record("expected a pairing prompt, got \(model.connection)")
            return
        }
        #expect(recorder.all.isEmpty)
        try model.pair(with: candidate)
        #expect(model.settings.pin == .test)
        #expect(model.settings.token == "t")
        #expect(await eventually { model.connection == .connected })
        #expect(model.hubSession.pinning.pin == .test)
        #expect(recorder.all == [HubEndpoint(host: "mac.local", port: 8765, token: "t")])
        model.stop()
    }

    @Test func manualHostWithAnotherCertificateIsUntrustedAndGetsNoToken() async throws {
        let recorder = EndpointRecorder()
        let model = makeModel(probe: FakeHubProbe(default: .success(.other)), recorder: recorder)
        model.start()
        #expect(await eventually { model.connection == .untrustedHub(host: "10.0.0.5") })
        try await Task.sleep(for: .milliseconds(50))
        #expect(model.connection == .untrustedHub(host: "10.0.0.5"))
        #expect(recorder.all.isEmpty)
        model.stop()
    }

    @Test func sameCertificateButDifferentHubIDIsUntrusted() async throws {
        let recorder = EndpointRecorder()
        let impostor = HubPin(hubID: "0000000000000000", certSHA256: HubPin.test.certSHA256)
        let model = makeModel(probe: FakeHubProbe(default: .success(impostor)), recorder: recorder)
        model.start()
        #expect(await eventually { model.connection == .untrustedHub(host: "10.0.0.5") })
        #expect(recorder.all.isEmpty)
        model.stop()
    }

    @Test func discoverySkipsUnknownHubsAndUsesThePairedOne() async throws {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: "t", pin: .test),
            discovery: FakeDiscovery(results: [
                HubAddress(host: "rogue.local", port: 8765), HubAddress(host: "mac.local", port: 8765),
            ]),
            probe: FakeHubProbe(["rogue.local": .success(.other)]),
            recorder: recorder)
        model.start()
        #expect(await eventually { model.connection == .connected })
        #expect(recorder.all.map(\.host) == ["mac.local"])
        model.stop()
    }

    @Test func discoveryFindingOnlyAnUnknownHubKeepsTheNotice() async throws {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: HubSettings(manualHost: nil, port: 8765, token: "t", pin: .test),
            discovery: FakeDiscovery(results: [HubAddress(host: "rogue.local", port: 8765)]),
            probe: FakeHubProbe(default: .success(.other)),
            recorder: recorder)
        model.start()
        #expect(await eventually { model.connection == .untrustedHub(host: "rogue.local") })
        try await Task.sleep(for: .milliseconds(50))
        #expect(model.connection == .untrustedHub(host: "rogue.local"))
        #expect(recorder.all.isEmpty)
        model.stop()
    }

    @Test func unreachableHubIsRetriedUntilItAnswers() async throws {
        let probe = SequenceProbe([
            .failure(HubProbeError("Could not connect to Coco Hub.")), .success(.test),
        ])
        let model = makeModel(probe: probe)
        model.start()
        #expect(await eventually { model.connection == .connected })
        #expect(probe.calls == 2)
        model.stop()
    }

    @Test func unreachableHubShowsTheReasonWhileRetrying() async throws {
        let probe = SequenceProbe([.failure(HubProbeError("Could not connect to Coco Hub."))])
        let model = makeModel(probe: probe)
        model.start()
        #expect(await eventually { model.connection == .unreachable(reason: "Could not connect to Coco Hub.") })
        #expect(await eventually { probe.calls > 2 })
        model.stop()
    }

    @Test func certificateMismatchOnTheEventStreamIsUntrusted() async throws {
        let model = makeModel(updates: [.connecting(attempt: 1), .untrustedHub(host: "10.0.0.5")])
        model.start()
        #expect(await eventually { model.connection == .untrustedHub(host: "10.0.0.5") })
        model.stop()
    }

    @Test func forgettingTheHubClearsPinAndTokenAndNeedsBothAgain() async throws {
        let model = makeModel()
        model.start()
        #expect(await eventually { model.connection == .connected })
        try model.forgetHub()
        #expect(model.settings.pin == nil)
        #expect(model.settings.token == nil)
        #expect(model.settings.manualHost == "10.0.0.5")
        #expect(await eventually { model.connection == .unauthorized })
        #expect(model.hubSession.pinning.pin == nil)
        model.stop()
    }

    @Test func afterForgettingEnteringTheTokenAsksToPairAgain() async throws {
        let model = makeModel()
        model.start()
        #expect(await eventually { model.connection == .connected })
        try model.forgetHub()
        guard case .success(let edited) = model.settings.applyingEdit(
            host: "10.0.0.5", port: 8765, newToken: "t2")
        else {
            Issue.record("edit failed")
            return
        }
        try model.saveSettings(edited)
        let candidate = PairingCandidate(address: HubAddress(host: "10.0.0.5", port: 8765), pin: .test)
        #expect(await eventually { model.connection == .needsPairing(candidate) })
        model.stop()
    }
}
