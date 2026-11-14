import Foundation
import Synchronization
import Testing
@testable import CocoKit

@Suite struct BackoffTests {
    @Test func doublesFromBaseUpToCap() {
        var backoff = Backoff(base: .seconds(1), cap: .seconds(30), jitter: 0.2, random: { 0 })
        let delays = (0..<8).map { _ in backoff.nextDelay() }
        #expect(delays == [1, 2, 4, 8, 16, 30, 30, 30].map { Duration.seconds($0) })
    }

    @Test func resetStartsOverFromBase() {
        var backoff = Backoff(base: .seconds(1), cap: .seconds(30), jitter: 0.2, random: { 0 })
        _ = backoff.nextDelay()
        _ = backoff.nextDelay()
        backoff.reset()
        #expect(backoff.nextDelay() == .seconds(1))
    }

    @Test func jitterOnlyShortensWithinFraction() {
        var backoff = Backoff(base: .seconds(10), cap: .seconds(30), jitter: 0.2, random: { 1 })
        #expect(backoff.nextDelay() == .seconds(8))
        #expect(backoff.nextDelay() == .seconds(16))
        #expect(backoff.nextDelay() == .seconds(24))
    }
}

/// Records requested sleeps and returns immediately.
final class RecordingSleeper: Sleeper {
    private let recorded = Mutex<[Duration]>([])
    var delays: [Duration] { recorded.withLock { $0 } }

    func sleep(for duration: Duration) async throws {
        recorded.withLock { $0.append(duration) }
        try Task.checkCancellation()
    }
}

/// Plays a script of connection outcomes, one per `connect`. Once the script runs out the
/// connection stays open (silent) until cancelled.
final class ScriptedTransport: EventTransport {
    enum Step: Sendable {
        case fail
        case unauthorized
        /// Delivers the messages, then the connection drops.
        case messages([String])
    }

    private let script: Mutex<[Step]>
    private let urls = Mutex<[URL]>([])
    var connectedURLs: [URL] { urls.withLock { $0 } }

    init(_ script: [Step]) { self.script = Mutex(script) }

    func connect(to url: URL) -> AsyncThrowingStream<Data, any Error> {
        urls.withLock { $0.append(url) }
        let step = script.withLock { $0.isEmpty ? nil : $0.removeFirst() }
        return AsyncThrowingStream { continuation in
            switch step {
            case .fail: continuation.finish(throwing: URLError(.cannotConnectToHost))
            case .unauthorized: continuation.finish(throwing: EventTransportError.unauthorized)
            case .messages(let messages):
                for message in messages { continuation.yield(Data(message.utf8)) }
                continuation.finish()
            case nil: break  // stays open
            }
        }
    }
}

private let helloJSON = #"{"type": "hello", "server_time": "2026-11-14T15:04:05Z", "timers": [], "now_playing": null}"#

@Suite struct EventStreamTests {
    let url = URL(string: "ws://hub.test:8765/v1/events?token=t")!

    private func collect(
        _ transport: ScriptedTransport, sleeper: RecordingSleeper, count: Int
    ) async -> [EventStreamUpdate] {
        let stream = EventStream(
            url: url, transport: transport, sleeper: sleeper,
            backoff: Backoff(base: .seconds(1), cap: .seconds(30), jitter: 0.2, random: { 0 }))
        var updates: [EventStreamUpdate] = []
        for await update in stream.updates() {
            updates.append(update)
            if updates.count == count { break }
        }
        return updates
    }

    @Test func decodesEventsAndReportsConnected() async throws {
        let transport = ScriptedTransport([.messages([helloJSON])])
        let updates = await collect(transport, sleeper: RecordingSleeper(), count: 3)
        let hello = try CocoJSON.decoder.decode(Event.self, from: Data(helloJSON.utf8))
        #expect(updates == [.connecting(attempt: 1), .connected, .event(hello)])
        #expect(transport.connectedURLs.first == url)  // it may already be reconnecting
    }

    @Test func undecodableMessagesAreSkipped() async throws {
        let transport = ScriptedTransport([.messages(["not json", helloJSON])])
        let updates = await collect(transport, sleeper: RecordingSleeper(), count: 3)
        #expect(updates.last.map { if case .event = $0 { true } else { false } } == true)
    }

    @Test func retriesWithExponentialBackoffAndResetsAfterSuccess() async throws {
        let transport = ScriptedTransport([.fail, .fail, .fail, .messages([helloJSON]), .fail, .fail])
        let sleeper = RecordingSleeper()
        // 3 failures (connecting + waiting each), success (connecting, connected, event, waiting),
        // 2 failures, then the final connecting.
        let updates = await collect(transport, sleeper: sleeper, count: 3 * 2 + 4 + 2 * 2 + 1)
        #expect(sleeper.delays == [1, 2, 4, 1, 2, 4].map { Duration.seconds($0) })
        #expect(updates.last == .connecting(attempt: 7))
        #expect(updates.contains(.waiting(retryIn: .seconds(4), reason: "Could not connect to Coco Hub.")))
    }

    @Test func backoffIsCapped() async throws {
        let transport = ScriptedTransport(Array(repeating: .fail, count: 8))
        let sleeper = RecordingSleeper()
        _ = await collect(transport, sleeper: sleeper, count: 8 * 2 + 1)
        #expect(sleeper.delays == [1, 2, 4, 8, 16, 30, 30, 30].map { Duration.seconds($0) })
    }

    @Test func unauthorizedStopsRetrying() async throws {
        let transport = ScriptedTransport([.fail, .unauthorized, .fail])
        let sleeper = RecordingSleeper()
        let stream = EventStream(
            url: url, transport: transport, sleeper: sleeper,
            backoff: Backoff(base: .seconds(1), cap: .seconds(30), jitter: 0, random: { 0 }))
        var updates: [EventStreamUpdate] = []
        for await update in stream.updates() { updates.append(update) }  // finishes on its own
        #expect(updates.last == .unauthorized)
        #expect(transport.connectedURLs.count == 2)
        #expect(sleeper.delays == [.seconds(1)])
    }
}
