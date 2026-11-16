import Foundation
import Synchronization
import Testing
@testable import CocoKit

// #96: the home weather location, set from Settings.

// MARK: - Protocol

@Test func locationSettingsDecodeTheOverrideWithAlternatives() throws {
    let value = try Fixtures.decode(LocationSettings.self, "location_settings")
    #expect(value.source == .override)
    #expect(value.location?.label == "Portland, Oregon")
    #expect(value.location?.timezone == "America/Los_Angeles")
    #expect(value.ambiguous)
    #expect(value.alternatives.map(\.label) == ["Portland, Maine", "Portland, United Kingdom"])
}

@Test func locationSettingsDecodeTheConfigFileLocation() throws {
    let config = try Fixtures.decode(LocationSettings.self, "location_settings_config")
    #expect(config.source == .config)
    #expect(config.location?.country == nil)
    let unset = try Fixtures.decode(LocationSettings.self, "location_settings_unset")
    #expect(unset.location == nil)
}

@Test func locationRequestsEncodeExactlyLikeTheFixtures() throws {
    let query = try Fixtures.jsonObject(
        CocoJSON.encoder.encode(LocationQueryRequest(query: "Portland, Oregon")))
    #expect(query == (try Fixtures.jsonObject(Fixtures.data("location_update_query"))))

    let maine = try Fixtures.decode(LocationSettings.self, "location_settings").alternatives[0]
    let place = try Fixtures.jsonObject(CocoJSON.encoder.encode(LocationPlaceRequest(maine)))
    #expect(place == (try Fixtures.jsonObject(Fixtures.data("location_update_place"))))
}

@Test func homeLocationEncodesNullsExplicitly() throws {
    let home = try Fixtures.decode(LocationSettings.self, "location_settings_config")
    let encoded = try Fixtures.jsonObject(CocoJSON.encoder.encode(home))
    #expect(encoded == (try Fixtures.jsonObject(Fixtures.data("location_settings_config"))))
}

// MARK: - HubClient

@Suite struct HomeLocationClientTests {
    let host = "hub-\(UUID().uuidString.lowercased()).test"
    var client: HubClient {
        HubClient(endpoint: HubEndpoint(host: host, port: 8765, token: "tok"), session: StubURLProtocol.session)
    }
    private var lastRequest: URLRequest? { StubURLProtocol.recorded(host: host).last }

    private func serve(_ fixture: String, status: Int = 200) {
        StubURLProtocol.register(host: host) { _ in (status, try Fixtures.data(fixture)) }
    }

    @Test func getsTheLocation() async throws {
        serve("location_settings_config")
        let value = try await client.location()
        #expect(value.source == .config)
        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "GET")
        #expect(sent.url?.path == "/v1/settings/location")
        #expect(sent.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
    }

    @Test func putsAName() async throws {
        serve("location_settings")
        let value = try await client.setLocation(query: "Portland, Oregon")
        #expect(value.ambiguous)
        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "PUT")
        #expect(sent.url?.path == "/v1/settings/location")
        let body = try Fixtures.jsonObject(try #require(sent.httpBody))
        #expect(body == (try Fixtures.jsonObject(Fixtures.data("location_update_query"))))
    }

    @Test func putsAPickedPlace() async throws {
        serve("location_settings")
        let maine = try Fixtures.decode(LocationSettings.self, "location_settings").alternatives[0]
        _ = try await client.setLocation(place: maine)
        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "PUT")
        let body = try Fixtures.jsonObject(try #require(sent.httpBody))
        #expect(body == (try Fixtures.jsonObject(Fixtures.data("location_update_place"))))
    }

    @Test func deletesTheOverride() async throws {
        serve("location_settings_config")
        let value = try await client.resetLocation()
        #expect(value.source == .config)
        #expect(lastRequest?.httpMethod == "DELETE")
        #expect(lastRequest?.url?.path == "/v1/settings/location")
    }

    @Test func nothingFoundIsAServerError() async throws {
        StubURLProtocol.register(host: host) { _ in
            (404, Data(#"{"error": {"code": "location_not_found", "message": "Coco couldn't find \"Narnia\"."}}"#.utf8))
        }
        await #expect(throws: HubError.server(code: "location_not_found", message: "Coco couldn't find \"Narnia\".")) {
            _ = try await client.setLocation(query: "Narnia")
        }
    }
}

// MARK: - View model

/// Answers the location calls from a script and records what was asked.
private final class FakeLocationHub: HubAPI {
    enum Call: Equatable {
        case get, query(String), place(HomeLocation), reset
    }

    let calls = Mutex<[Call]>([])
    let replies: Mutex<[Result<LocationSettings, HubError>]>

    init(_ replies: [Result<LocationSettings, HubError>]) { self.replies = Mutex(replies) }

    private func reply(_ call: Call) throws(HubError) -> LocationSettings {
        calls.withLock { $0.append(call) }
        let next = replies.withLock { $0.isEmpty ? nil : $0.removeFirst() }
        guard let next else { throw .unreachable("no reply scripted") }
        return try next.get()
    }

