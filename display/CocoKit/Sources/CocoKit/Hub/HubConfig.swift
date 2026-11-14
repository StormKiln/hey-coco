import Foundation
import Security
import Synchronization

/// Where the Hub is and how to authenticate with it.
public struct HubEndpoint: Sendable, Equatable, Hashable {
    public static let defaultPort = 8765

    public var host: String
    public var port: Int
    /// Shared LAN token; sent as `Authorization: Bearer` (REST) or `?token=` (WebSocket).
    public var token: String

    public init(host: String, port: Int, token: String) {
        self.host = host
        self.port = port
        self.token = token
    }

    /// `http://host:port` (IPv6 hosts are bracketed).
    public var baseURL: URL { components(scheme: "http").url! }

    public func url(for path: String) -> URL {
        var components = components(scheme: "http")
        components.path = path
        return components.url!
    }

    /// `ws://host:port/v1/events?token=…`.
    public var eventsURL: URL {
        var components = components(scheme: "ws")
        components.path = "/v1/events"
        components.percentEncodedQuery =
            "token=" + (token.addingPercentEncoding(withAllowedCharacters: .urlQueryValueAllowed) ?? "")
        return components.url!
    }

    private func components(scheme: String) -> URLComponents {
        var components = URLComponents()
        components.scheme = scheme
        // URLComponents wants IPv6 literals pre-bracketed.
        components.percentEncodedHost = host.contains(":") ? "[\(host)]" : host
        components.port = port
        return components
    }
}

extension CharacterSet {
    /// Unreserved characters (RFC 3986): everything else in a query value is percent-encoded.
    static let urlQueryValueAllowed = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
}

/// A Hub found on the network (or typed in by hand).
public struct HubAddress: Sendable, Equatable, Hashable {
    public var host: String
    public var port: Int

    public init(host: String, port: Int) {
        self.host = host
        self.port = port
    }
}

/// What the user configured in Settings.
public struct HubSettings: Sendable, Equatable {
    /// Manual host override; `nil` means "use Bonjour discovery".
    public var manualHost: String?
    /// Port for the manual host.
    public var port: Int
    public var token: String?

    public init(manualHost: String?, port: Int = HubEndpoint.defaultPort, token: String?) {
        self.manualHost = manualHost
        self.port = port
        self.token = token
    }
}

extension HubEndpoint {
    /// Manual host wins; otherwise the first Hub `discovery` finds. `nil` if discovery ends empty.
    /// A missing token resolves to `""` so the caller can still report "unauthorized" sensibly.
    public static func resolve(settings: HubSettings, discovery: any HubDiscovery) async -> HubEndpoint? {
        let token = settings.token ?? ""
        if let host = settings.manualHost {
            return HubEndpoint(host: host, port: settings.port, token: token)
        }
        for await address in discovery.browse() {
            return HubEndpoint(host: address.host, port: address.port, token: token)
        }
        return nil
    }
}

// MARK: - Storage

public protocol HubConfigStore: Sendable {
    func load() -> HubSettings
    func save(_ settings: HubSettings) throws
}

/// Persists the Hub token (a secret, so not in UserDefaults).
public protocol TokenStore: Sendable {
    func readToken() throws -> String?
    /// `nil` deletes the token.
    func writeToken(_ token: String?) throws
}

/// Host and port in `UserDefaults`, token in a `TokenStore` (Keychain in the app).
public final class UserDefaultsHubConfigStore: HubConfigStore, @unchecked Sendable {
    // UserDefaults is thread-safe; it is only not annotated Sendable on every SDK.
    private let defaults: UserDefaults
    private let tokens: any TokenStore

    private enum Key {
        static let host = "hub.manualHost"
        static let port = "hub.port"
    }

    public init(defaults: UserDefaults = .standard, tokens: any TokenStore = KeychainTokenStore()) {
        self.defaults = defaults
        self.tokens = tokens
    }

    public func load() -> HubSettings {
        let port = defaults.object(forKey: Key.port) as? Int ?? HubEndpoint.defaultPort
        return HubSettings(
            manualHost: defaults.string(forKey: Key.host).flatMap(Self.nonBlank),
            port: port,
            token: (try? tokens.readToken()).flatMap { $0 }.flatMap(Self.nonBlank))
    }

    public func save(_ settings: HubSettings) throws {
        if let host = settings.manualHost.flatMap(Self.nonBlank) {
            defaults.set(host, forKey: Key.host)
        } else {
            defaults.removeObject(forKey: Key.host)
        }
        defaults.set(settings.port, forKey: Key.port)
        try tokens.writeToken(settings.token.flatMap(Self.nonBlank))
    }

    private static func nonBlank(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

/// For tests and previews.
public final class InMemoryTokenStore: TokenStore {
    private let token = Mutex<String?>(nil)

    public init(token: String? = nil) { self.token.withLock { $0 = token } }

    public func readToken() throws -> String? { token.withLock { $0 } }
    public func writeToken(_ token: String?) throws { self.token.withLock { $0 = token } }
}

public struct KeychainError: Error, Sendable, Equatable {
    public var status: Int32
}

/// Stores the token as a generic password in the Keychain.
public struct KeychainTokenStore: TokenStore {
    public var service: String
    public var account: String

    public init(service: String = "com.stormkiln.heycoco.display", account: String = "hub-token") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public func readToken() throws -> String? {
        var query = query
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw KeychainError(status: status)
        }
        return String(decoding: data, as: UTF8.self)
    }

    public func writeToken(_ token: String?) throws {
        let deleteStatus = SecItemDelete(query as CFDictionary)
        guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
            throw KeychainError(status: deleteStatus)
        }
        guard let token else { return }
        var item = query
        item[kSecValueData as String] = Data(token.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }
}
