import Foundation
import UserNotifications

/// One local alarm: ring for timer `id` at `fireDate` (local clock).
public struct AlarmRequest: Sendable, Equatable {
    public var id: String
    public var title: String
    public var fireDate: Date

    public init(id: String, title: String, fireDate: Date) {
        self.id = id
        self.title = title
        self.fireDate = fireDate
    }
}

/// Rings timers on the iPad itself, independent of the Hub.
@MainActor
public protocol AlarmScheduling: AnyObject {
    /// Called with the timer id when an alarm's time arrives (in-process path).
    var onFire: (@MainActor (String) -> Void)? { get set }
    /// Replaces the whole set: alarms not in `alarms` are cancelled, new or moved ones scheduled.
    func schedule(_ alarms: [AlarmRequest])
}

/// Does nothing; the default for previews and tests that don't care.
@MainActor
public final class NoAlarmScheduler: AlarmScheduling {
    public var onFire: (@MainActor (String) -> Void)?
    public init() {}
    public func schedule(_ alarms: [AlarmRequest]) {}
}

// MARK: - Building blocks

/// Precise one-shot callbacks inside the running app.
@MainActor
public protocol OneShotTimers: AnyObject {
    func schedule(id: String, at date: Date, fire: @escaping @MainActor () -> Void)
    func cancel(id: String)
}

/// The OS notification backup, which rings if the app is somehow not in the foreground.
public protocol AlarmNotifications: Sendable {
    /// Asks once for permission; later calls are cheap.
    func requestAuthorization() async
    func add(_ alarm: AlarmRequest) async
    /// Removes pending and already-delivered notifications for these alarms.
    func remove(ids: [String]) async
}

/// The real scheduler: an in-process timer (the primary, precise path) **and** a time-sensitive
/// local notification with a long sound (the backup if the app is backgrounded). Notification
/// permission is requested the first time any alarm is scheduled.
@MainActor
public final class LocalAlarmScheduler: AlarmScheduling {
    public var onFire: (@MainActor (String) -> Void)?

    private let timers: any OneShotTimers
    private let notifications: any AlarmNotifications
    private var scheduled: [String: AlarmRequest] = [:]
    private var askedForPermission = false
    /// Serialises notification changes so a remove can't overtake the add it undoes.
    private var notificationQueue: Task<Void, Never>?

    public init(timers: any OneShotTimers, notifications: any AlarmNotifications) {
        self.timers = timers
        self.notifications = notifications
    }

    public func schedule(_ alarms: [AlarmRequest]) {
        let wanted = Dictionary(alarms.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
        let removed = scheduled.keys.filter { wanted[$0] == nil }
        let changed = wanted.values.filter { scheduled[$0.id] != $0 }
        let moved = changed.map(\.id).filter { scheduled[$0] != nil }
        guard !removed.isEmpty || !changed.isEmpty else { return }

        for id in removed { timers.cancel(id: id) }
        for alarm in changed {
            timers.schedule(id: alarm.id, at: alarm.fireDate) { [weak self] in
                self?.onFire?(alarm.id)
            }
        }
        scheduled = wanted

        let askFirst = !askedForPermission && !wanted.isEmpty
        if askFirst { askedForPermission = true }
        let notifications = notifications
        let previous = notificationQueue
        let changedAlarms = changed.sorted { $0.fireDate < $1.fireDate }
        let idsToRemove = removed + moved
        notificationQueue = Task {
            await previous?.value
            if askFirst { await notifications.requestAuthorization() }
            if !idsToRemove.isEmpty { await notifications.remove(ids: idsToRemove) }
            for alarm in changedAlarms { await notifications.add(alarm) }
        }
    }

    /// Waits for queued notification changes (tests).
    public func flush() async {
        await notificationQueue?.value
    }
}

/// `OneShotTimers` using tasks. Sleeps in slices of at most a minute and re-checks the wall
/// clock, so a clock change (NTP, time zone) doesn't make an alarm late.
@MainActor
public final class TaskOneShotTimers: OneShotTimers {
    private var tasks: [String: Task<Void, Never>] = [:]
    private let now: @Sendable () -> Date

    public init(now: @escaping @Sendable () -> Date = Date.init) {
        self.now = now
    }

    public func schedule(id: String, at date: Date, fire: @escaping @MainActor () -> Void) {
        tasks[id]?.cancel()
        let now = now
        tasks[id] = Task { [weak self] in
            while !Task.isCancelled {
                let remaining = date.timeIntervalSince(now())
                if remaining <= 0 { break }
                try? await Task.sleep(for: .seconds(min(remaining, 60)), tolerance: .milliseconds(20))
            }
            guard !Task.isCancelled else { return }
            self?.tasks[id] = nil
            fire()
        }
    }

    public func cancel(id: String) {
        tasks.removeValue(forKey: id)?.cancel()
    }
}

/// `AlarmNotifications` over `UNUserNotificationCenter`. Without a notification-center delegate
/// the OS does not present these while the app is in the foreground, so they never double up
/// with the in-app alarm; they only ring when the app is not frontmost.
public struct UserNotificationAlarms: AlarmNotifications {
    /// A sound file in `Library/Sounds` (see `AlarmTone.installNotificationSound()`), ≤ 30 s.
    public let soundName: String?

    public init(soundName: String? = AlarmTone.fileName) {
        self.soundName = soundName
    }

    static func identifier(_ id: String) -> String { "coco.timer.\(id)" }

    public func requestAuthorization() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [
            .alert, .sound,
        ])
    }

    public func add(_ alarm: AlarmRequest) async {
        let content = UNMutableNotificationContent()
        content.title = alarm.title
        content.body = "Timer done. Open Coco to stop the alarm."
        content.sound = soundName.map { UNNotificationSound(named: UNNotificationSoundName($0)) } ?? .default
        content.interruptionLevel = .timeSensitive
        let interval = max(1, alarm.fireDate.timeIntervalSinceNow)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: interval, repeats: false)
        let request = UNNotificationRequest(
            identifier: Self.identifier(alarm.id), content: content, trigger: trigger)
        try? await UNUserNotificationCenter.current().add(request)
    }

    public func remove(ids: [String]) async {
        let identifiers = ids.map(Self.identifier)
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: identifiers)
        center.removeDeliveredNotifications(withIdentifiers: identifiers)
    }
}

extension LocalAlarmScheduler {
    /// The app's wiring: task timers + `UNUserNotificationCenter`.
    public static func live() -> LocalAlarmScheduler {
        LocalAlarmScheduler(timers: TaskOneShotTimers(), notifications: UserNotificationAlarms())
    }
}
