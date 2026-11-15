import Foundation
import Observation

/// Voice test mode (#6): while switched on (from Diagnostics), counts what the voice loop does
/// so the owner can measure the wake word and speech on the kitchen iPad: wake-word detections
/// and probable false wakes, per-turn latency, how long "hey coco" was actually listening, and
/// thermal / battery / CPU samples every minute. Off by default. Never records or stores audio,
/// and keeps no transcript text (only its word count).
@MainActor
@Observable
public final class VoiceTestRecorder {
    public enum Trigger: String, Sendable { case wakeWord = "wake", tap }

    public struct WakeDetection: Sendable, Equatable {
        public enum Outcome: Sendable, Equatable {
            /// Not resolved yet (the turn is still going).
            case pending
            /// A request with a non-empty transcript followed.
            case request
            /// Nothing was heard, the recognizer failed, or the turn was cancelled or cut off
            /// first: most likely the wake word fired on something else.
            case probableFalseWake
        }

        public var at: Date
        public var outcome: Outcome
    }

    public struct Turn: Sendable, Equatable {
        public var trigger: Trigger
        public var startedAt: Date
        /// Words in the final transcript (0 when nothing was heard).
        public var words: Int
        /// Last partial transcript (≈ end of speech) → final transcript, in seconds. Includes the
        /// silence the recognizer waits for before deciding the person has finished.
        public var speechEndToFinal: TimeInterval?
        /// Final transcript → the Hub's reply, in seconds.
        public var finalToReply: TimeInterval?
        public var replySucceeded: Bool?
    }

    public struct Sample: Sendable, Equatable {
        public var at: Date
        public var conditions: DeviceConditions
        /// This app's CPU use since the previous sample (100 = one full core).
        public var cpuPercent: Double?
    }

    public struct Summary: Sendable, Equatable {
        public var duration: TimeInterval = 0
        public var wakeDetections = 0
        public var wakesWithRequest = 0
        public var probableFalseWakes = 0
        public var taps = 0
        /// Seconds the wake word spent in each state.
        public var armed: TimeInterval = 0
        public var paused: TimeInterval = 0
        public var restarting: TimeInterval = 0
        public var off: TimeInterval = 0
        /// Times the wake word failed to start or was lost (entered `retrying` / `unavailable`).
        public var wakeWordFailures = 0
        public var maxThermal: DeviceConditions.Thermal?
    }

    public private(set) var isRecording = false
    public private(set) var startedAt: Date?
    public private(set) var stoppedAt: Date?
    public private(set) var onDeviceRecognition: Bool?
    public private(set) var device: String?
    public private(set) var wakeDetections: [WakeDetection] = []
    public private(set) var turns: [Turn] = []
    public private(set) var samples: [Sample] = []
    /// The wake word's status as last reported.
    public private(set) var wakeWordStatus: VoiceLoop.WakeWordStatus?

    @ObservationIgnored private var conditions: any DeviceConditionsSource
    @ObservationIgnored private let now: @Sendable () -> Date
    @ObservationIgnored private let sleep: @Sendable (Duration) async throws -> Void
    @ObservationIgnored private let sampleInterval: Duration
    @ObservationIgnored private let timeZone: TimeZone
    @ObservationIgnored private var samplingTask: Task<Void, Never>?

    private struct TurnInProgress {
        var trigger: Trigger
        var startedAt: Date
        var wakeIndex: Int?
        var lastSpeech: Date?
        var finalAt: Date?
        var words = 0
        var replyAfter: TimeInterval?
        var replySucceeded: Bool?
    }

    @ObservationIgnored private var current: TurnInProgress?
    /// A wake detection whose turn has not started yet.
    @ObservationIgnored private var pendingWake: Int?
    private var statusTimes: [StatusBucket: TimeInterval] = [:]
    private var wakeWordFailures = 0
    @ObservationIgnored private var statusSince: Date?

    private enum StatusBucket { case armed, paused, restarting, off }

