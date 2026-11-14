import CryptoKit
import Foundation
import Security
import Synchronization

// Trust on first use (#54). The Hub serves HTTPS/WSS with a self-signed certificate. The first
// time the Display meets it, the person setting up the iPad compares the fingerprint shown on
// screen with `make hub-fingerprint` on the Mac and taps Pair; the Display then pins that exact
// certificate (and the Hub ID) and never sends its token to a server presenting anything else.

/// The Hub the Display paired with: its ID and the SHA-256 of its DER certificate.
public struct HubPin: Codable, Sendable, Equatable, Hashable {
    public var hubID: String
    /// SHA-256 of the Hub's leaf certificate (DER), 32 bytes.
    public var certSHA256: Data

    public init(hubID: String, certSHA256: Data) {
        self.hubID = hubID
        self.certSHA256 = certSHA256
    }

    /// What people compare: `ABCD-EF01-2345-6789-ABCD-EF01` (as `make hub-fingerprint` prints).
    public var fingerprint: String { HubFingerprint.short(certSHA256) }

    enum CodingKeys: String, CodingKey {
        case hubID = "hub_id"
        case certSHA256 = "cert_sha256"
    }
}

public enum HubFingerprint {
    public static func sha256(_ der: Data) -> Data { Data(SHA256.hash(data: der)) }

    /// The first 12 bytes of `digest` as six upper-case groups of four hex digits.
    public static func short(_ digest: Data) -> String {
        let hex = digest.prefix(12).map { String(format: "%02X", $0) }.joined()
        var groups: [Substring] = []
        var rest = Substring(hex)
        while !rest.isEmpty {
            groups.append(rest.prefix(4))
            rest = rest.dropFirst(4)
        }
        return groups.joined(separator: "-")
    }

    /// The leaf (server) certificate of `trust`, DER-encoded.
    public static func leafCertificate(of trust: SecTrust) -> Data? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
            let leaf = chain.first
        else { return nil }
        return SecCertificateCopyData(leaf) as Data
    }
}

// MARK: - Decisions (pure)

public enum PinRejection: Sendable, Equatable {
    /// No Hub paired yet: nothing may carry the token.
    case notPaired
    /// The server sent no certificate we could read.
    case noCertificate
    /// A different certificate from the pinned one.
    case mismatch(presented: Data)
}

public enum PinDecision: Sendable, Equatable {
    case trusted
    case rejected(PinRejection)

    /// Whether a TLS connection presenting `leafCertificate` (DER) may proceed under `pin`.
    /// Only an exact match of the pinned certificate is trusted; CA trust is never consulted,
    /// so a certificate some public CA vouches for is rejected just the same.
    public static func evaluate(leafCertificate: Data?, pin: HubPin?) -> PinDecision {
        guard let pin else { return .rejected(.notPaired) }
        guard let leafCertificate else { return .rejected(.noCertificate) }
        let presented = HubFingerprint.sha256(leafCertificate)
        return presented == pin.certSHA256 ? .trusted : .rejected(.mismatch(presented: presented))
    }
}

/// What to do with a Hub found on the network (or typed in), given what it presented.
public enum HubAssessment: Sendable, Equatable {
    /// It is the paired Hub: connect and send the token.
    case trusted
    /// Nothing is paired yet: ask the person to confirm this Hub's fingerprint.
    case needsPairing(HubPin)
    /// Some other server (or the Hub with a new certificate): ignore it, never send the token.
    case untrusted(presented: HubPin)
    case unreachable(String)

    public static func assess(_ probe: Result<HubPin, HubProbeError>, pin: HubPin?) -> HubAssessment {
        switch probe {
        case .failure(let error):
            return .unreachable(error.message)
        case .success(let presented):
            guard let pin else { return .needsPairing(presented) }
            return presented == pin ? .trusted : .untrusted(presented: presented)
        }
    }
}

/// A Hub waiting for the person to confirm its fingerprint.
public struct PairingCandidate: Sendable, Equatable, Hashable, Identifiable {
    public var address: HubAddress
    public var pin: HubPin

    public init(address: HubAddress, pin: HubPin) {
        self.address = address
        self.pin = pin
    }

    public var id: Self { self }
}

// MARK: - URLSession delegate

/// Server-trust evaluation for every connection that may carry the token: `HubClient`, the
/// event WebSocket and Hub media. Accepts the TLS handshake only when the leaf certificate's
/// SHA-256 equals the pin, and cancels it otherwise, so the request (and its
/// `Authorization` header) is never sent. With no pin, everything is rejected.
public final class PinningSessionDelegate: NSObject, URLSessionDelegate, Sendable {
    private struct State {
        var pin: HubPin?
        var rejectedHosts: Set<String> = []
    }

    private let state: Mutex<State>

    public init(pin: HubPin? = nil) {
        state = Mutex(State(pin: pin))
    }

    public var pin: HubPin? { state.withLock { $0.pin } }

    /// Replaces the pin (after pairing or forgetting the Hub) and forgets past rejections.
    public func setPin(_ pin: HubPin?) {
        state.withLock { $0 = State(pin: pin) }
    }

