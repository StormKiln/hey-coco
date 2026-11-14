import Foundation
import Testing
@testable import CocoKit

/// Decodes every fixture in `docs/protocol/fixtures` (the Hub ↔ Display contract).
enum Fixtures {
    static let directory = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // CocoKitTests
        .deletingLastPathComponent()  // Tests
        .deletingLastPathComponent()  // CocoKit
        .deletingLastPathComponent()  // display
        .deletingLastPathComponent()  // repo root
        .appendingPathComponent("docs/protocol/fixtures")

    static var names: [String] {
        let files = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return files.filter { $0.hasSuffix(".json") }.map { String($0.dropLast(5)) }.sorted()
    }

    static func data(_ name: String) throws -> Data {
        try Data(contentsOf: directory.appendingPathComponent("\(name).json"))
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try CocoJSON.decoder.decode(type, from: data(name))
    }

    static func jsonObject(_ data: Data) throws -> NSObject {
        try JSONSerialization.jsonObject(with: data) as! NSObject
    }
}

/// Decodes `name` with the model its filename prefix maps to, then checks encode → decode is stable.
private func checkRoundTrip(_ name: String) throws -> Bool {
    func roundTrip<T: Codable & Equatable>(_ type: T.Type) throws -> Bool {
        let value = try Fixtures.decode(type, name)
        let again = try CocoJSON.decoder.decode(type, from: CocoJSON.encoder.encode(value))
        #expect(again == value, "\(name) did not round-trip")
        return true
    }
    let table: [(String, () throws -> Bool)] = [
        ("utterance_request", { try roundTrip(UtteranceRequest.self) }),
        ("action_", { try roundTrip(Action.self) }),
        ("idle_info", { try roundTrip(IdleInfo.self) }),
        ("timers_list", { try roundTrip(TimersList.self) }),
        ("timer_create_request", { try roundTrip(TimerCreateRequest.self) }),
        ("timer_", { try roundTrip(TimerInfo.self) }),
        ("event_", { try roundTrip(Event.self) }),
        ("error", { try roundTrip(ErrorResponse.self) }),
        ("diagnostics", { try roundTrip(HubDiagnostics.self) }),
    ]
    guard let match = table.filter({ name.hasPrefix($0.0) }).max(by: { $0.0.count < $1.0.count })
    else { return false }
    return try match.1()
}

@Test func fixturesArePresent() {
    #expect(Fixtures.names.count >= 30)
}

@Test(arguments: Fixtures.names)
func everyFixtureDecodesWithItsModel(name: String) throws {
    #expect(try checkRoundTrip(name), "no model mapped for fixture \(name)")
}

@Test func utteranceRequestEncodesExactlyLikeTheFixture() throws {
    for name in ["utterance_request", "utterance_request_touch_media"] {
        let value = try Fixtures.decode(UtteranceRequest.self, name)
        let encoded = try Fixtures.jsonObject(CocoJSON.encoder.encode(value))
        #expect(encoded == (try Fixtures.jsonObject(Fixtures.data(name))), "\(name)")
    }
}

@Test func timerCreateRequestEncodesNullName() throws {
    let value = TimerCreateRequest(name: nil, durationS: 60)
    let encoded = try Fixtures.jsonObject(CocoJSON.encoder.encode(value))
    #expect(encoded == (try Fixtures.jsonObject(Fixtures.data("timer_create_request_unnamed"))))
}

@Test func weatherActionDecodes() throws {
    let action = try Fixtures.decode(Action.self, "action_weather")
    #expect(action.intent == "weather.current")
    #expect(action.matchedBy == .rules)
    guard case .weather(let weather) = action.view else {
        Issue.record("expected weather view, got \(String(describing: action.view))")
        return
    }
    #expect(weather.current.unit == .celsius)
    #expect(weather.current.temp == 4.5)
    #expect(weather.hourly.count == 6)
    #expect(weather.hourly[2].precipProbability == 55)
    #expect(weather.daily[0].date == "2026-11-14")
    #expect(weather.current.time == Date(timeIntervalSince1970: 1_794_668_400))
}

@Test func commandsDecodeInOrder() throws {
    let action = try Fixtures.decode(Action.self, "action_commands")
    #expect(action.say == nil)
    #expect(action.view == nil)
    #expect(action.commands == [
        .alarmStop,
        .mediaPause(target: .music),
        .volumeSet(level: 40),
        .volumeChange(delta: -10),
        .screenWake,
        .screenSleep,
    ])
}

@Test func timerFiredEventDecodes() throws {
    let event = try Fixtures.decode(Event.self, "event_timer_fired")
    #expect(event.serverTime == Date(timeIntervalSince1970: 1_794_669_000))
    guard case .timerFired(let timer) = event.kind else {
        Issue.record("expected timer.fired, got \(event.kind)")
        return
    }
    #expect(timer.id == "t_7f3a")
    #expect(timer.name == "pasta")
    #expect(timer.durationS == 600)
    #expect(timer.state == .ringing)
}

@Test func nullNowPlayingDecodes() throws {
    let event = try Fixtures.decode(Event.self, "event_now_playing_null")
    #expect(event.kind == .nowPlaying(nil))
}

@Test func idleInfoWithoutWeatherDecodes() throws {
    let idle = try Fixtures.decode(IdleInfo.self, "idle_info_no_weather")
    #expect(idle.weather == nil)
    #expect(idle.night == Night(start: "21:30", end: "06:30", darkAfterIdleS: 300))
}

@Test func unknownTypesDecodeAsUnknown() throws {
    let json = """
        {"say": null, "view": {"type": "hologram", "depth": 3},
         "commands": [{"type": "lights.dim", "level": 10}, {"type": "screen.wake"}],
         "intent": "none", "matched_by": "none"}
        """
    let action = try CocoJSON.decoder.decode(Action.self, from: Data(json.utf8))
    #expect(action.view == .unknown("hologram"))
    #expect(action.commands == [.unknown("lights.dim"), .screenWake])

    let event = try CocoJSON.decoder.decode(
        Event.self, from: Data(#"{"type": "doorbell.rang", "server_time": "2026-11-14T15:04:05Z"}"#.utf8))
    #expect(event.kind == .unknown("doorbell.rang"))
}

@Test func errorDecodes() throws {
    let error = try Fixtures.decode(ErrorResponse.self, "error")
    #expect(error.error.code == "timer_not_found")
}

@Test func diagnosticsDecodes() throws {
    let diag = try Fixtures.decode(HubDiagnostics.self, "diagnostics")
    #expect(diag.gitSha == "3e190cd")
    #expect(diag.settings.qwen.model == "qwen3:8b")
    #expect(diag.health.spotify.status == .error)
    #expect(diag.health.weather.fetchedAt != nil)
    #expect(diag.utterances.count == 4)
    #expect(diag.utterances[0].matchedBy == .qwen)
    #expect(diag.utterances[2].intent == nil)
    #expect(diag.utterances[2].error == "SpotifyError: HTTP 502")

    let unknown = try CocoJSON.decoder.decode([CheckStatus].self, from: Data(#"["ok", "melting"]"#.utf8))
    #expect(unknown == [.ok, .unknown])
}