    func location() async throws(HubError) -> LocationSettings { try reply(.get) }
    func setLocation(query: String) async throws(HubError) -> LocationSettings { try reply(.query(query)) }
    func setLocation(place: HomeLocation) async throws(HubError) -> LocationSettings { try reply(.place(place)) }
    func resetLocation() async throws(HubError) -> LocationSettings { try reply(.reset) }

    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action { throw .unreachable("unused") }
    func idle() async throws(HubError) -> IdleInfo { throw .unreachable("unused") }
    func timers() async throws(HubError) -> TimersList { throw .unreachable("unused") }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
}

@MainActor @Suite struct HomeLocationModelTests {
    let found: LocationSettings
    let config: LocationSettings
    let unset: LocationSettings

    init() throws {
        found = try Fixtures.decode(LocationSettings.self, "location_settings")
        config = try Fixtures.decode(LocationSettings.self, "location_settings_config")
        unset = try Fixtures.decode(LocationSettings.self, "location_settings_unset")
    }

    /// What `GET` returns after a save: the place, no alternatives.
    private func saved(_ place: HomeLocation) -> LocationSettings {
        LocationSettings(source: .override, location: place, ambiguous: false, alternatives: [])
    }

    @Test func summarisesWhereHomeIs() async {
        let model = HomeLocationModel()
        #expect(model.summary == "…")
        await model.load(using: FakeLocationHub([.success(config)]))
        #expect(model.summary == "Home (Hub default)")
        await model.load(using: FakeLocationHub([.success(unset)]))
        #expect(model.summary == "Not set")
        await model.load(using: FakeLocationHub([.success(saved(found.location!))]))
        #expect(model.summary == "Portland, Oregon")
        #expect(model.status == .idle)
    }

    @Test func loadWithoutAHubFails() async {
        let model = HomeLocationModel()
        await model.load(using: nil)
        #expect(model.status == .failed("Not connected to Coco Hub."))
    }

    @Test func findSavesTheMatchAndOffersTheOthers() async throws {
        let hub = FakeLocationHub([.success(found)])
        let model = HomeLocationModel()
        #expect(!model.canFind)
        model.query = "  Portland  "
        #expect(model.canFind)
        await model.find(using: hub)
        #expect(hub.calls.withLock { $0 } == [.query("Portland")])
        #expect(model.summary == "Portland, Oregon")
        #expect(model.ambiguous)
        #expect(model.choices.map(\.label) == ["Portland, Oregon", "Portland, Maine", "Portland, United Kingdom"])
        #expect(model.isCurrent(model.choices[0]))
        #expect(!model.isCurrent(model.choices[1]))
        #expect(model.status == .idle)
    }

    @Test func pickingAnotherMatchSavesItAndKeepsTheList() async throws {
        let maine = found.alternatives[0]
        let hub = FakeLocationHub([.success(found), .success(saved(maine))])
        let model = HomeLocationModel()
        model.query = "Portland"
        await model.find(using: hub)
        await model.pick(maine, using: hub)
        #expect(hub.calls.withLock { $0 }.last == .place(maine))
        #expect(model.summary == "Portland, Maine")
        #expect(model.isCurrent(maine))
        #expect(model.choices.count == 3)
    }

    @Test func nothingFoundShowsTheHubsMessageAndKeepsTheOldPlace() async {
        let message = "Coco couldn't find \"Narnia\". Try adding the state or country."
        let hub = FakeLocationHub([
            .success(config), .failure(.server(code: "location_not_found", message: message)),
        ])
        let model = HomeLocationModel()
        await model.load(using: hub)
        model.query = "Narnia"
        await model.find(using: hub)
        #expect(model.status == .failed(message))
        #expect(model.summary == "Home (Hub default)")
        #expect(model.choices.isEmpty)
    }

    @Test func otherErrorsAreFormatted() async {
        let model = HomeLocationModel()
        model.query = "Portland"
        await model.find(using: FakeLocationHub([.failure(.unauthorized)]))
        #expect(model.status == .failed("Unauthorized (check the token)"))
    }

    @Test func useTheHubsDefault() async {
        let hub = FakeLocationHub([.success(found), .success(config)])
        let model = HomeLocationModel()
        model.query = "Portland"
        await model.find(using: hub)
        await model.useHubDefault(using: hub)
        #expect(hub.calls.withLock { $0 }.last == .reset)
        #expect(model.summary == "Home (Hub default)")
        #expect(model.choices.isEmpty)
        #expect(!model.ambiguous)
        #expect(model.query.isEmpty)
        #expect(!model.canUseHubDefault)
    }

    @Test func blankQueryDoesNothing() async {
        let hub = FakeLocationHub([])
        let model = HomeLocationModel()
        model.query = "   "
        await model.find(using: hub)
        #expect(hub.calls.withLock { $0 }.isEmpty)
    }
}
