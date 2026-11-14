import Foundation
import Security
import Synchronization

/// Where the Hub is and how to authenticate with it.
public struct HubEndpoint: Sendable, Equatable, Hashable {
    public static let defaultPort = 8765

    public var host: String
    public var port: Int
    /// Shared LAN token; sent as `Authorization: Bearer` on REST requests and the WebSocket
    /// upgrade. Never put in a URL.
    public var token: String

    public init(host: String, port: Int, token: String) {
        self.host = host
        self.port = port
        self.token = token
    }

    /// `https://host:port` (IPv6 hosts are bracketed). The Hub serves TLS only; its
    /// certificate is checked against the pin (`PinningSessionDelegate`), not a CA.
    public var baseURL: URL { components(scheme: "https").url! }

    public func url(for path: String) -> URL {
        var components = components(scheme: "https")
        components.path = path
        return components.url!
    }

    /// `wss://host:port/v1/events` (no token: that goes in the upgrade request's header).
    public var eventsURL: URL {
        var components = components(scheme: "wss")
        components.path = "/v1/events"
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
    /// The Hub paired with (trust on first use). `nil`: not paired; the token goes nowhere.
    public var pin: HubPin?

    public init(
        manualHost: String?, port: Int = HubEndpoint.defaultPort, token: String?,
        pin: HubPin? = nil
    ) {
        self.manualHost = manualHost
        self.port = port
        self.token = token
        self.pin = pin
    }
}

public enum SettingsEditError: Error, Sendable, Equatable {
    /// The host or port changed but no new token was entered: the saved token is never sent to a
    /// different Hub without the user typing it again.
    case tokenRequired
}

extension HubSettings {
    /// Whether a token is saved. Settings shows only this, never the token itself.
    public var hasToken: Bool { token != nil }

    /// "Forget Hub / Re-pair": drops the pin and the token, keeps the host and port. The next
    /// connection asks to pair again, and the token must be entered again (it is never kept
    /// for a Hub that is no longer trusted).
    public func forgettingHub() -> HubSettings {
        HubSettings(manualHost: manualHost, port: port, token: nil, pin: nil)
    }

    /// Applies what the user entered in Settings. `host` blank means Bonjour discovery. An empty
    /// `newToken` keeps the saved token, but only if the host and port are unchanged; otherwise
    /// the token must be re-entered (`.tokenRequired`). With no saved token, an empty
    /// `newToken` just leaves it unset. The pin is kept either way: it names the Hub, not its
    /// address (a Hub whose IP changed is still the paired Hub).
    public func applyingEdit(
        host: String, port: Int, newToken: String
    ) -> Result<HubSettings, SettingsEditError> {
        let trimmedHost = host.trimmingCharacters(in: .whitespacesAndNewlines)
        let newHost = trimmedHost.isEmpty ? nil : trimmedHost
        let trimmedToken = newToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedToken.isEmpty {
            return .success(
                HubSettings(manualHost: newHost, port: port, token: trimmedToken, pin: pin))
        }
        if token != nil && (newHost != manualHost || port != self.port) {
            return .failure(.tokenRequired)
        }
        return .success(HubSettings(manualHost: newHost, port: port, token: token, pin: pin))
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

/// Host and port in `UserDefaults`; the token and the Hub pin in `TokenStore`s (two Keychain
/// items in the app), so the pin can't be swapped by editing preferences.
public final class UserDefaultsHubConfigStore: HubConfigStore, @unchecked Sendable {
    // UserDefaults is thread-safe; it is only not annotated Sendable on every SDK.
    private let defaults: UserDefaults
    private let tokens: any TokenStore
    private let pins: any TokenStore

    private enum Key {
        static let host = "hub.manualHost"
        static let port = "hub.port"
    }

    public init(
        defaults: UserDefaults = .standard,
        tokens: any TokenStore = KeychainTokenStore(),
        pins: any TokenStore = KeychainTokenStore(account: KeychainTokenStore.pinAccount)
    ) {
        self.defaults = defaults
        self.tokens = tokens
        self.pins = pins
    }

    public func load() -> HubSettings {
        let port = defaults.object(forKey: Key.port) as? Int ?? HubEndpoint.defaultPort
        return HubSettings(
            manualHost: defaults.string(forKey: Key.host).flatMap(Self.nonBlank),
            port: port,
            token: (try? tokens.readToken()).flatMap { $0 }.flatMap(Self.nonBlank),
            pin: (try? pins.readToken()).flatMap { $0 }.flatMap(Self.decodePin))
    }

    public func save(_ settings: HubSettings) throws {
        if let host = settings.manualHost.flatMap(Self.nonBlank) {
            defaults.set(host, forKey: Key.host)
        } else {
            defaults.removeObject(forKey: Key.host)
        }
        defaults.set(settings.port, forKey: Key.port)
        try tokens.writeToken(settings.token.flatMap(Self.nonBlank))
        try pins.writeToken(try settings.pin.map(Self.encodePin))
    }

    private static func encodePin(_ pin: HubPin) throws -> String {
        String(decoding: try JSONEncoder().encode(pin), as: UTF8.self)
    }

    /// An unreadable pin counts as "not paired" (the person pairs again).
    private static func decodePin(_ json: String) -> HubPin? {
        try? JSONDecoder().decode(HubPin.self, from: Data(json.utf8))
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

/// Stores the token (or, with `account: pinAccount`, the Hub pin as JSON) as a generic
/// password in the Keychain.
public struct KeychainTokenStore: TokenStore {
    public static let pinAccount = "hub-pin"

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