    /// True if the last handshake with `host` was refused because of the pin. Lets callers
    /// report "untrusted Hub" instead of a bare "cancelled".
    public func wasRejected(host: String) -> Bool {
        state.withLock { $0.rejectedHosts.contains(host.lowercased()) }
    }

    /// The decision for `leafCertificate` from `host`, recorded for `wasRejected(host:)`.
    public func evaluate(leafCertificate: Data?, host: String) -> PinDecision {
        state.withLock { state in
            let decision = PinDecision.evaluate(leafCertificate: leafCertificate, pin: state.pin)
            if decision == .trusted {
                state.rejectedHosts.remove(host.lowercased())
            } else {
                state.rejectedHosts.insert(host.lowercased())
            }
            return decision
        }
    }

    /// What to answer a server-trust challenge with.
    public func disposition(
        serverTrust: SecTrust, host: String
    ) -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let leaf = HubFingerprint.leafCertificate(of: serverTrust)
        switch evaluate(leafCertificate: leaf, host: host) {
        case .trusted: return (.useCredential, URLCredential(trust: serverTrust))
        case .rejected: return (.cancelAuthenticationChallenge, nil)
        }
    }

    public func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust else {
            return (.performDefaultHandling, nil)
        }
        guard let trust = space.serverTrust else { return (.cancelAuthenticationChallenge, nil) }
        return disposition(serverTrust: trust, host: space.host)
    }
}

/// One `URLSession` for everything that talks to the Hub with the token, behind the pin.
/// `AppModel` updates the pin when the person pairs or forgets the Hub.
public final class HubSession: Sendable {
    public let pinning: PinningSessionDelegate
    public let urlSession: URLSession

    public init(pin: HubPin? = nil, configuration: URLSessionConfiguration = .default) {
        pinning = PinningSessionDelegate(pin: pin)
        urlSession = URLSession(configuration: configuration, delegate: pinning, delegateQueue: nil)
    }

    public func setPin(_ pin: HubPin?) { pinning.setPin(pin) }

    public func client(for endpoint: HubEndpoint) -> HubClient {
        HubClient(endpoint: endpoint, session: urlSession)
    }

    public func events(for endpoint: HubEndpoint) -> EventStream {
        EventStream(
            url: endpoint.eventsURL, token: endpoint.token,
            transport: URLSessionEventTransport(session: urlSession))
    }

    /// Hub media through the pinned session; public pictures through `URLSession.shared`.
    public func mediaLoader(for endpoint: HubEndpoint) -> HubMediaLoader {
        HubMediaLoader(endpoint: endpoint, session: urlSession, publicSession: .shared)
    }
}

extension URLSession {
    /// The pin check that rejected the last handshake with `host`, if this session pins.
    func pinRejected(host: String?) -> Bool {
        guard let host, let pinning = delegate as? PinningSessionDelegate else { return false }
        return pinning.wasRejected(host: host)
    }
}

// MARK: - Probing a Hub before trusting it

public struct HubProbeError: Error, Sendable, Equatable {
    public var message: String
    public init(_ message: String) { self.message = message }
}

/// Learns which Hub answers at an address, without sending the token: its `/healthz`
/// (Hub ID) and the certificate it presents (fingerprint).
public protocol HubProbe: Sendable {
    func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError>
}

/// `HubProbe` over HTTPS: accepts whatever certificate the server presents for this one
/// unauthenticated request, and records it. Trust is decided afterwards by comparing it with
/// the pin (or by the person, when pairing).
public struct HealthzHubProbe: HubProbe {
    public var timeout: TimeInterval = 5

    public init() {}

    public func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError> {
        let capture = CertificateCapturingDelegate()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        let session = URLSession(configuration: configuration, delegate: capture, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let endpoint = HubEndpoint(host: address.host, port: address.port, token: "")
        var request = URLRequest(url: endpoint.url(for: "/healthz"))
        request.timeoutInterval = timeout
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError where error.code == .secureConnectionFailed {
            return .failure(HubProbeError(
                "\(address.host) doesn't offer a secure connection. Update Coco Hub."))
        } catch {
            return .failure(HubProbeError(HubClient.describe(error)))
        }
        guard (response as? HTTPURLResponse)?.statusCode == 200,
            let health = try? CocoJSON.decoder.decode(HubHealth.self, from: data)
        else {
            return .failure(HubProbeError("\(address.host) is not a Coco Hub."))
        }
        guard let leaf = capture.leafCertificate else {
            return .failure(HubProbeError("\(address.host) sent no certificate."))
        }
        return .success(HubPin(hubID: health.hubID, certSHA256: HubFingerprint.sha256(leaf)))
    }
}

/// Accepts any server certificate and keeps the leaf. Only for `HealthzHubProbe`'s
/// unauthenticated `/healthz` request: never used for a request that carries the token.
final class CertificateCapturingDelegate: NSObject, URLSessionDelegate, Sendable {
    private let leaf = Mutex<Data?>(nil)

    var leafCertificate: Data? { leaf.withLock { $0 } }

    func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = space.serverTrust
        else { return (.performDefaultHandling, nil) }
        let der = HubFingerprint.leafCertificate(of: trust)
        leaf.withLock { $0 = der }
        return (.useCredential, URLCredential(trust: trust))
    }
}
