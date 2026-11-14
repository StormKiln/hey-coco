import Foundation

// MARK: - Backoff

/// Exponential backoff: `base · 2ⁿ`, capped, then shortened by up to `jitter` (a fraction) so a
/// reconnecting Display does not hammer the Hub in lockstep.
public struct Backoff: Sendable {
    public var base: Duration
    public var cap: Duration
    public var jitter: Double
    private let random: @Sendable () -> Double
    private var attempt = 0

    public init(
        base: Duration = .seconds(1), cap: Duration = .seconds(30), jitter: Double = 0.2,
        random: @escaping @Sendable () -> Double = { Double.random(in: 0..<1) }
    ) {
        self.base = base
        self.cap = cap
        self.jitter = jitter
        self.random = random
    }

    public mutating func nextDelay() -> Duration {
        let exponent = min(attempt, 30)  // avoid overflow; the cap is reached long before
        attempt += 1
        let raw = base * (1 << exponent)
        let capped = min(raw, cap)
        return capped * (1 - jitter * random())
    }

    public mutating func reset() { attempt = 0 }
}

/// Sleeping, behind a protocol so reconnect timing is testable without waiting.
public protocol Sleeper: Sendable {
    func sleep(for duration: Duration) async throws
}

public struct TaskSleeper: Sleeper {
    public init() {}
    public func sleep(for duration: Duration) async throws { try await Task.sleep(for: duration) }
}

// MARK: - Transport

public enum EventTransportError: Error, Sendable, Equatable {
    /// The Hub rejected the token (HTTP 401/403 on the upgrade, or close code 4401/1008).
    case unauthorized
}

/// Opens one WebSocket connection and yields its messages; the stream ends (or throws) when the
/// connection drops. Cancelling the consumer closes the connection.
public protocol EventTransport: Sendable {
    func connect(to url: URL) -> AsyncThrowingStream<Data, any Error>
}

/// `EventTransport` over `URLSessionWebSocketTask`, with a ping every `pingInterval` so a
/// half-open connection (iPad Wi-Fi napping) is noticed.
public struct URLSessionEventTransport: EventTransport {
    public let session: URLSession
    public var pingInterval: Duration = .seconds(20)

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func connect(to url: URL) -> AsyncThrowingStream<Data, any Error> {
        let task = session.webSocketTask(with: url)
        let pingInterval = pingInterval
        return AsyncThrowingStream { continuation in
            task.resume()
            let reader = Task {
                do {
                    while true {
                        switch try await task.receive() {
                        case .string(let text): continuation.yield(Data(text.utf8))
                        case .data(let data): continuation.yield(data)
                        @unknown default: break
                        }
                    }
                } catch {
                    continuation.finish(throwing: Self.map(error, task: task))
                }
            }
            let pinger = Task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: pingInterval)
                    if Task.isCancelled { return }
                    task.sendPing { error in
                        if error != nil { task.cancel(with: .goingAway, reason: nil) }
                    }
                }
            }
            continuation.onTermination = { _ in
                reader.cancel()
                pinger.cancel()
                task.cancel(with: .goingAway, reason: nil)
            }
        }
    }

    private static func map(_ error: any Error, task: URLSessionWebSocketTask) -> any Error {
        if let http = task.response as? HTTPURLResponse, [401, 403].contains(http.statusCode) {
            return EventTransportError.unauthorized
        }
        if task.closeCode == .policyViolation || task.closeCode.rawValue == 4401 {
            return EventTransportError.unauthorized
        }
        return error
    }
}

// MARK: - Stream

public enum EventStreamUpdate: Sendable, Equatable {
    /// A connection attempt is starting (1-based, counted since the stream began).
    case connecting(attempt: Int)
    /// The first message arrived on this connection; backoff has been reset.
    case connected
    case event(Event)
    /// The connection failed or dropped; the next attempt starts after `retryIn`.
    case waiting(retryIn: Duration, reason: String)
    /// The token was rejected. Terminal: the stream finishes after this.
    case unauthorized
}

/// Keeps a WebSocket to `/v1/events` open: decodes `Event`s, reconnects with backoff, and resets
/// the backoff once a connection delivers a message (the Hub sends `hello` on connect).
/// Messages that do not decode are skipped.
public struct EventStream: Sendable {
    public let url: URL
    public let transport: any EventTransport
    public let sleeper: any Sleeper
    public let backoff: Backoff

    public init(
        url: URL, transport: any EventTransport = URLSessionEventTransport(),
        sleeper: any Sleeper = TaskSleeper(), backoff: Backoff = Backoff()
    ) {
        self.url = url
        self.transport = transport
        self.sleeper = sleeper
        self.backoff = backoff
    }

    /// Starts connecting. Stop by ending iteration / cancelling the consuming task.
    public func updates() -> AsyncStream<EventStreamUpdate> {
        let (stream, continuation) = AsyncStream.makeStream(of: EventStreamUpdate.self)
        let task = Task { [self] in
            await run(continuation)
            continuation.finish()
        }
        continuation.onTermination = { _ in task.cancel() }
        return stream
    }

    private func run(_ continuation: AsyncStream<EventStreamUpdate>.Continuation) async {
        var backoff = backoff
        var attempt = 0
        while !Task.isCancelled {
            attempt += 1
            continuation.yield(.connecting(attempt: attempt))
            var connected = false
            var reason = "Lost connection to Coco Hub."
            do {
                for try await data in transport.connect(to: url) {
                    if !connected {
                        connected = true
                        backoff.reset()
                        continuation.yield(.connected)
                    }
                    if let event = try? CocoJSON.decoder.decode(Event.self, from: data) {
                        continuation.yield(.event(event))
                    }
                }
            } catch EventTransportError.unauthorized {
                continuation.yield(.unauthorized)
                return
            } catch {
                if !connected { reason = HubClient.describe(error) }
            }
            if Task.isCancelled { return }
            let delay = backoff.nextDelay()
            continuation.yield(.waiting(retryIn: delay, reason: reason))
            do {
                try await sleeper.sleep(for: delay)
            } catch {
                return
            }
        }
    }
}
