import Foundation

// Easier pairing (#89). `make hub-pair` on the Mac prints a QR code holding a `PairingURI` (the
// Hub's address hint, port, token and full certificate SHA-256) and a one-time 6-digit code.
//
// - QR code: the Display pins the scanned certificate hash and pairs only if the Hub it reaches
//   presents exactly that certificate. No fingerprint comparison needed.
// - Code: the person confirms the fingerprint as before, then the code goes to `POST /v1/pair`
//   over TLS pinned to that certificate, and the Hub answers with the token.
// - Typing the token in Settings stays as the last resort.

/// `cocohub://pair?v=1&host=<hint>&port=<port>&token=<token>&cert=<64 hex>`.
public struct PairingURI: Sendable, Equatable {
    public static let scheme = "cocohub"
    public static let version = "1"

    /// Where to look for the Hub first. Only a hint: trust comes from `certSHA256`.
    public var host: String
    public var port: Int
    public var token: String
    /// SHA-256 of the Hub's DER certificate, 32 bytes.
    public var certSHA256: Data

    public var address: HubAddress { HubAddress(host: host, port: port) }
    public var fingerprint: String { HubFingerprint.short(certSHA256) }

    public static func parse(_ text: String) throws(PairingURIError) -> PairingURI {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed),
            components.scheme?.lowercased() == scheme, components.host == "pair"
        else { throw .notCocoHub }
        var query: [String: String] = [:]
        for item in components.queryItems ?? [] where query[item.name] == nil {
            query[item.name] = item.value ?? ""
        }
        guard query["v"] == version else { throw .unsupportedVersion }
        guard let host = query["host"]?.trimmingCharacters(in: .whitespaces), !host.isEmpty else {
            throw .invalid("host")
        }
        guard let port = query["port"].flatMap(Int.init), (1...65535).contains(port) else {
            throw .invalid("port")
        }
        guard let token = query["token"],
            !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { throw .invalid("token") }
        guard let cert = query["cert"].flatMap(Self.sha256(hex:)) else { throw .invalid("cert") }
        return PairingURI(host: host, port: port, token: token, certSHA256: cert)
    }

    private static func sha256(hex: String) -> Data? {
        let digits = Array(hex.utf8)
        guard digits.count == 64 else { return nil }
        var bytes = Data(capacity: 32)
        for index in stride(from: 0, to: 64, by: 2) {
            guard let high = nibble(digits[index]), let low = nibble(digits[index + 1]) else {
                return nil
            }
            bytes.append(high << 4 | low)
        }
        return bytes
    }

    private static func nibble(_ c: UInt8) -> UInt8? {
        switch c {
        case UInt8(ascii: "0")...UInt8(ascii: "9"): c - UInt8(ascii: "0")
        case UInt8(ascii: "a")...UInt8(ascii: "f"): c - UInt8(ascii: "a") + 10
        case UInt8(ascii: "A")...UInt8(ascii: "F"): c - UInt8(ascii: "A") + 10
        default: nil
        }
    }
}

public enum PairingURIError: Error, Sendable, Equatable {
    /// Some other QR code.
    case notCocoHub
    /// A Coco Hub QR code this Display doesn't understand (update the app).
    case unsupportedVersion
    /// A Coco Hub QR code with a missing or bad field.
    case invalid(String)

    public var message: String {
        switch self {
        case .notCocoHub: "That isn't a Coco Hub QR code. Run make hub-pair on the Mac and scan the code it shows."
        case .unsupportedVersion: "This Coco Hub QR code is newer than this app. Update Coco, or use Enter code."
        case .invalid: "That Coco Hub QR code is damaged. Run make hub-pair again and rescan."
        }
    }
}

public enum PairingCode {
    /// The 6 digits of what the person typed (spaces and dashes ignored), or `nil`.
    public static func normalize(_ typed: String) -> String? {
        let digits = typed.filter { $0 != " " && $0 != "-" }
        guard digits.count == 6, digits.allSatisfy({ ("0"..."9").contains($0) }) else { return nil }
        return digits
    }
}

/// Trades a pairing code for the token at a Hub, over TLS pinned to `pin`.
public protocol PairingCodeRedeemer: Sendable {
    func redeem(code: String, at address: HubAddress, pin: HubPin) async throws(HubError) -> String
}

/// `POST /v1/pair` on a one-off session pinned to the certificate the person confirmed, so the
/// code (and the token coming back) only ever travel to that Hub. A different certificate
/// cancels the handshake: `HubError.untrustedHub`.
public struct PinnedPairingCodeRedeemer: PairingCodeRedeemer {
    public init() {}

    public func redeem(
        code: String, at address: HubAddress, pin: HubPin
    ) async throws(HubError) -> String {
        let session = HubSession(pin: pin, configuration: .ephemeral)
        defer { session.urlSession.finishTasksAndInvalidate() }
        let endpoint = HubEndpoint(host: address.host, port: address.port, token: "")
        return try await session.client(for: endpoint).pair(code: code)
    }
}

public enum PairingError: Error, Sendable, Equatable {
    /// Not 6 digits; nothing was sent.
    case invalidCode
    /// The Hub refused the code, or couldn't be reached.
    case hub(HubError)
    /// The Hub at `host` presents a different certificate from the scanned QR code. Nothing
    /// was saved and no token was sent.
    case certificateMismatch(host: String)
    /// No Hub answered at the scanned address (or the one found on the network).
    case unreachable(String)
    /// Paired, but saving the pin or token failed.
    case notSaved(String)

    public var message: String {
        switch self {
        case .invalidCode:
            return "Enter the 6-digit code from make hub-pair."
        case .hub(.server(code: "not_found", _)):
            return "This Coco Hub can't pair with a code yet. Update Coco Hub, or enter the token in Settings."
        case .hub(.server(_, let message)):
            return message
        case .hub(.untrustedHub(let host)):
            return "\(host) isn't the Coco Hub with that fingerprint. Nothing was sent."
        case .hub(.unreachable(let reason)), .unreachable(let reason):
            return "Couldn't reach Coco Hub: \(reason)"
        case .hub(.unauthorized):
            return "Coco Hub refused the code. Run make hub-pair again."
        case .hub(.decoding):
            return "Coco Hub's answer didn't make sense. Update Coco Hub and try again."
        case .certificateMismatch(let host):
            return "The Coco Hub at \(host) doesn't match the QR code, so Coco didn't pair. Run make hub-pair again on the Hub's Mac and rescan."
        case .notSaved(let reason):
            return "Couldn't save the pairing: \(reason)"
        }
    }
}