    public init(
        conditions: (any DeviceConditionsSource)? = nil,
        now: @escaping @Sendable () -> Date = { Date() },
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        sampleInterval: Duration = .seconds(60),
        timeZone: TimeZone = .current
    ) {
        self.conditions = conditions ?? LiveDeviceConditions()
        self.now = now
        self.sleep = sleep
        self.sampleInterval = sampleInterval
        self.timeZone = timeZone
    }

    // MARK: - Control

    /// Clears the last run and starts counting. `device` is a free-text line for the report.
    public func start(
        onDeviceRecognition: Bool?, wakeWord: VoiceLoop.WakeWordStatus, device: String? = nil
    ) {
        stopSampling()
        let start = now()
        isRecording = true
        startedAt = start
        stoppedAt = nil
        self.onDeviceRecognition = onDeviceRecognition
        self.device = device
        wakeDetections = []
        turns = []
        samples = []
        current = nil
        pendingWake = nil
        statusTimes = [:]
        wakeWordFailures = 0
        wakeWordStatus = wakeWord
        statusSince = start
        takeSample()
        samplingTask = Task { [weak self, sleep, sampleInterval] in
            while !Task.isCancelled {
                do { try await sleep(sampleInterval) } catch { return }
                guard let self, self.isRecording else { return }
                self.takeSample()
            }
        }
    }

    public func stop() {
        guard isRecording else { return }
        closeStatusInterval(at: now())
        takeSample()
        stoppedAt = now()
        isRecording = false
        stopSampling()
    }

    // MARK: - Inputs (from `VoiceLoop`)

    public func wakeWordDetected() {
        guard isRecording else { return }
        // A barge-in abandons the turn in progress without an `.ended`.
        finishTurn()
        if let pendingWake { resolveWake(pendingWake, as: .probableFalseWake) }
        wakeDetections.append(WakeDetection(at: now(), outcome: .pending))
        pendingWake = wakeDetections.count - 1
    }

    public func wakeWordStatusChanged(_ status: VoiceLoop.WakeWordStatus) {
        guard isRecording, status != wakeWordStatus else { return }
        closeStatusInterval(at: now())
        switch status {
        case .retrying, .unavailable: wakeWordFailures += 1
        case .armed, .paused: break
        }
        wakeWordStatus = status
    }

    public func record(_ event: ConversationEvent) {
        guard isRecording else { return }
        let time = now()
        switch event {
        case .started:
            finishTurn()
            current = TurnInProgress(
                trigger: pendingWake == nil ? .tap : .wakeWord, startedAt: time, wakeIndex: pendingWake)
            pendingWake = nil
        case .heardSpeech:
            current?.lastSpeech = time
        case .finalTranscript(let text):
            current?.finalAt = time
            let words = text.split(whereSeparator: \.isWhitespace).count
            current?.words = words
            if words > 0, let index = current?.wakeIndex { resolveWake(index, as: .request) }
        case .recognitionFailed:
            current?.finalAt = nil
        case .hubReplied(let succeeded):
            if let finalAt = current?.finalAt { current?.replyAfter = time.timeIntervalSince(finalAt) }
            current?.replySucceeded = succeeded
        case .ended:
            finishTurn()
            // A wake whose turn never started (e.g. no speech permission).
            if let pendingWake {
                resolveWake(pendingWake, as: .probableFalseWake)
                self.pendingWake = nil
            }
        }
    }

    // MARK: - Results

    public var summary: Summary {
        var s = Summary()
        if let startedAt {
            s.duration = (stoppedAt ?? now()).timeIntervalSince(startedAt)
        }
        s.wakeDetections = wakeDetections.count
        s.wakesWithRequest = wakeDetections.filter { $0.outcome == .request }.count
        s.probableFalseWakes = wakeDetections.filter { $0.outcome == .probableFalseWake }.count
        s.taps = turns.filter { $0.trigger == .tap }.count
        var times = statusTimes
        if isRecording, let since = statusSince, let status = wakeWordStatus {
            times[Self.bucket(status), default: 0] += now().timeIntervalSince(since)
        }
        s.armed = times[.armed] ?? 0
        s.paused = times[.paused] ?? 0
        s.restarting = times[.restarting] ?? 0
        s.off = times[.off] ?? 0
        s.wakeWordFailures = wakeWordFailures
        s.maxThermal = samples.compactMap(\.conditions.thermal).max()
        return s
    }

