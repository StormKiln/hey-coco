import Foundation
import Testing
@testable import CocoKit

@Suite struct HubEndpointTests {
    @Test func baseURLUsesHTTPSHostAndPort() {
        let endpoint = HubEndpoint(host: "192.168.1.20", port: 8765, token: "secret")
        #expect(endpoint.baseURL.absoluteString == "https://192.168.1.20:8765")
        #expect(endpoint.url(for: "/v1/idle").absoluteString == "https://192.168.1.20:8765/v1/idle")
    }

    @Test func eventsURLIsWebSocketWithoutToken() {
        let endpoint = HubEndpoint(host: "coco.local", port: 8765, token: "a b&c")
        #expect(endpoint.eventsURL.absoluteString == "wss://coco.local:8765/v1/events")
        #expect(endpoint.eventsURL.query == nil)
    }

    @Test func ipv6HostsAreBracketed() {
        let endpoint = HubEndpoint(host: "fe80::1", port: 8765, token: "t")
        #expect(endpoint.baseURL.absoluteString == "https://[fe80::1]:8765")
    }
}

@Suite struct HubSettingsEditTests {
    let saved = HubSettings(manualHost: "10.0.0.5", port: 8765, token: "old")

    @Test func emptyTokenWithSameHostAndPortKeepsSavedToken() {
        #expect(saved.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "") == .success(saved))
        #expect(saved.applyingEdit(host: " 10.0.0.5 ", port: 8765, newToken: "  ") == .success(saved))
    }

    @Test func newTokenReplacesSavedToken() {
        let expected = HubSettings(manualHost: "10.0.0.5", port: 8765, token: "new")
        #expect(saved.applyingEdit(host: "10.0.0.5", port: 8765, newToken: " new ") == .success(expected))
    }

    @Test func changedHostWithEmptyTokenRequiresToken() {
        #expect(saved.applyingEdit(host: "10.0.0.6", port: 8765, newToken: "") == .failure(.tokenRequired))
        #expect(saved.applyingEdit(host: "", port: 8765, newToken: "") == .failure(.tokenRequired))
    }

    @Test func changedPortWithEmptyTokenRequiresToken() {
        #expect(saved.applyingEdit(host: "10.0.0.5", port: 9000, newToken: "") == .failure(.tokenRequired))
    }

    @Test func changedHostWithNewTokenSucceeds() {
        let expected = HubSettings(manualHost: "10.0.0.6", port: 9000, token: "new")
        #expect(saved.applyingEdit(host: "10.0.0.6", port: 9000, newToken: "new") == .success(expected))
    }

    @Test func blankHostMeansDiscovery() {
        let expected = HubSettings(manualHost: nil, port: 8765, token: "new")
        #expect(saved.applyingEdit(host: "   ", port: 8765, newToken: "new") == .success(expected))
    }

    @Test func noSavedTokenAndEmptyTokenStaysWithoutToken() {
        let unset = HubSettings(manualHost: nil, port: 8765, token: nil)
        let expected = HubSettings(manualHost: "10.0.0.5", port: 8765, token: nil)
        #expect(unset.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "") == .success(expected))
    }

    @Test func savedTokenIsReported() {
        #expect(saved.hasToken)
        #expect(!HubSettings(manualHost: nil, token: nil).hasToken)
    }
}

@Suite struct HubConfigStoreTests {
    private func makeStore() -> (UserDefaultsHubConfigStore, InMemoryTokenStore, UserDefaults) {
        let defaults = UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!
        let tokens = InMemoryTokenStore()
        return (
            UserDefaultsHubConfigStore(defaults: defaults, tokens: tokens, pins: InMemoryTokenStore()),
            tokens, defaults)
    }

    @Test func emptyStoreHasNoOverrideAndDefaultPort() {
        let (store, _, _) = makeStore()
        #expect(store.load() == HubSettings(manualHost: nil, port: HubEndpoint.defaultPort, token: nil))
    }

    @Test func savedSettingsRoundTripAndTokenStaysOutOfUserDefaults() throws {
        let (store, tokens, defaults) = makeStore()
        let settings = HubSettings(manualHost: "10.0.0.5", port: 9000, token: "s3cret")
        try store.save(settings)
        #expect(store.load() == settings)
        #expect(try tokens.readToken() == "s3cret")
        let everything = defaults.dictionaryRepresentation().values.map { "\($0)" }
        #expect(!everything.contains { $0.contains("s3cret") })
    }

    @Test func blankHostAndTokenAreStoredAsNil() throws {
        let (store, tokens, _) = makeStore()
        try store.save(HubSettings(manualHost: "1.2.3.4", port: 8765, token: "x"))
        try store.save(HubSettings(manualHost: "  ", port: 8765, token: ""))
        #expect(store.load() == HubSettings(manualHost: nil, port: 8765, token: nil))
        #expect(try tokens.readToken() == nil)
    }
}

@Suite struct HubPinSettingsTests {
    let paired = HubSettings(manualHost: "10.0.0.5", port: 8765, token: "old", pin: .test)

    @Test func editsKeepThePin() throws {
        #expect(try paired.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "").get().pin == .test)
        #expect(try paired.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "new").get().pin == .test)
        // The pin names the Hub, not its address: a new IP is still the paired Hub.
        #expect(try paired.applyingEdit(host: "10.0.0.9", port: 8765, newToken: "new").get().pin == .test)
    }

