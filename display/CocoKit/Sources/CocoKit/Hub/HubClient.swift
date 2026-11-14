import Foundation

/// Everything that can go wrong talking to the Hub.
public enum HubError: Error, Sendable, Equatable {
    /// 401/403: the token is missing or wrong.
    case unauthorized
    /// No HTTP response (connection refused, timed out, no route, …).
    case unreachable(String)
    /// Non-2xx with the protocol's error body. Bodies that are not the error JSON become
    /// `code: "http_<status>"` with the body text as the message.
    case server(code: String, message: String)
    /// A 2xx whose body does not match the protocol model.
    case decoding(String)
}

/// The Hub REST API (`docs/protocol/README.md`). A protocol so `AppModel` can be tested with fakes.
public protocol HubAPI: Sendable {
    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action
    func idle() async throws(HubError) -> IdleInfo
    func timers() async throws(HubError) -> TimersList
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo
    /// `GET /v1/diagnostics`, for the hidden diagnostics screen (#45).
    func diagnostics() async throws(HubError) -> HubDiagnostics
}

extension HubAPI {
    /// Fakes that never open the diagnostics screen need not implement it.
    public func diagnostics() async throws(HubError) -> HubDiagnostics {
        throw .server(code: "unsupported", message: "Diagnostics are not available.")
    }
}

/// `HubAPI` over `URLSession`.
public struct HubClient: HubAPI {
    public let endpoint: HubEndpoint
    public let session: URLSession
    /// Most calls are local and quick; an utterance may wait on the LLM.
    public var timeout: TimeInterval = 10
    public var utteranceTimeout: TimeInterval = 30

    public init(endpoint: HubEndpoint, session: URLSession = .shared) {
        self.endpoint = endpoint
        self.session = session
    }

    public func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        try await send("POST", "/v1/utterance", body: request, timeout: utteranceTimeout)
    }

    public func idle() async throws(HubError) -> IdleInfo {
        try await send("GET", "/v1/idle")
    }

    public func timers() async throws(HubError) -> TimersList {
        try await send("GET", "/v1/timers")
    }

    public func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo {
        try await send("POST", "/v1/timers", body: request)
    }

    public func dismissTimer(id: String) async throws(HubError) -> TimerInfo {
        try await send("POST", "/v1/timers/\(id)/dismiss")
    }

    public func cancelTimer(id: String) async throws(HubError) -> TimerInfo {
        try await send("DELETE", "/v1/timers/\(id)")
    }

    public func diagnostics() async throws(HubError) -> HubDiagnostics {
        try await send("GET", "/v1/diagnostics")
    }

    // MARK: - Plumbing

    private struct NoBody: Encodable {}

    private func send<Response: Decodable>(
        _ method: String, _ path: String, timeout: TimeInterval? = nil
    ) async throws(HubError) -> Response {
        try await send(method, path, body: Optional<NoBody>.none, timeout: timeout)
    }

    private func send<Body: Encodable, Response: Decodable>(
        _ method: String, _ path: String, body: Body?, timeout: TimeInterval? = nil
    ) async throws(HubError) -> Response {
        var request = URLRequest(url: endpoint.url(for: path))
        request.httpMethod = method
        request.timeoutInterval = timeout ?? self.timeout
        request.setValue("Bearer \(endpoint.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            do {
                request.httpBody = try CocoJSON.encoder.encode(body)
            } catch {
                throw .decoding("Could not encode request: \(error)")
            }
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw .unreachable(Self.describe(error))
        }
        guard let http = response as? HTTPURLResponse else {
            throw .unreachable("Not an HTTP response.")
        }
        switch http.statusCode {
        case 200..<300:
            do {
                return try CocoJSON.decoder.decode(Response.self, from: data)
            } catch {
                throw .decoding("\(Response.self): \(error)")
            }
        case 401, 403:
            throw .unauthorized
        default:
            if let body = try? CocoJSON.decoder.decode(ErrorResponse.self, from: data) {
                throw .server(code: body.error.code, message: body.error.message)
            }
            throw .server(
                code: "http_\(http.statusCode)",
                message: String(decoding: data.prefix(500), as: UTF8.self))
        }
    }

    static func describe(_ error: any Error) -> String {
        if let error = error as? URLError {
            switch error.code {
            case .timedOut: return "Coco Hub took too long to answer."
            case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
                return "Could not connect to Coco Hub."
            case .notConnectedToInternet, .networkConnectionLost:
                return "The network connection was lost."
            default: return error.localizedDescription
            }
        }
        return error.localizedDescription
    }
}
