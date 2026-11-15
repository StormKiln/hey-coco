import Foundation
import Synchronization
@testable import CocoKit

/// Polls (yielding the main actor) until `condition` holds or ~2s pass.
@MainActor
func waitUntil(_ condition: () -> Bool) async -> Bool {
    for _ in 0..<200 {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return condition()
}

/// A `sleep` replacement whose sleeps only end when the test fires them. Honours cancellation,
/// so code that cancels a pending sleep sees `CancellationError` like it would with `Task.sleep`.
final class ManualSleeper: Sendable {
    private struct Pending {
        let id: Int
        let duration: Duration
        let continuation: CheckedContinuation<Void, any Error>
    }
    private struct State {
        var nextID = 0
        var pending: [Pending] = []
        var cancelledEarly: Set<Int> = []
    }
    private let state = Mutex(State())

    var sleep: @Sendable (Duration) async throws -> Void {
        { [self] duration in try await self.sleep(for: duration) }
    }

    /// Durations of the sleeps currently waiting, oldest first.
    var pending: [Duration] { state.withLock { $0.pending.map(\.duration) } }

    /// Ends every pending sleep (or only those of `duration`) normally.
    func fire(_ duration: Duration? = nil) {
        let fired = state.withLock { state -> [Pending] in
            let matching = state.pending.filter { duration == nil || $0.duration == duration }
            state.pending.removeAll { duration == nil || $0.duration == duration }
            return matching
        }
        for pending in fired { pending.continuation.resume() }
    }

    private func sleep(for duration: Duration) async throws {
        let id = state.withLock { state in
            defer { state.nextID += 1 }
            return state.nextID
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                let cancelled = state.withLock { state -> Bool in
                    if state.cancelledEarly.remove(id) != nil { return true }
                    state.pending.append(Pending(id: id, duration: duration, continuation: continuation))
                    return false
                }
                if cancelled { continuation.resume(throwing: CancellationError()) }
            }
        } onCancel: {
            let continuation = state.withLock { state -> CheckedContinuation<Void, any Error>? in
                if let index = state.pending.firstIndex(where: { $0.id == id }) {
                    return state.pending.remove(at: index).continuation
                }
                state.cancelledEarly.insert(id)
                return nil
            }
            continuation?.resume(throwing: CancellationError())
        }
    }
}

/// Shared, ordered record of what the fakes were asked to do, for ordering assertions.
@MainActor
final class CallLog {
    private(set) var entries: [String] = []
    func record(_ entry: String) { entries.append(entry) }
}

@MainActor
final class FakeRecognizer: SpeechRecognizer {
    var permission: SpeechPermission = .granted
    var startError: (any Error)?
    private(set) var startCount = 0
    private(set) var stopCount = 0
    private(set) var cancelCount = 0
    private(set) var continuation: AsyncThrowingStream<SpeechTranscript, any Error>.Continuation?

    func requestPermission() async -> SpeechPermission { permission }

    func start() throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        startCount += 1
        if let startError { throw startError }
        let (stream, continuation) = AsyncThrowingStream.makeStream(of: SpeechTranscript.self)
        self.continuation = continuation
        return stream
    }

    func stop() { stopCount += 1 }

    func cancel() {
        cancelCount += 1
        continuation?.finish()
    }

    /// Sends one transcript update to the controller.
    func hear(_ transcript: SpeechTranscript) { continuation?.yield(transcript) }
    func finish(throwing error: (any Error)? = nil) { continuation?.finish(throwing: error) }
}

@MainActor
final class FakeSynthesizer: SpeechSynthesizer {
    let log: CallLog
    /// When true, `speak` waits for `finishSpeaking()` or `stop()`.
    var holds = false
    private(set) var spoken: [String] = []
    private(set) var stopCount = 0
    private var waiting: CheckedContinuation<Void, Never>?

    init(log: CallLog) { self.log = log }

    var isSpeaking: Bool { waiting != nil }

    func speak(_ text: String) async {
        spoken.append(text)
        log.record("say:\(text)")
        guard holds else { return }
        await withCheckedContinuation { waiting = $0 }
    }

    func stop() {
        stopCount += 1
        finishSpeaking()
    }

    func finishSpeaking() {
        waiting?.resume()
        waiting = nil
    }
}

@MainActor
final class FakeConversationAudio: ConversationAudio {
    let log: CallLog
    init(log: CallLog) { self.log = log }
    func beginConversation() async throws { log.record("audio:begin") }
    func playListeningChime() async { log.record("audio:chime") }
    func endConversation() { log.record("audio:end") }
}

@MainActor
final class FakeConversationHost: ConversationHost {
    let log: CallLog
    var currentView: ViewSpec = .idle
    var alarmRinging = false
    var mediaPlaying: MediaTarget?
    var result: Result<Action, HubError> = .failure(.unreachable("down"))
    /// When true, `postUtterance` waits for `respond()`.
    var holds = false
    private(set) var requests: [UtteranceRequest] = []
    private(set) var applied: [Action] = []
    private var waiting: CheckedContinuation<Void, Never>?

    init(log: CallLog) { self.log = log }

    var isWaiting: Bool { waiting != nil }

    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        requests.append(request)
        log.record("hub:\(request.text)")
        if holds { await withCheckedContinuation { waiting = $0 } }
        return try result.get()
    }

    func respond() {
        waiting?.resume()
        waiting = nil
    }

    func perform(_ command: Command) { log.record("command:\(command.type)") }

    func apply(_ action: Action) {
        applied.append(action)
        log.record("view:\(action.view?.type ?? "nil")")
        if let view = action.view { currentView = view }
    }
}