    /// #91: the token only ever goes to the pinned certificate, so a new address for the paired
    /// Hub can't leak it. Re-entering it would only get in the way.
    @Test func changingTheHostOrPortWhilePinnedKeepsTheToken() {
        #expect(
            paired.applyingEdit(host: "10.0.0.9", port: 8765, newToken: "")
                == .success(HubSettings(manualHost: "10.0.0.9", port: 8765, token: "old", pin: .test)))
        #expect(
            paired.applyingEdit(host: "10.0.0.5", port: 9000, newToken: "")
                == .success(HubSettings(manualHost: "10.0.0.5", port: 9000, token: "old", pin: .test)))
        #expect(
            paired.applyingEdit(host: "", port: 8765, newToken: "")
                == .success(HubSettings(manualHost: nil, port: 8765, token: "old", pin: .test)))
    }

    @Test func editsKeepTheLastKnownAddress() throws {
        var settings = paired
        settings.lastKnownAddress = HubAddress(host: "192.168.4.32", port: 8765)
        #expect(
            try settings.applyingEdit(host: "10.0.0.9", port: 8765, newToken: "").get().lastKnownAddress
                == HubAddress(host: "192.168.4.32", port: 8765))
        #expect(
            try settings.applyingEdit(host: "", port: 8765, newToken: "new").get().lastKnownAddress
                == HubAddress(host: "192.168.4.32", port: 8765))
    }

    @Test func forgettingTheHubDropsPinAndTokenButKeepsTheAddress() {
        let forgotten = paired.forgettingHub()
        #expect(forgotten == HubSettings(manualHost: "10.0.0.5", port: 8765, token: nil, pin: nil))
        #expect(!forgotten.hasToken)
    }

    @Test func forgettingTheHubDropsTheLastKnownAddress() {
        var settings = paired
        settings.lastKnownAddress = HubAddress(host: "192.168.4.32", port: 8765)
        #expect(settings.forgettingHub().lastKnownAddress == nil)
    }

    @Test func afterForgettingAnEmptyTokenLeavesItUnset() {
        let forgotten = paired.forgettingHub()
        #expect(forgotten.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "") == .success(forgotten))
        #expect(
            forgotten.applyingEdit(host: "10.0.0.5", port: 8765, newToken: "t2")
                == .success(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t2", pin: nil)))
    }

    @Test func pinRoundTripsThroughItsOwnStoreAndStaysOutOfUserDefaults() throws {
        let defaults = UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!
        let pins = InMemoryTokenStore()
        let store = UserDefaultsHubConfigStore(defaults: defaults, tokens: InMemoryTokenStore(), pins: pins)
        try store.save(paired)
        #expect(store.load() == paired)
        #expect(try pins.readToken() != nil)
        let hex = HubPin.test.certSHA256.base64EncodedString()
        let everything = defaults.dictionaryRepresentation().values.map { "\($0)" }
        #expect(!everything.contains { $0.contains(hex) || $0.contains(HubPin.test.hubID) })
        try store.save(paired.forgettingHub())
        #expect(try pins.readToken() == nil)
        #expect(store.load().pin == nil)
    }

    @Test func lastKnownAddressRoundTripsWithoutItsAdvertisedID() throws {
        let store = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        var settings = paired
        settings.lastKnownAddress = HubAddress(host: "192.168.4.32", port: 8766, advertisedHubID: "x")
        try store.save(settings)
        #expect(store.load().lastKnownAddress == HubAddress(host: "192.168.4.32", port: 8766))
        try store.save(settings.forgettingHub())
        #expect(store.load().lastKnownAddress == nil)
    }

    @Test func settingsSavedBeforeLastKnownAddressExistedLoadWithoutIt() {
        let defaults = UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!
        // What a build before #91 wrote: host and port only.
        defaults.set("10.0.0.5", forKey: "hub.manualHost")
        defaults.set(8765, forKey: "hub.port")
        let store = UserDefaultsHubConfigStore(
            defaults: defaults, tokens: InMemoryTokenStore(token: "t"), pins: InMemoryTokenStore())
        #expect(store.load() == HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t"))
        defaults.set("{not json", forKey: "hub.lastKnownAddress")
        #expect(store.load().lastKnownAddress == nil)
    }

    @Test func savingOnlyTheLastKnownAddressNeverRewritesTheToken() throws {
        let tokens = InMemoryTokenStore()
        let pins = InMemoryTokenStore()
        let store = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: tokens, pins: pins)
        try store.save(paired)
        // As if the Keychain can't be read right now (before first unlock): load() would see no
        // token, and a load-modify-save would delete it.
        try tokens.writeToken(nil)
        try store.saveLastKnownAddress(HubAddress(host: "192.168.4.32", port: 8765))
        try tokens.writeToken("old")
        var expected = paired
        expected.lastKnownAddress = HubAddress(host: "192.168.4.32", port: 8765)
        #expect(store.load() == expected)
    }

    @Test func unreadablePinMeansNotPaired() throws {
        let pins = InMemoryTokenStore(token: "{not json")
        let store = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: pins)
        #expect(store.load().pin == nil)
    }
}

/// Yields `results` and then finishes.
actor FakeDiscovery: HubDiscovery {
    let results: [HubAddress]
    private(set) var browseCount = 0

    init(results: [HubAddress]) { self.results = results }

    nonisolated func browse() -> AsyncStream<HubAddress> {
        let results = results
        Task { await self.recordBrowse() }
        return AsyncStream { continuation in
            for result in results { continuation.yield(result) }
            continuation.finish()
        }
    }

    private func recordBrowse() { browseCount += 1 }
}
