import Foundation
import Security
import Testing
@testable import CocoKit

private func hex(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }

/// A server trust for `der`, as URLSession would hand the delegate (no network involved).
private func serverTrust(_ der: Data, host: String = "10.0.0.5") throws -> SecTrust {
    let certificate = try #require(SecCertificateCreateWithData(nil, der as CFData))
    var trust: SecTrust?
    let status = SecTrustCreateWithCertificates(
        certificate, SecPolicyCreateSSL(true, host as CFString), &trust)
    #expect(status == errSecSuccess)
    return try #require(trust)
}

@Suite struct HubFingerprintTests {
    @Test func sha256MatchesTheHub() {
        #expect(hex(HubFingerprint.sha256(TestCerts.hubA)) == TestCerts.hubASHA256Hex)
    }

    @Test func shortFormIsSixUpperCaseGroupsOfFourLikeMakeHubFingerprint() {
        #expect(HubFingerprint.short(HubFingerprint.sha256(TestCerts.hubA)) == TestCerts.hubAShort)
        #expect(HubPin.test.fingerprint == TestCerts.hubAShort)
        #expect(HubFingerprint.short(Data(0..<32)) == "0001-0203-0405-0607-0809-0A0B")
    }

    @Test func leafCertificateComesOutOfTheTrust() throws {
        #expect(HubFingerprint.leafCertificate(of: try serverTrust(TestCerts.hubA)) == TestCerts.hubA)
    }

    @Test func pinCodesWithSnakeCaseKeys() throws {
        let json = try JSONEncoder().encode(HubPin.test)
        let object = try #require(JSONSerialization.jsonObject(with: json) as? [String: Any])
        #expect(Set(object.keys) == ["hub_id", "cert_sha256"])
        #expect(try JSONDecoder().decode(HubPin.self, from: json) == .test)
    }
}

@Suite struct PinDecisionTests {
    @Test func exactCertificateIsTrusted() {
        #expect(PinDecision.evaluate(leafCertificate: TestCerts.hubA, pin: .test) == .trusted)
    }

    @Test func anotherCertificateIsRejected() {
        let presented = HubFingerprint.sha256(TestCerts.hubB)
        #expect(
            PinDecision.evaluate(leafCertificate: TestCerts.hubB, pin: .test)
                == .rejected(.mismatch(presented: presented)))
    }

    @Test func nothingIsTrustedBeforePairing() {
        #expect(PinDecision.evaluate(leafCertificate: TestCerts.hubA, pin: nil) == .rejected(.notPaired))
    }

    @Test func missingCertificateIsRejected() {
        #expect(PinDecision.evaluate(leafCertificate: nil, pin: .test) == .rejected(.noCertificate))
    }
}

@Suite struct HubAssessmentTests {
    @Test func pairedHubIsTrusted() {
        #expect(HubAssessment.assess(.success(.test), pin: .test) == .trusted)
    }

    @Test func unpairedAsksToPairWithWhatWasPresented() {
        #expect(HubAssessment.assess(.success(.other), pin: nil) == .needsPairing(.other))
    }

    @Test func otherCertificateOrOtherHubIDIsUntrusted() {
        #expect(HubAssessment.assess(.success(.other), pin: .test) == .untrusted(presented: .other))
        let sameCertNewID = HubPin(hubID: "ffffffffffffffff", certSHA256: HubPin.test.certSHA256)
        #expect(
            HubAssessment.assess(.success(sameCertNewID), pin: .test)
                == .untrusted(presented: sameCertNewID))
    }

    @Test func probeFailureIsUnreachable() {
        #expect(
            HubAssessment.assess(.failure(HubProbeError("down")), pin: .test) == .unreachable("down"))
    }
}

@Suite struct PinningSessionDelegateTests {
    @Test func matchingCertificateIsAcceptedWithACredential() throws {
        let delegate = PinningSessionDelegate(pin: .test)
        let (disposition, credential) = delegate.disposition(
            serverTrust: try serverTrust(TestCerts.hubA), host: "10.0.0.5")
        #expect(disposition == .useCredential)
        #expect(credential != nil)
        #expect(!delegate.wasRejected(host: "10.0.0.5"))
    }

    /// Cancelling the challenge aborts the TLS handshake, so the HTTP request (with its
    /// `Authorization: Bearer` header) is never written to the socket.
    @Test func mismatchedCertificateCancelsTheHandshake() throws {
        let delegate = PinningSessionDelegate(pin: .test)
        let (disposition, credential) = delegate.disposition(
            serverTrust: try serverTrust(TestCerts.hubB), host: "10.0.0.5")
        #expect(disposition == .cancelAuthenticationChallenge)
        #expect(credential == nil)
        #expect(delegate.wasRejected(host: "10.0.0.5"))
        #expect(!delegate.wasRejected(host: "10.0.0.6"))
    }

    @Test func withoutAPinEverythingIsCancelled() throws {
        let delegate = PinningSessionDelegate()
        let (disposition, _) = delegate.disposition(
            serverTrust: try serverTrust(TestCerts.hubA), host: "10.0.0.5")
        #expect(disposition == .cancelAuthenticationChallenge)
    }

    @Test func aLaterGoodHandshakeClearsTheRejection() throws {
        let delegate = PinningSessionDelegate(pin: .test)
        _ = delegate.disposition(serverTrust: try serverTrust(TestCerts.hubB), host: "Mac.local")
        #expect(delegate.wasRejected(host: "mac.local"))
        _ = delegate.disposition(serverTrust: try serverTrust(TestCerts.hubA), host: "mac.local")
        #expect(!delegate.wasRejected(host: "MAC.local"))
    }

