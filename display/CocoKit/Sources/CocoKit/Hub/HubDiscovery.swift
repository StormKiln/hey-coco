import Foundation
import Network

/// Finds Coco Hubs on the LAN.
public protocol HubDiscovery: Sendable {
    /// Yields each Hub found, resolved to host:port. Browsing stops when the consumer stops
    /// iterating (or cancels).
    func browse() -> AsyncStream<HubAddress>
}

/// Bonjour browse for `_coco._tcp` (the Hub advertises it on its HTTP port).
///
/// Each found service is resolved by opening a short-lived TCP connection to it and reading the
/// remote host:port, preferring IPv4 so the address works in an `http://` URL without a zone id.
public struct BonjourHubDiscovery: HubDiscovery {
    public static let serviceType = "_coco._tcp"

    public init() {}

    public func browse() -> AsyncStream<HubAddress> {
        AsyncStream { continuation in
            let queue = DispatchQueue(label: "coco.discovery")
            let browser = NWBrowser(
                for: .bonjourWithTXTRecord(type: Self.serviceType, domain: nil), using: .tcp)
            browser.browseResultsChangedHandler = { _, changes in
                for change in changes {
                    let result: NWBrowser.Result
                    switch change {
                    case .added(let added): result = added
                    // e.g. the TXT record or the interface changed: resolve again.
                    case .changed(_, let new, _): result = new
                    default: continue
                    }
                    let hubID = Self.hubID(in: result.metadata)
                    Self.resolve(result.endpoint, on: queue) { address in
                        guard var address else { return }
                        address.advertisedHubID = hubID
                        continuation.yield(address)
                    }
                }
            }
            browser.stateUpdateHandler = { state in
                if case .failed = state { continuation.finish() }
            }
            continuation.onTermination = { _ in browser.cancel() }
            browser.start(queue: queue)
        }
    }

    /// The `id` key of the service's TXT record (see `docs/protocol/README.md`).
    static func hubID(in metadata: NWBrowser.Result.Metadata) -> String? {
        guard case .bonjour(let record) = metadata, let id = record["id"], !id.isEmpty else {
            return nil
        }
        return id
    }

    private static func resolve(
        _ endpoint: NWEndpoint, on queue: DispatchQueue,
        completion: @escaping @Sendable (HubAddress?) -> Void
    ) {
        let parameters = NWParameters.tcp
        if let ip = parameters.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: parameters)
        connection.stateUpdateHandler = { state in
            switch state {
            case .ready:
                completion(connection.currentPath?.remoteEndpoint.flatMap(address(of:)))
                connection.cancel()
            case .failed, .waiting:
                completion(nil)
                connection.cancel()
            default: break
            }
        }
        connection.start(queue: queue)
    }

    private static func address(of endpoint: NWEndpoint) -> HubAddress? {
        guard case .hostPort(let host, let port) = endpoint else { return nil }
        let name: String
        switch host {
        case .ipv4(let address): name = "\(address)"
        case .ipv6(let address): name = "\(address)"
        case .name(let hostname, _): name = hostname
        @unknown default: return nil
        }
        // Drop any interface scope (`%en0`).
        let bare = name.split(separator: "%", maxSplits: 1).first.map(String.init) ?? name
        return HubAddress(host: bare, port: Int(port.rawValue))
    }
}