    /// Median of the turns' "speech end → final transcript", in seconds.
    public var medianSpeechEndToFinal: TimeInterval? { Self.median(turns.compactMap(\.speechEndToFinal)) }
    /// Median of the turns' "final transcript → Hub reply", in seconds.
    public var medianFinalToReply: TimeInterval? { Self.median(turns.compactMap(\.finalToReply)) }

    /// The plain-text report for the clipboard / share sheet (and `docs/spikes/voice.md`).
    public func report() -> String {
        let s = summary
        var lines: [String] = []
        lines.append("Coco voice test")
        if let startedAt {
            lines.append("Started: \(stamp(startedAt, date: true))")
        }
        lines.append("Duration: \(DiagnosticsFormat.uptime(seconds: Int(s.duration)))\(isRecording ? " (still running)" : "")")
        if let device { lines.append("Device: \(device)") }
        lines.append("On-device recognition supported: \(onDeviceRecognition.map { $0 ? "yes" : "no" } ?? "unknown")")
        lines.append("")
        lines.append("Wake word")
        lines.append("  Wake word detections: \(s.wakeDetections)")
        lines.append("  Followed by a request: \(s.wakesWithRequest)")
        let falseTimes = wakeDetections.filter { $0.outcome == .probableFalseWake }.map { stamp($0.at) }
        lines.append(
            "  Probable false wakes: \(s.probableFalseWakes)"
                + (falseTimes.isEmpty ? "" : " (at \(falseTimes.joined(separator: ", ")))"))
        if s.duration >= 60 {
            let perHour = Double(s.probableFalseWakes) / (s.duration / 3600)
            lines.append("  False wakes per hour: \(String(format: "%.1f", perHour))")
        }
        lines.append("  Tap-to-talk turns: \(s.taps)")
        lines.append("")
        lines.append("Continuous listening (wake word)")
        lines.append("  Armed: \(span(s.armed, of: s.duration))")
        lines.append("  Paused (conversation or alarm): \(span(s.paused, of: s.duration))")
        lines.append("  Restarting: \(span(s.restarting, of: s.duration))")
        lines.append("  Off/unavailable: \(span(s.off, of: s.duration))")
        lines.append("  Wake word start failures: \(s.wakeWordFailures)")
        lines.append("")
        lines.append("Latency")
        lines.append("  Speech end → final transcript: \(latencyLine(turns.compactMap(\.speechEndToFinal)))")
        lines.append("  Final transcript → Hub reply: \(latencyLine(turns.compactMap(\.finalToReply)))")
        lines.append("  (speech end = last partial transcript; includes the recognizer's end-of-speech wait)")
        lines.append("")
        lines.append("Device")
        lines.append("  Max thermal state: \(s.maxThermal.map(String.init(describing:)) ?? "unknown")")
        if let first = samples.first, let last = samples.last {
            lines.append("  Battery: \(battery(first.conditions)) → \(battery(last.conditions))")
        }
        let cpu = samples.compactMap(\.cpuPercent)
        if !cpu.isEmpty {
            let average = cpu.reduce(0, +) / Double(cpu.count)
            lines.append(String(format: "  App CPU: average %.1f%%, max %.1f%%", average, cpu.max() ?? 0))
        }
        lines.append("")
        lines.append("Samples (time, thermal, battery, app CPU)")
        for sample in samples {
            let cpuText = sample.cpuPercent.map { String(format: "%.1f%%", $0) } ?? "—"
            lines.append(
                "  \(stamp(sample.at))  \(sample.conditions.thermal.map(String.init(describing:)) ?? "?")  \(battery(sample.conditions))  \(cpuText)"
            )
        }
        lines.append("")
        lines.append("Turns (time, trigger, words, speech end → final, final → reply, reply)")
        for turn in turns {
            let toFinal = turn.speechEndToFinal.map { DiagnosticsFormat.latency(ms: Int(($0 * 1000).rounded())) } ?? "—"
            let toReply = turn.finalToReply.map { DiagnosticsFormat.latency(ms: Int(($0 * 1000).rounded())) } ?? "—"
            let reply = turn.replySucceeded.map { $0 ? "ok" : "failed" } ?? "—"
            lines.append("  \(stamp(turn.startedAt))  \(turn.trigger.rawValue)  \(turn.words)  \(toFinal)  \(toReply)  \(reply)")
        }
        return lines.joined(separator: "\n") + "\n"
    }

