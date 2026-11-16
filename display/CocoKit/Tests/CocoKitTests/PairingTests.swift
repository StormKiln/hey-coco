import Foundation
import Synchronization
import Testing
@testable import CocoKit

private let certHex = TestCerts.hubASHA256Hex

/// The QR code `make hub-pair` prints (#89).
@Suite struct PairingURITests {
    @Test func parsesTheHubsURI() throws {
        // Exactly what `coco_hub.pairing.pairing_uri` builds (hub/tests/test_pairing.py).
        let uri = try PairingURI.parse(
            "cocohub://pair?v=1&host=192.168.1.20&port=8765&token=a%20b%26c%3Dd&cert=\(certHex)")
        #expect(uri.host == "192.168.1.20")
        #expect(uri.port == 8765)
        #expect(uri.token == "a b&c=d")
        #expect(uri.certSHA256 == HubFingerprint.sha256(TestCerts.hubA))
        #expect(uri.fingerprint == TestCerts.hubAShort)
        #expect(uri.address == HubAddress(host: "192.168.1.20", port: 8765))
    }

    @Test func acceptsUpperCaseHexAndSurroundingWhitespace() throws {
        let uri = try PairingURI.parse(
            "  cocohub://pair?v=1&host=mac.local&port=59510&token=t&cert=\(certHex.uppercased())\n")
        #expect(uri.certSHA256 == HubFingerprint.sha256(TestCerts.hubA))
        #expect(uri.port == 59510)
    }

    @Test(arguments: [
        ("https://example.com/?v=1", PairingURIError.notCocoHub),
        ("cocohub://other?v=1&host=h&port=1&token=t&cert=\(certHex)", .notCocoHub),
        ("hello", .notCocoHub),
        ("cocohub://pair?v=2&host=h&port=1&token=t&cert=\(certHex)", .unsupportedVersion),
        ("cocohub://pair?host=h&port=1&token=t&cert=\(certHex)", .unsupportedVersion),
        ("cocohub://pair?v=1&port=1&token=t&cert=\(certHex)", .invalid("host")),
        ("cocohub://pair?v=1&host=&port=1&token=t&cert=\(certHex)", .invalid("host")),
        ("cocohub://pair?v=1&host=h&port=0&token=t&cert=\(certHex)", .invalid("port")),
        ("cocohub://pair?v=1&host=h&port=65536&token=t&cert=\(certHex)", .invalid("port")),
        ("cocohub://pair?v=1&host=h&port=x&token=t&cert=\(certHex)", .invalid("port")),
        ("cocohub://pair?v=1&host=h&port=1&cert=\(certHex)", .invalid("token")),
        ("cocohub://pair?v=1&host=h&port=1&token=%20&cert=\(certHex)", .invalid("token")),
        ("cocohub://pair?v=1&host=h&port=1&token=t&cert=abc", .invalid("cert")),
        ("cocohub://pair?v=1&host=h&port=1&token=t&cert=\(String(certHex.dropLast()))g", .invalid("cert")),
        ("cocohub://pair?v=1&host=h&port=1&token=t", .invalid("cert")),
    ])
    func rejects(_ text: String, _ expected: PairingURIError) {
        #expect(throws: expected) { _ = try PairingURI.parse(text) }
    }
}

@Suite struct PairingCodeTests {
    @Test(arguments: [("123456", "123456"), ("123 456", "123456"), (" 123-456 ", "123456")])
    func normalizes(_ typed: String, _ code: String) {
        #expect(PairingCode.normalize(typed) == code)
    }

    @Test(arguments: ["", "12345", "1234567", "12345a", "１２３４５６"])
    func rejects(_ typed: String) {
        #expect(PairingCode.normalize(typed) == nil)
    }
}

/// `POST /v1/pair` (#89).
@Suite struct HubClientPairTests {
    let host = "hub-\(UUID().uuidString.lowercased()).test"
    var client: HubClient {
        HubClient(endpoint: HubEndpoint(host: host, port: 8765, token: ""), session: StubURLProtocol.session)
    }

