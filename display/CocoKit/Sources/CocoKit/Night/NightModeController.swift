import Foundation
import Observation

/// What night mode shows.
public enum NightPhase: Sendable, Equatable {
    /// Outside the night window: normal screens, the user's brightness.
    case day
    /// Inside the window and recently used: dark theme, low brightness.
    case dim
    /// Inside the window and idle: pure black, minimum brightness. A tap always wakes it.
    case dark
}

/// The Hub's night window (`IdleInfo.night`) parsed. Times are Hub-local wall-clock minutes, so a
/// DST change moves the window with the clock (21:30 is always 21:30).
public struct NightSchedule: Sendable, Equatable {
    /// Minutes after midnight, 0..<1440.
    public var startMinute: Int
    public var endMinute: Int
    /// Seconds without activity before `.dim` turns `.dark`.
    public var darkAfterIdle: TimeInterval

    /// `nil` when a time is not a valid `HH:MM`.
    public init?(_ night: Night) {
        guard let start = Self.minutes(night.start), let end = Self.minutes(night.end) else {
            return nil
        }
        startMinute = start
        endMinute = end
        darkAfterIdle = TimeInterval(max(0, night.darkAfterIdleS))
    }

    /// Whether `date` falls in the window, by the wall clock in `timeZone`. The window may cross
    /// midnight (21:30–06:30); equal start and end means no window.
    public func contains(_ date: Date, in timeZone: TimeZone) -> Bool {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        let minute = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        if startMinute == endMinute { return false }
        if startMinute < endMinute { return minute >= startMinute && minute < endMinute }
        return minute >= startMinute || minute < endMinute
    }

    private static func minutes(_ text: String) -> Int? {
        let parts = text.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, parts.allSatisfy({ $0.count == 2 }),
            let hour = Int(parts[0]), let minute = Int(parts[1]),
            (0..<24).contains(hour), (0..<60).contains(minute)
        else { return nil }
        return hour * 60 + minute
    }
}

/// The screen's backlight. `UIKitScreenBrightness` on iPad; a no-op on a Mac.
@MainActor
public protocol ScreenBrightness: AnyObject {
    /// 0...1.
    var level: Double { get set }
}

/// Night mode (#24): dims at bedtime, goes black after `darkAfterIdle` without activity, and
/// wakes on a tap, the wake word, a ringing alarm or a `screen.wake` command. Never goes dark
/// while an alarm rings, media plays or a conversation is in progress, and a tap always wakes it.
///
/// The user's daytime brightness is remembered the first time night mode lowers it and restored
/// at the end of the window and whenever the app leaves the foreground.
@MainActor
@Observable
public final class NightModeController {
    /// Things that keep the screen from going dark while they last.
    public enum Hold: Hashable, Sendable {
        case alarm, media, conversation
    }

    /// After a wake, the screen stays up at least this long, even with `dark_after_idle_s: 0`,
    /// so a tap can never leave it stuck black.
    public static let minimumAwake: TimeInterval = 10

    public private(set) var phase: NightPhase = .day
    public private(set) var schedule: NightSchedule?
    /// The Hub's timezone (`IdleInfo.timezone`); the device's until the Hub says otherwise.
    public private(set) var timeZone: TimeZone

    @ObservationIgnored private let brightness: any ScreenBrightness
    @ObservationIgnored private let dimBrightness: Double
    @ObservationIgnored private let darkBrightness: Double
    @ObservationIgnored private let tickInterval: Duration
    @ObservationIgnored private let sleep: @Sendable (Duration) async throws -> Void
    @ObservationIgnored private let now: @MainActor () -> Date

    @ObservationIgnored private var lastActivity: Date
    @ObservationIgnored private var holds: Set<Hold> = []
    /// `screen.sleep`: dark now, until the next activity.
    @ObservationIgnored private var forcedDark = false
    @ObservationIgnored private var inForeground = true
    /// The user's brightness before night mode lowered it; `nil` while untouched.
    @ObservationIgnored private var savedBrightness: Double?
    @ObservationIgnored private var loop: Task<Void, Never>?