    // MARK: - Private

    private func takeSample() {
        let reading = conditions.sample()
        let time = now()
        var cpuPercent: Double?
        if let previous = samples.last, let cpu = reading.cpuSeconds, let before = previous.conditions.cpuSeconds {
            let wall = time.timeIntervalSince(previous.at)
            if wall > 0 { cpuPercent = max(0, cpu - before) / wall * 100 }
        }
        samples.append(Sample(at: time, conditions: reading, cpuPercent: cpuPercent))
    }

    private func stopSampling() {
        samplingTask?.cancel()
        samplingTask = nil
    }

    private func finishTurn() {
        guard let turn = current else { return }
        current = nil
        let speechEndToFinal: TimeInterval? =
            if let finalAt = turn.finalAt, let lastSpeech = turn.lastSpeech, turn.words > 0 {
                finalAt.timeIntervalSince(lastSpeech)
            } else {
                nil
            }
        turns.append(
            Turn(
                trigger: turn.trigger, startedAt: turn.startedAt, words: turn.words,
                speechEndToFinal: speechEndToFinal, finalToReply: turn.replyAfter,
                replySucceeded: turn.replySucceeded))
        if let index = turn.wakeIndex, wakeDetections[index].outcome == .pending {
            resolveWake(index, as: .probableFalseWake)
        }
    }

    private func resolveWake(_ index: Int, as outcome: WakeDetection.Outcome) {
        guard wakeDetections.indices.contains(index), wakeDetections[index].outcome == .pending else { return }
        wakeDetections[index].outcome = outcome
        if pendingWake == index { pendingWake = nil }
    }

    private func closeStatusInterval(at time: Date) {
        if let since = statusSince, let status = wakeWordStatus {
            statusTimes[Self.bucket(status), default: 0] += time.timeIntervalSince(since)
        }
        statusSince = time
    }

    private static func bucket(_ status: VoiceLoop.WakeWordStatus) -> StatusBucket {
        switch status {
        case .armed: .armed
        case .paused: .paused
        case .retrying: .restarting
        case .unavailable: .off
        }
    }

    private static func median(_ values: [Double]) -> Double? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted()
        let mid = sorted.count / 2
        return sorted.count.isMultiple(of: 2) ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
    }

    private func latencyLine(_ values: [Double]) -> String {
        guard let median = Self.median(values), let max = values.max() else { return "no turns" }
        func ms(_ s: Double) -> String { DiagnosticsFormat.latency(ms: Int((s * 1000).rounded())) }
        return "median \(ms(median)), max \(ms(max)) (n=\(values.count))"
    }

    private func span(_ seconds: TimeInterval, of total: TimeInterval) -> String {
        let text = DiagnosticsFormat.uptime(seconds: Int(seconds.rounded()))
        guard total > 0 else { return text }
        return String(format: "%@ (%.1f%%)", text, seconds / total * 100)
    }

    private func battery(_ c: DeviceConditions) -> String {
        let level = c.batteryLevel.map { "\(Int(($0 * 100).rounded()))%" } ?? "n/a"
        return "\(level) \(c.batteryState.rawValue)"
    }

    private func stamp(_ date: Date, date withDate: Bool = false) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = withDate ? "yyyy-MM-dd HH:mm:ss zzz" : "HH:mm:ss"
        return formatter.string(from: date)
    }
}
