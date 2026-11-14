import Foundation
import Testing
@testable import CocoKit

@Suite struct HubEndpointTests {
    @Test func baseURLUsesHTTPHostAndPort() {
        let endpoint = HubEndpoint(host: "192.168.1.20", port: 8765, token: "secret")
        #expect(endpoint.baseURL.absoluteString == "http://192.168.1.20:8765")
        #expect(endpoint.url(for: "/v1/idle").absoluteString == "http://192.168.1.20:8765/v1/idle")
    }

    @Test func eventsURLIsWebSocketWithEncodedToken() {
        let endpoint = HubEndpoint(host: "coco.local", port: 8765, token: "a b&c")
        #expect(endpoint.eventsURL.absoluteString == "ws://coco.local:8765/v1/events?token=a%20b%26c")
    }

    @Test func ipv6HostsAreBracketed() {
        let endpoint = HubEndpoint(host: "fe80::1", port: 8765, token: "t")
        #expect(endpoint.baseURL.absoluteString == "http://[fe80::1]:8765")
    }
}

@Suite struct HubConfigStoreTests {
    private func makeStore() -> (UserDefaultsHubConfigStore, InMemoryTokenStore, UserDefaults) {
        let defaults = UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!
        let tokens = InMemoryTokenStore()
        return (UserDefaultsHubConfigStore(defaults: defaults, tokens: tokens), tokens, defaults)
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

@Suite struct EndpointResolutionTests {
    @Test func manualHostWinsOverDiscovery() async {
        let discovery = FakeDiscovery(results: [HubAddress(host: "discovered.local", port: 1111)])
        let settings = HubSettings(manualHost: "10.0.0.5", port: 9000, token: "t")
        let endpoint = await HubEndpoint.resolve(settings: settings, discovery: discovery)
        #expect(endpoint == HubEndpoint(host: "10.0.0.5", port: 9000, token: "t"))
        #expect(await discovery.browseCount == 0)
    }

    @Test func discoveryIsUsedWithoutManualHost() async {
        let discovery = FakeDiscovery(results: [HubAddress(host: "mac.local", port: 8765)])
        let settings = HubSettings(manualHost: nil, port: 9000, token: "t")
        let endpoint = await HubEndpoint.resolve(settings: settings, discovery: discovery)
        #expect(endpoint == HubEndpoint(host: "mac.local", port: 8765, token: "t"))
    }

    @Test func missingTokenResolvesWithEmptyToken() async {
        let discovery = FakeDiscovery(results: [])
        let settings = HubSettings(manualHost: "h", port: 1, token: nil)
        let endpoint = await HubEndpoint.resolve(settings: settings, discovery: discovery)
        #expect(endpoint?.token == "")
    }

    @Test func discoveryEndingWithoutResultResolvesNil() async {
        let discovery = FakeDiscovery(results: [])
        let settings = HubSettings(manualHost: nil, port: 8765, token: "t")
        #expect(await HubEndpoint.resolve(settings: settings, discovery: discovery) == nil)
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
