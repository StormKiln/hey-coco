import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Serves canned responses to a `URLSession`. Handlers are keyed by host so tests can run in
/// parallel: each test gets its own unique host.
final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) throws -> (Int, Data)

    private static let handlers = Mutex<[String: Handler]>([:])
    private static let requests = Mutex<[String: [URLRequest]]>([:])

    static func register(host: String, handler: @escaping Handler) {
        handlers.withLock { $0[host] = handler }
    }

    static func recorded(host: String) -> [URLRequest] {
        requests.withLock { $0[host] ?? [] }
    }

    static var session: URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let host = request.url?.host ?? ""
        var request = self.request
        // URLSession moves bodies into a stream before protocols see them.
        if request.httpBody == nil, let stream = request.httpBodyStream {
            request.httpBody = Data(reading: stream)
        }
        Self.requests.withLock { $0[host, default: []].append(request) }
        guard let handler = Self.handlers.withLock({ $0[host] }) else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotFindHost))
            return
        }
        do {
            let (status, data) = try handler(request)
            let response = HTTPURLResponse(
                url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

extension Data {
    init(reading stream: InputStream) {
        self.init()
        stream.open()
        defer { stream.close() }
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            append(buffer, count: count)
        }
    }
}

@Suite struct HubClientTests {
    let host = "hub-\(UUID().uuidString.lowercased()).test"
    var endpoint: HubEndpoint { HubEndpoint(host: host, port: 8765, token: "tok") }
    var client: HubClient { HubClient(endpoint: endpoint, session: StubURLProtocol.session) }

    private func serve(_ fixture: String, status: Int = 200) {
        StubURLProtocol.register(host: host) { _ in (status, try Fixtures.data(fixture)) }
    }

    private var lastRequest: URLRequest? { StubURLProtocol.recorded(host: host).last }

    @Test func postUtteranceSendsBearerAndBodyAndDecodesAction() async throws {
        serve("action_timers")
        let request = try Fixtures.decode(UtteranceRequest.self, "utterance_request")
        let action = try await client.postUtterance(request)
        #expect(action == (try Fixtures.decode(Action.self, "action_timers")))

        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "POST")
        #expect(sent.url?.path == "/v1/utterance")
        #expect(sent.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
        #expect(sent.value(forHTTPHeaderField: "Content-Type") == "application/json")
        let body = try CocoJSON.decoder.decode(UtteranceRequest.self, from: try #require(sent.httpBody))
        #expect(body == request)
    }

    @Test func idleDecodesIdleInfo() async throws {
        serve("idle_info")
        let idle = try await client.idle()
        #expect(idle == (try Fixtures.decode(IdleInfo.self, "idle_info")))
        #expect(lastRequest?.httpMethod == "GET")
        #expect(lastRequest?.url?.path == "/v1/idle")
    }

    @Test func timersDecodesTimersList() async throws {
        serve("timers_list")
        let list = try await client.timers()
        #expect(list == (try Fixtures.decode(TimersList.self, "timers_list")))
        #expect(lastRequest?.url?.path == "/v1/timers")
    }

    @Test func createTimerPostsRequest() async throws {
        serve("timer_running")
        let timer = try await client.createTimer(TimerCreateRequest(name: nil, durationS: 60))
        #expect(timer == (try Fixtures.decode(TimerInfo.self, "timer_running")))
        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "POST")
        #expect(sent.url?.path == "/v1/timers")
        let body = try Fixtures.jsonObject(try #require(sent.httpBody))
        #expect(body == (try Fixtures.jsonObject(Fixtures.data("timer_create_request_unnamed"))))
    }

    @Test func dismissTimerPostsToDismiss() async throws {
        serve("timer_dismissed")
        let timer = try await client.dismissTimer(id: "t_7f3a")
        #expect(timer.state == .dismissed)
        #expect(lastRequest?.httpMethod == "POST")
        #expect(lastRequest?.url?.path == "/v1/timers/t_7f3a/dismiss")
    }

    @Test func diagnosticsDecodesFixtureAndSendsBearer() async throws {
        serve("diagnostics")
        let diagnostics = try await client.diagnostics()
        #expect(diagnostics == (try Fixtures.decode(HubDiagnostics.self, "diagnostics")))
        #expect(diagnostics.utterances.count == 4)
        let sent = try #require(lastRequest)
        #expect(sent.httpMethod == "GET")
        #expect(sent.url?.path == "/v1/diagnostics")
        #expect(sent.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
    }

    @Test func cancelTimerDeletes() async throws {
        serve("timer_cancelled")
        let timer = try await client.cancelTimer(id: "t_7f3a")
        #expect(timer.state == .cancelled)
        #expect(lastRequest?.httpMethod == "DELETE")
        #expect(lastRequest?.url?.path == "/v1/timers/t_7f3a")
    }

    @Test func errorBodyMapsToServerError() async throws {
        serve("error", status: 404)
        await #expect(throws: HubError.server(code: "timer_not_found", message: "No timer with id t_0000.")) {
            _ = try await client.dismissTimer(id: "t_0000")
        }
    }

    @Test(arguments: [401, 403])
    func authFailuresMapToUnauthorized(status: Int) async throws {
        StubURLProtocol.register(host: host) { _ in
            (status, Data(#"{"error": {"code": "unauthorized", "message": "Bad token."}}"#.utf8))
        }
        await #expect(throws: HubError.unauthorized) { _ = try await client.idle() }
    }

    @Test func nonJSONErrorBodyStillMapsToServerError() async throws {
        StubURLProtocol.register(host: host) { _ in (500, Data("Internal Server Error".utf8)) }
        await #expect(throws: HubError.server(code: "http_500", message: "Internal Server Error")) {
            _ = try await client.idle()
        }
    }

    @Test func connectionFailureMapsToUnreachable() async throws {
        StubURLProtocol.register(host: host) { _ in throw URLError(.cannotConnectToHost) }
        do throws(HubError) {
            _ = try await client.idle()
            Issue.record("expected an error")
        } catch {
            guard case .unreachable = error else {
                Issue.record("expected unreachable, got \(error)")
                return
            }
        }
    }

    @Test func badPayloadMapsToDecoding() async throws {
        StubURLProtocol.register(host: host) { _ in (200, Data(#"{"nope": 1}"#.utf8)) }
        do throws(HubError) {
            _ = try await client.idle()
            Issue.record("expected an error")
        } catch {
            guard case .decoding = error else {
                Issue.record("expected decoding, got \(error)")
                return
            }
        }
    }
}