    @Test func sendsTheCodeWithoutABearerAndReturnsTheToken() async throws {
        StubURLProtocol.register(host: host) { _ in (200, try Fixtures.data("pair_response")) }
        let token = try await client.pair(code: "482913")
        #expect(token == "kq3V8mZr2xT0bH7nL5cW9yPd")
        let sent = try #require(StubURLProtocol.recorded(host: host).last)
        #expect(sent.httpMethod == "POST")
        #expect(sent.url?.path == "/v1/pair")
        #expect(sent.value(forHTTPHeaderField: "Authorization") == nil)
        let body = try CocoJSON.decoder.decode(PairRequest.self, from: try #require(sent.httpBody))
        #expect(body == PairRequest(code: "482913"))
    }

    @Test(arguments: [
        (401, "pairing_code_wrong"), (404, "no_pairing_code"), (410, "pairing_code_expired"),
        (429, "pairing_code_locked"),
    ])
    func pairingErrorsKeepTheirCode(status: Int, code: String) async throws {
        StubURLProtocol.register(host: host) { _ in
            (status, Data(#"{"error": {"code": "\#(code)", "message": "Nope."}}"#.utf8))
        }
        await #expect(throws: HubError.server(code: code, message: "Nope.")) {
            _ = try await client.pair(code: "000000")
        }
    }
}

/// Records what the Display tried to redeem, and answers with `result`.
final class FakeRedeemer: PairingCodeRedeemer {
    let calls = Mutex<[(String, HubAddress, HubPin)]>([])
    let result: Result<String, HubError>

    init(_ result: Result<String, HubError> = .success("fresh-token")) { self.result = result }

    func redeem(code: String, at address: HubAddress, pin: HubPin) async throws(HubError) -> String {
        calls.withLock { $0.append((code, address, pin)) }
        return try result.get()
    }
}

@MainActor
private func makeModel(
    settings: HubSettings,
    discovery: any HubDiscovery = FakeDiscovery(results: []),
    probe: any HubProbe = FakeHubProbe(),
    redeemer: any PairingCodeRedeemer = FakeRedeemer(),
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
        makeEvents: { _ in AsyncStream { $0.yield(.connected) } },
        redeemer: redeemer)
}

@MainActor
@Suite struct AppModelEasyPairingTests {
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    private let unpaired = HubSettings(manualHost: nil, port: 8765, token: nil)

    @Test func withNoTokenAndNoPinItOffersToPairWithoutSendingAnything() async {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: unpaired,
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            recorder: recorder)
        model.start()
        let candidate = PairingCandidate(address: HubAddress(host: "mac.local", port: 8765), pin: .test)
        #expect(await eventually { model.connection == .needsPairing(candidate) })
        #expect(recorder.all.isEmpty)
        model.stop()
    }

    @Test func aPairedHubWithNoTokenStillNeedsTheToken() async {
        let model = makeModel(settings: HubSettings(manualHost: "10.0.0.5", token: nil, pin: .test))
        model.start()
        #expect(await eventually { model.connection == .unauthorized })
        model.stop()
    }

    // MARK: Code

