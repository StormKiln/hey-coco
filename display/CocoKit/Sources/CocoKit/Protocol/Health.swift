import Foundation

/// `GET /healthz` (unauthenticated): liveness plus the Hub's identity, read when pairing.
///
/// `fingerprint` is advisory (`null` when the Hub runs without TLS): the Display computes the
/// fingerprint itself from the certificate it was served, and only that is pinned.
public struct HubHealth: Codable, Sendable, Equatable {
    public var status: String
    public var hubID: String
    public var fingerprint: String?

    public init(status: String = "ok", hubID: String, fingerprint: String?) {
        self.status = status
        self.hubID = hubID
        self.fingerprint = fingerprint
    }

    enum CodingKeys: String, CodingKey {
        case status
        case hubID = "hub_id"
        case fingerprint
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(status, forKey: .status)
        try container.encode(hubID, forKey: .hubID)
        try container.encode(fingerprint, forKey: .fingerprint)
    }
}
