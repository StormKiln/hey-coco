import Foundation

/// A timer as the Display tracks it. Unlike `TimerInfo`, `endsAt` is on the **iPad's** clock
/// (the Hub's `ends_at` corrected by the measured clock offset), and a timer can exist only
/// locally (created by touch while the Hub was unreachable).
public struct DisplayTimer: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var name: String?
    public var durationS: Int
    /// When it rings, on the local clock.
    public var endsAt: Date
    /// `running` or `ringing`; finished timers are removed, not kept.
    public var state: TimerInfo.State
    /// Created on this Display and not (yet) known to the Hub.
    public var isLocalOnly: Bool

    public init(
        id: String, name: String?, durationS: Int, endsAt: Date, state: TimerInfo.State,
        isLocalOnly: Bool
    ) {
        self.id = id
        self.name = name
        self.durationS = durationS
        self.endsAt = endsAt
        self.state = state
        self.isLocalOnly = isLocalOnly
    }

    /// `name` capitalised, or e.g. "5 min timer" for unnamed timers.
    public var displayName: String {
        if let name, !name.isEmpty { return name.prefix(1).uppercased() + name.dropFirst() }
        return "\(TimerFormat.duration(durationS)) timer"
    }

    public var isRinging: Bool { state == .ringing }
}

/// Work queued for the Hub while it was unreachable.
public enum PendingTimerOp: Codable, Sendable, Equatable {
    /// Create the local-only timer `localID` on the Hub with its remaining time.
    case create(localID: String)
    case dismiss(id: String)
    case cancel(id: String)
}

/// Everything `TimerStore` persists so a relaunch restores timers before the Hub reconnects.
public struct TimerSnapshot: Codable, Sendable, Equatable {
    public var timers: [DisplayTimer]
    public var pending: [PendingTimerOp]
    /// Hub clock minus iPad clock, seconds.
    public var clockOffset: TimeInterval

    public init(timers: [DisplayTimer], pending: [PendingTimerOp], clockOffset: TimeInterval) {
        self.timers = timers
        self.pending = pending
        self.clockOffset = clockOffset
    }
}

public enum TimerFormat {
    /// `m:ss` or `h:mm:ss`, rounded up so it reads `0:00` exactly when the timer rings.
    public static func countdown(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.up)))
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        func two(_ n: Int) -> String { n < 10 ? "0\(n)" : "\(n)" }
        return h > 0 ? "\(h):\(two(m)):\(two(s))" : "\(m):\(two(s))"
    }

    /// Short human duration: "5 min", "1 min 30 s", "1 h", "1 h 15 min".
    public static func duration(_ seconds: Int) -> String {
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        let s = seconds % 60
        var parts: [String] = []
        if h > 0 { parts.append("\(h) h") }
        if m > 0 { parts.append("\(m) min") }
        if s > 0 || parts.isEmpty { parts.append("\(s) s") }
        return parts.joined(separator: " ")
    }
}
