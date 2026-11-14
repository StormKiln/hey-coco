import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Serves `diagnostics()` from a fixed result and counts calls.
private final class FakeDiagnosticsHub: HubAPI {
    let result: Result<HubDiagnostics, HubError>
    let calls = Mutex(0)

    init(_ result: Result<HubDiagnostics, HubError>) { self.result = result }

    func diagnostics() async throws(HubError) -> HubDiagnostics {
        calls.withLock { $0 += 1 }
        return try result.get()
    }
    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action { throw .unreachable("unused") }
    func idle() async throws(HubError) -> IdleInfo { throw .unreachable("unused") }
    func timers() async throws(HubError) -> TimersList { throw .unreachable("unused") }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo { throw .unreachable("unused") }
}

@Suite struct DiagnosticsFormatTests {
    @Test(arguments: [
        (0, "0 s"), (42, "42 s"), (60, "1 m"), (305, "5 m"), (3600, "1 h 0 m"),
        (43_500, "12 h 5 m"), (86_400, "1 d 0 h"), (273_600, "3 d 4 h"),
    ])
    func uptime(_ seconds: Int, _ expected: String) {
        #expect(DiagnosticsFormat.uptime(seconds: seconds) == expected)
    }

    @Test(arguments: [
        (2.0, "just now"), (9, "9 s ago"), (100, "1 min ago"), (2700, "45 min ago"),
        (7200, "2 h ago"), (180_000, "2 d ago"), (-30, "just now"),
    ])
    func relative(_ secondsAgo: Double, _ expected: String) {
        let now = Date(timeIntervalSince1970: 1_000_000)
        #expect(DiagnosticsFormat.relative(now.addingTimeInterval(-secondsAgo), now: now) == expected)
    }

    @Test func latency() {
        #expect(DiagnosticsFormat.latency(ms: 2) == "2 ms")
        #expect(DiagnosticsFormat.latency(ms: 312) == "312 ms")
        #expect(DiagnosticsFormat.latency(ms: 1840) == "1.8 s")
    }

    @Test func connectionAndErrors() {
        #expect(DiagnosticsFormat.connection(.connected) == "Connected")
        #expect(DiagnosticsFormat.connection(.unreachable(reason: "Timed out.")) == "Unreachable: Timed out.")
        #expect(DiagnosticsFormat.connection(.unauthorized) == "Token rejected")
        #expect(DiagnosticsFormat.error(.server(code: "bad", message: "Nope")) == "bad: Nope")
        #expect(DiagnosticsFormat.error(.unauthorized) == "Unauthorized (check the token)")
    }

    @MainActor @Test func wakeWord() {
        #expect(DiagnosticsFormat.wakeWord(nil) == "Not started")
        #expect(DiagnosticsFormat.wakeWord(.armed) == "Armed (listening for “hey coco”)")
        #expect(DiagnosticsFormat.wakeWord(.unavailable(reason: "No key")) == "Unavailable: No key")
        #expect(DiagnosticsFormat.wakeWord(.retrying(after: .seconds(4))) == "Retrying in 4 s")
    }

    @Test func hubAddressNeverIncludesTheToken() {
        let endpoint = HubEndpoint(host: "coco.local", port: 8765, token: "s3cret")
        #expect(DiagnosticsFormat.address(endpoint) == "coco.local:8765")
        #expect(DiagnosticsFormat.address(nil) == "Not found yet")
    }
}

@MainActor
@Suite struct DiagnosticsModelTests {
    private func fixture() throws -> HubDiagnostics {
        try Fixtures.decode(HubDiagnostics.self, "diagnostics")
    }

    @Test func loadsAndFormatsTheFixture() async throws {
        let hub = FakeDiagnosticsHub(.success(try fixture()))
        let model = DiagnosticsModel()
        #expect(model.state == .idle)
        await model.load(using: hub)
        #expect(model.diagnostics == (try fixture()))
        #expect(model.uptime == "12 h 5 m")
        #expect(model.versionLine == "0.1.0 (3e190cd)")

        #expect(model.healthRows.map(\.name) == ["Ollama", "Weather", "YouTube", "Spotify"])
        #expect(model.healthRows.map(\.status) == [.ok, .warn, .ok, .error])
        #expect(model.healthRows[0].summary == "reachable · model loaded")
        #expect(model.healthRows[1].summary == "last fetch failed · stale · fetched 45 min ago")
        #expect(model.healthRows[2].summary == "quota 300 / 9000")
        #expect(model.healthRows[3].summary == "Kitchen iPad not visible · timed out")

        let rows = model.utteranceRows
        #expect(rows.count == 4)
        // Relative to the Hub's server_time, so the iPad's clock skew doesn't matter.
        #expect(rows[0].when == "9 s ago")
        #expect(rows[0].text == "why is the sky blue")
        #expect(rows[0].intent == "general.answer")
        #expect(rows[0].matchedBy == "qwen")
        #expect(rows[0].latency == "1.8 s")
        #expect(rows[0].error == nil)
        #expect(rows[2].intent == "—")
        #expect(rows[2].matchedBy == "—")
        #expect(rows[2].error == "SpotifyError: HTTP 502")
        #expect(rows[3].when == "5 min ago")
    }

    @Test func failedLoadKeepsTheLastGoodDataAndReportsTheError() async throws {
        let model = DiagnosticsModel()
        await model.load(using: FakeDiagnosticsHub(.success(try fixture())))
        await model.load(using: FakeDiagnosticsHub(.failure(.unreachable("Could not connect to Coco Hub."))))
        #expect(model.state == .failed("Could not connect to Coco Hub."))
        #expect(model.diagnostics != nil)
    }

    @Test func noHubMeansNotConnected() async {
        let model = DiagnosticsModel()
        await model.load(using: nil)
        #expect(model.state == .failed("Not connected to Coco Hub."))
        #expect(model.healthRows.isEmpty)
        #expect(model.utteranceRows.isEmpty)
    }
}
