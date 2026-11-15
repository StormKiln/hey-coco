import Foundation

/// Decides when the speaker has finished: `silenceTimeout` after the last heard words, or
/// `noSpeechTimeout` if nothing is heard at all, and never later than `maxDuration`.
/// Calls `onEnd` exactly once.
@MainActor
public final class UtteranceEndpointer {
    public struct Policy: Sendable, Equatable {
        public var silenceTimeout: Duration
        public var noSpeechTimeout: Duration
        public var maxDuration: Duration

        public init(
            silenceTimeout: Duration = .milliseconds(1200),
            noSpeechTimeout: Duration = .seconds(5),
            maxDuration: Duration = .seconds(8)
        ) {
            self.silenceTimeout = silenceTimeout
            self.noSpeechTimeout = noSpeechTimeout
            self.maxDuration = maxDuration
        }
    }

    public let policy: Policy
    public private(set) var hasEnded = false

    private let sleep: @Sendable (Duration) async throws -> Void
    private let onEnd: @MainActor () -> Void
    private var silenceTask: Task<Void, Never>?
    private var capTask: Task<Void, Never>?

    public init(
        policy: Policy = Policy(),
        sleep: @escaping @Sendable (Duration) async throws -> Void = cocoRealSleep,
        onEnd: @escaping @MainActor () -> Void
    ) {
        self.policy = policy
        self.sleep = sleep
        self.onEnd = onEnd
    }

    /// Call when listening starts.
    public func start() {
        guard !hasEnded else { return }
        capTask = after(policy.maxDuration)
        silenceTask = after(policy.noSpeechTimeout)
    }

    /// Call on every new partial transcript.
    public func heardSpeech() {
        guard !hasEnded else { return }
        silenceTask?.cancel()
        silenceTask = after(policy.silenceTimeout)
    }

    public func cancel() {
        silenceTask?.cancel()
        capTask?.cancel()
        silenceTask = nil
        capTask = nil
    }

    private func after(_ duration: Duration) -> Task<Void, Never> {
        Task { [weak self, sleep] in
            do { try await sleep(duration) } catch { return }
            self?.end()
        }
    }

    private func end() {
        guard !hasEnded else { return }
        hasEnded = true
        cancel()
        onEnd()
    }
}

/// Returns a transient screen (the Answer screen) to idle after `timeout` of no interaction.
/// Feed it the current view and whether a conversation is active on every change; it only counts
/// down while one of `views` is showing and nothing is being said or heard.
@MainActor
public final class AutoReturnTimer {
    public let timeout: Duration
    public let views: Set<ViewType>
    public private(set) var isArmed = false

    private let sleep: @Sendable (Duration) async throws -> Void
    private let onTimeout: @MainActor () -> Void
    private var armedFor: ViewSpec?
    private var task: Task<Void, Never>?

    public init(
        timeout: Duration = .seconds(20),
        views: Set<ViewType> = [.answer],
        sleep: @escaping @Sendable (Duration) async throws -> Void = cocoRealSleep,
        onTimeout: @escaping @MainActor () -> Void
    ) {
        self.timeout = timeout
        self.views = views
        self.sleep = sleep
        self.onTimeout = onTimeout
    }

    public func update(view: ViewSpec, busy: Bool) {
        let shouldRun = !busy && (view.viewType.map(views.contains) ?? false)
        if !shouldRun {
            disarm()
        } else if armedFor != view {
            arm(for: view)
        }
    }

    /// A touch on the screen: restart the countdown if it is running.
    public func noteInteraction() {
        guard let view = armedFor else { return }
        arm(for: view)
    }

    private func arm(for view: ViewSpec) {
        task?.cancel()
        armedFor = view
        isArmed = true
        task = Task { [weak self, sleep, timeout] in
            do { try await sleep(timeout) } catch { return }
            guard let self, !Task.isCancelled else { return }
            self.disarm()
            self.onTimeout()
        }
    }

    private func disarm() {
        task?.cancel()
        task = nil
        armedFor = nil
        isArmed = false
    }
}

/// The "I'm listening" chime: two short rising sine tones, generated in code (no audio asset).
public enum ListeningChime {
    /// 16-bit mono PCM WAV.
    public static func wavData(sampleRate: Int = 44_100) -> Data {
        let tones: [(frequency: Double, seconds: Double)] = [(880, 0.09), (1318.5, 0.12)]
        let amplitude = 0.35 * Double(Int16.max)
        var samples: [Int16] = []
        for tone in tones {
            let count = Int(tone.seconds * Double(sampleRate))
            let fade = max(1, count / 5)
            for i in 0..<count {
                // Linear fade in/out on each tone so it doesn't click.
                let envelope = min(1, Double(i) / Double(fade), Double(count - 1 - i) / Double(fade))
                let value = sin(2 * .pi * tone.frequency * Double(i) / Double(sampleRate))
                samples.append(Int16(value * envelope * amplitude))
            }
        }

        var data = Data()
        func append<T: FixedWidthInteger>(_ value: T) {
            withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
        }
        let dataSize = UInt32(samples.count * 2)
        data.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36) + dataSize)
        data.append(contentsOf: Array("WAVEfmt ".utf8))
        append(UInt32(16))  // fmt chunk size
        append(UInt16(1))  // PCM
        append(UInt16(1))  // mono
        append(UInt32(sampleRate))
        append(UInt32(sampleRate * 2))  // byte rate
        append(UInt16(2))  // block align
        append(UInt16(16))  // bits per sample
        data.append(contentsOf: Array("data".utf8))
        append(dataSize)
        for sample in samples { append(sample) }
        return data
    }
}