    @Test func rePairingReplacesThePinAndForgetsRejections() throws {
        let delegate = PinningSessionDelegate(pin: .test)
        _ = delegate.disposition(serverTrust: try serverTrust(TestCerts.hubB), host: "10.0.0.5")
        delegate.setPin(.other)
        #expect(delegate.pin == .other)
        #expect(!delegate.wasRejected(host: "10.0.0.5"))
        let (disposition, _) = delegate.disposition(
            serverTrust: try serverTrust(TestCerts.hubB), host: "10.0.0.5")
        #expect(disposition == .useCredential)
    }
}

/// Everything that can carry the token is built on the one pinned session.
@Suite struct HubSessionWiringTests {
    let endpoint = HubEndpoint(host: "10.0.0.5", port: 8765, token: "tok")

    @Test func clientUsesThePinnedSession() {
        let hub = HubSession(pin: .test)
        let client = hub.client(for: endpoint)
        #expect(client.session === hub.urlSession)
        #expect(client.session.delegate === hub.pinning)
        #expect(client.endpoint.baseURL.scheme == "https")
    }

    @Test func eventStreamUsesThePinnedSessionOverWSS() throws {
        let hub = HubSession(pin: .test)
        let stream = hub.events(for: endpoint)
        let transport = try #require(stream.transport as? URLSessionEventTransport)
        #expect(transport.session === hub.urlSession)
        #expect(transport.session.delegate === hub.pinning)
        #expect(stream.url.scheme == "wss")
    }

    @Test func mediaLoaderSendsHubMediaThroughThePinnedSessionOnly() {
        let hub = HubSession(pin: .test)
        let loader = hub.mediaLoader(for: endpoint)
        #expect(loader.session === hub.urlSession)
        #expect(loader.session.delegate === hub.pinning)
        #expect(loader.publicSession === URLSession.shared)
        #expect(loader.isHub(URL(string: "https://10.0.0.5:8765/v1/media/x.m4a")!))
        #expect(!loader.isHub(URL(string: "http://10.0.0.5:8765/v1/media/x.m4a")!))
        #expect(!loader.isHub(URL(string: "https://10.0.0.5/v1/media/x.m4a")!))
    }

    @Test func setPinReachesTheDelegate() {
        let hub = HubSession()
        #expect(hub.pinning.pin == nil)
        hub.setPin(.test)
        #expect(hub.pinning.pin == .test)
    }

    @MainActor
    @Test func appModelMediaLoaderUsesItsPinnedSession() async throws {
        let store = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        try store.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test))
        let hub = HubSession()
        let model = AppModel(
            config: store, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
            hubSession: hub,
            makeClient: { _ in FakeHubAPI() },
            makeEvents: { _ in AsyncStream { $0.yield(.connected) } })
        model.start()
        for _ in 0..<200 where model.mediaLoader == nil {
            try await Task.sleep(for: .milliseconds(10))
        }
        let loader = try #require(model.mediaLoader)
        #expect(loader.session === hub.urlSession)
        model.stop()
    }
}

/// A refused handshake surfaces as "untrusted Hub", not a generic network error.
@Suite struct UntrustedHubErrorTests {
    let host = "hub-\(UUID().uuidString.lowercased()).test"

    /// A stubbed session whose delegate is the pinning delegate (the stub has no TLS, so the
    /// rejection is recorded by hand, as the real handshake would).
    private func pinnedStubSession(rejecting host: String) -> URLSession {
        let delegate = PinningSessionDelegate(pin: .test)
        _ = delegate.evaluate(leafCertificate: TestCerts.hubB, host: host)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    @Test func hubClientReportsUntrustedHub() async {
        StubURLProtocol.register(host: host) { _ in throw URLError(.cancelled) }
        let client = HubClient(
            endpoint: HubEndpoint(host: host, port: 8765, token: "t"),
            session: pinnedStubSession(rejecting: host))
        await #expect(throws: HubError.untrustedHub(host)) { try await client.idle() }
    }

    @Test func hubClientWithoutARejectionStaysUnreachable() async {
        StubURLProtocol.register(host: host) { _ in throw URLError(.cannotConnectToHost) }
        let client = HubClient(
            endpoint: HubEndpoint(host: host, port: 8765, token: "t"), session: StubURLProtocol.session)
        await #expect(throws: HubError.unreachable("Could not connect to Coco Hub.")) {
            try await client.idle()
        }
    }

    @Test func mediaLoaderReportsUntrustedHub() async {
        StubURLProtocol.register(host: host) { _ in throw URLError(.cancelled) }
        let loader = HubMediaLoader(
            endpoint: HubEndpoint(host: host, port: 8765, token: "t"),
            session: pinnedStubSession(rejecting: host), cache: nil)
        await #expect(throws: HubError.untrustedHub(host)) {
            try await loader.data(for: "/v1/media/sounds/clips/cow.m4a")
        }
    }

    @Test func eventStreamStopsOnUntrustedHub() async {
        let transport = UntrustedTransport()
        let stream = EventStream(
            url: URL(string: "wss://mac.local:8765/v1/events")!, token: "t", transport: transport,
            sleeper: RecordingSleeper())
        var updates: [EventStreamUpdate] = []
        for await update in stream.updates() { updates.append(update) }  // finishes on its own
        #expect(updates == [.connecting(attempt: 1), .untrustedHub(host: "mac.local")])
    }
}

private struct UntrustedTransport: EventTransport {
    func connect(to url: URL, token: String) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { $0.finish(throwing: EventTransportError.untrustedHub) }
    }
}