    @Test func enteringTheCodeRedeemsItOverTheCandidatesPinThenConnects() async throws {
        let redeemer = FakeRedeemer()
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: unpaired,
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            redeemer: redeemer, recorder: recorder)
        let candidate = PairingCandidate(address: HubAddress(host: "mac.local", port: 8765), pin: .test)
        try await model.pair(with: candidate, code: " 482 913 ")
        let calls = redeemer.calls.withLock { $0 }
        #expect(calls.count == 1)
        #expect(calls.first?.0 == "482913")
        #expect(calls.first?.1 == candidate.address)
        #expect(calls.first?.2 == .test)
        #expect(model.settings.pin == .test)
        #expect(model.settings.token == "fresh-token")
        #expect(model.settings.manualHost == nil)
        #expect(await eventually { model.connection == .connected })
        #expect(recorder.all == [HubEndpoint(host: "mac.local", port: 8765, token: "fresh-token")])
        model.stop()
    }

    @Test func aMalformedCodeIsRejectedWithoutAsking() async {
        let redeemer = FakeRedeemer()
        let model = makeModel(settings: unpaired, redeemer: redeemer)
        let candidate = PairingCandidate(address: HubAddress(host: "mac.local", port: 8765), pin: .test)
        await #expect(throws: PairingError.invalidCode) {
            try await model.pair(with: candidate, code: "12345")
        }
        #expect(redeemer.calls.withLock { $0.isEmpty })
        #expect(model.settings.pin == nil)
    }

    @Test func aRefusedCodeSavesNothing() async {
        let refusal = HubError.server(code: "pairing_code_wrong", message: "That code is wrong.")
        let model = makeModel(settings: unpaired, redeemer: FakeRedeemer(.failure(refusal)))
        let candidate = PairingCandidate(address: HubAddress(host: "mac.local", port: 8765), pin: .test)
        await #expect(throws: PairingError.hub(refusal)) {
            try await model.pair(with: candidate, code: "000000")
        }
        #expect(model.settings.pin == nil)
        #expect(model.settings.token == nil)
    }

    @Test func refusalsReadWell() {
        #expect(PairingError.invalidCode.message == "Enter the 6-digit code from make hub-pair.")
        #expect(
            PairingError.hub(.server(code: "pairing_code_wrong", message: "That code is wrong."))
                .message == "That code is wrong.")
        #expect(PairingError.hub(.untrustedHub("mac.local")).message.contains("fingerprint"))
        #expect(PairingError.certificateMismatch(host: "mac.local").message.contains("mac.local"))
    }

    // MARK: QR code

    private var scanned: PairingURI {
        try! PairingURI.parse(
            "cocohub://pair?v=1&host=192.168.1.20&port=8765&token=qr-token&cert=\(certHex)")
    }

    @Test func scanningPinsTheHubPresentingThatCertificate() async throws {
        let probe = FakeHubProbe()
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: unpaired,
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            probe: probe, recorder: recorder)
        try await model.pair(scanned: scanned)
        #expect(probe.probed.withLock { $0 }.first == "192.168.1.20")
        #expect(model.settings.pin == .test)  // the Hub ID comes from the probe
        #expect(model.settings.token == "qr-token")
        #expect(model.settings.manualHost == nil)  // still found with Bonjour
        #expect(await eventually { model.connection == .connected })
        #expect(recorder.all.allSatisfy { $0.token == "qr-token" })
        model.stop()
    }

    @Test func scanningFailsWhenTheHubPresentsAnotherCertificate() async {
        let recorder = EndpointRecorder()
        let model = makeModel(
            settings: unpaired, probe: FakeHubProbe(default: .success(.other)), recorder: recorder)
        await #expect(throws: PairingError.certificateMismatch(host: "192.168.1.20")) {
            try await model.pair(scanned: scanned)
        }
        #expect(model.settings.pin == nil)
        #expect(model.settings.token == nil)
        #expect(recorder.all.isEmpty)
    }

    @Test func scanningFallsBackToTheHubFoundOnTheNetwork() async throws {
        // The address in the QR code is only a hint (the Mac may have several).
        let probe = FakeHubProbe(["192.168.1.20": .failure(HubProbeError("Could not connect"))])
        let model = makeModel(
            settings: unpaired,
            discovery: FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)]),
            probe: probe)
        model.start()
        #expect(await eventually {
            if case .needsPairing = model.connection { true } else { false }
        })
        try await model.pair(scanned: scanned)
        #expect(probe.probed.withLock { $0 }.contains("192.168.1.20"))
        #expect(model.settings.pin == .test)
        #expect(model.settings.token == "qr-token")
        #expect(await eventually { model.connection == .connected })
        model.stop()
    }

    @Test func scanningAnUnreachableHubSavesNothing() async {
        let model = makeModel(
            settings: unpaired, probe: FakeHubProbe(default: .failure(HubProbeError("Could not connect"))))
        await #expect(throws: PairingError.unreachable("Could not connect")) {
            try await model.pair(scanned: scanned)
        }
        #expect(model.settings.pin == nil)
    }

    @Test func scanningKeepsAManualHost() async throws {
        let model = makeModel(settings: HubSettings(manualHost: "10.0.0.5", port: 8765, token: nil))
        try await model.pair(scanned: scanned)
        #expect(model.settings.manualHost == "10.0.0.5")
        #expect(model.settings.token == "qr-token")
        model.stop()
    }
}