    public init(
        brightness: any ScreenBrightness,
        timeZone: TimeZone = .current,
        dimBrightness: Double = 0.3,
        darkBrightness: Double = 0,
        tickInterval: Duration = .seconds(15),
        sleep: @escaping @Sendable (Duration) async throws -> Void = cocoRealSleep,
        now: @escaping @MainActor () -> Date = Date.init
    ) {
        self.brightness = brightness
        self.timeZone = timeZone
        self.dimBrightness = dimBrightness
        self.darkBrightness = darkBrightness
        self.tickInterval = tickInterval
        self.sleep = sleep
        self.now = now
        self.lastActivity = now()
    }

    // MARK: - Schedule

    /// From `IdleInfo` (schedule and the Hub's timezone).
    public func apply(_ info: IdleInfo) {
        if let zone = TimeZone(identifier: info.timezone) { timeZone = zone }
        update(night: info.night)
    }

    /// From `IdleInfo` or a `settings.changed` event. `nil` or a malformed schedule turns night
    /// mode off.
    public func update(night: Night?) {
        schedule = night.flatMap(NightSchedule.init)
        evaluate()
    }

    /// Ignored when the identifier is unknown.
    public func update(timeZoneIdentifier: String) {
        guard let zone = TimeZone(identifier: timeZoneIdentifier) else { return }
        timeZone = zone
        evaluate()
    }

    // MARK: - Inputs

    /// Something happened (touch, wake word, Coco speaking): restart the idle countdown and
    /// leave `.dark`.
    public func noteActivity() {
        lastActivity = now()
        forcedDark = false
        evaluate()
    }

    /// A tap on the dark screen or `screen.wake`.
    public func wake() { noteActivity() }

    /// `screen.sleep`: dark now. Only inside the night window, and not while something holds
    /// the screen awake.
    public func sleepNow() {
        guard isInWindow else { return }
        forcedDark = true
        evaluate()
    }

    /// Alarm ringing, media playing, conversation in progress. Starting one wakes the screen;
    /// ending one restarts the idle countdown.
    public func setHold(_ hold: Hold, _ active: Bool) {
        let changed = active ? holds.insert(hold).inserted : holds.remove(hold) != nil
        guard changed else { return }
        noteActivity()
    }

    /// Re-evaluates against the clock. Called by the tick loop.
    public func tick() { evaluate() }

    /// Ticks every `tickInterval` until `stop()`.
    public func start() {
        loop?.cancel()
        loop = Task { [weak self, sleep, tickInterval] in
            while !Task.isCancelled {
                do { try await sleep(tickInterval) } catch { return }
                self?.tick()
            }
        }
    }

    public func stop() {
        loop?.cancel()
        loop = nil
    }

    /// Hands the user's brightness back while Coco is not in front.
    public func didEnterBackground() {
        inForeground = false
        restoreBrightness()
    }

    /// Back in front: wake (someone opened the app) and re-apply night brightness.
    public func didBecomeActive() {
        inForeground = true
        noteActivity()
        applyBrightness()
    }

    // MARK: - State

    private var isInWindow: Bool { schedule?.contains(now(), in: timeZone) ?? false }

    private func evaluate() {
        let date = now()
        let next: NightPhase
        if let schedule, schedule.contains(date, in: timeZone) {
            // Entering the window: the idle countdown starts at bedtime.
            if phase == .day { lastActivity = max(lastActivity, date) }
            let idle = date.timeIntervalSince(lastActivity)
            let limit = max(schedule.darkAfterIdle, Self.minimumAwake)
            next = holds.isEmpty && (forcedDark || idle >= limit) ? .dark : .dim
        } else {
            forcedDark = false
            next = .day
        }
        guard next != phase else { return }
        phase = next
        applyBrightness()
    }

    private func applyBrightness() {
        guard inForeground else { return }
        switch phase {
        case .day:
            restoreBrightness()
        case .dim:
            let saved = rememberBrightness()
            brightness.level = min(saved, dimBrightness)
        case .dark:
            _ = rememberBrightness()
            brightness.level = darkBrightness
        }
    }

    private func rememberBrightness() -> Double {
        if let savedBrightness { return savedBrightness }
        let level = brightness.level
        savedBrightness = level
        return level
    }

    private func restoreBrightness() {
        guard let saved = savedBrightness else { return }
        brightness.level = saved
        savedBrightness = nil
    }
}

/// For tests and previews: just remembers the level.
@MainActor
public final class FakeScreenBrightness: ScreenBrightness {
    public var level: Double
    public init(level: Double = 1) { self.level = level }
}
