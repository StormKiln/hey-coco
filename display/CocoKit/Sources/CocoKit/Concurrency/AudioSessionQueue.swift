import Foundation

/// The one serial queue that every `AVAudioSession` `setCategory` / `setMode` / `setActive` call
/// (and the alarm's `AVAudioPlayer`) runs on, so the main thread never does.
///
/// Apple documents those calls as potentially blocking. In the simulator, a `.playAndRecord`
/// activation with an unanswered microphone prompt never returned at all; on the main thread that
/// froze the whole app the moment an alarm started ringing (no overlay, no sound).
///
/// Serial on purpose: session changes land in the order they were asked for (the latest one
/// wins), whichever part of the app asked. The price is that one wedged call holds up the ones
/// behind it, so callers never *wait* on it from anywhere that matters: `VoiceLoop` gives up after
/// a timeout, and the alarm player falls back to a system alert sound (see `AudioAlarmPlayer`).
public final class AudioSessionQueue: Sendable {
    public static let shared = AudioSessionQueue(label: "com.stormkiln.heycoco.audio-session")

    private let queue: DispatchQueue

    public init(label: String) {
        queue = DispatchQueue(label: label, qos: .userInitiated)
    }

    /// Runs `work` on the queue and returns its result.
    public func run<T: Sendable>(_ work: @escaping @Sendable () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { continuation.resume(with: Result { try work() }) }
        }
    }

    /// Runs `work` on the queue without waiting for it.
    public func enqueue(_ work: @escaping @Sendable () -> Void) {
        queue.async(execute: work)
    }
}

/// `operation` did not finish within its deadline.
public struct AudioSessionTimeout: Error, Equatable {
    public init() {}
}

/// Runs `operation` but stops waiting for it after `timeout` (throwing `AudioSessionTimeout`).
///
/// Unlike a task group, this returns at the deadline even if `operation` never does (a wedged
/// audio-session call can't be cancelled): the operation is left to finish, or not, on its own.
@MainActor
func withAudioSessionTimeout(
    _ timeout: Duration,
    sleep: @escaping @Sendable (Duration) async throws -> Void,
    _ operation: @escaping @MainActor () async throws -> Void
) async throws {
    let once = ResumeOnce()
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
        Task { @MainActor in
            do {
                try await operation()
                once.resume(continuation, with: .success(()))
            } catch {
                once.resume(continuation, with: .failure(error))
            }
        }
        let timer = Task { @MainActor in
            do { try await sleep(timeout) } catch { return }
            once.resume(continuation, with: .failure(AudioSessionTimeout()))
        }
        once.onResume = { timer.cancel() }
    }
}

/// Resumes a continuation exactly once, from whichever side finishes first.
@MainActor
private final class ResumeOnce {
    private var done = false
    var onResume: (() -> Void)?

    func resume(_ continuation: CheckedContinuation<Void, any Error>, with result: Result<Void, any Error>) {
        guard !done else { return }
        done = true
        onResume?()
        onResume = nil
        continuation.resume(with: result)
    }
}
