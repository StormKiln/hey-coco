import Foundation
import Observation

/// The Display's copy of the timers: mirrors the Hub (`hello` + `timer.*` events, REST results),
/// rings locally when a timer ends whether or not the Hub is reachable, and queues touch
/// actions taken while the Hub is down.
///
/// After every change it persists itself, reschedules local alarms, starts/stops the alarm
/// sound and calls `onTimersChanged`.
@MainActor
@Observable
public final class TimerStore {
    /// Running and ringing timers, soonest first.
    public private(set) var timers: [DisplayTimer] = []
    /// Hub clock minus iPad clock (seconds), from the latest `server_time`.
    public private(set) var clockOffset: TimeInterval = 0
    /// Queued Hub calls from while it was unreachable.
    public private(set) var pending: [PendingTimerOp] = []
    /// The latest sync problem worth showing, e.g. "Saved on this iPad; will sync with Coco Hub."
    public private(set) var lastSyncError: HubError?

    public var ringingTimers: [DisplayTimer] { timers.filter(\.isRinging) }
    public var runningTimers: [DisplayTimer] { timers.filter { $0.state == .running } }
    public var isAlarmRinging: Bool { timers.contains(where: \.isRinging) }
    public var hasPendingSync: Bool { !pending.isEmpty }

    /// Called after every change (the app returns to idle when the last timer goes).
    @ObservationIgnored public var onTimersChanged: (@MainActor () -> Void)?

    @ObservationIgnored private let now: @Sendable () -> Date
    @ObservationIgnored private let persistence: any TimerPersistence
    @ObservationIgnored private let scheduler: any AlarmScheduling
    @ObservationIgnored public let player: any AlarmPlayer
    @ObservationIgnored private let makeLocalID: @Sendable () -> String
    @ObservationIgnored private var api: (any HubAPI)?
    @ObservationIgnored private var syncing = false
    @ObservationIgnored private var syncAgain = false

    /// A local timer and a Hub timer within this many seconds of each other (and with
    /// compatible names) are the same timer.
    static let matchTolerance: TimeInterval = 5

    public init(
        now: @escaping @Sendable () -> Date = Date.init,
        persistence: any TimerPersistence = InMemoryTimerPersistence(),
        scheduler: any AlarmScheduling = NoAlarmScheduler(),
        player: any AlarmPlayer = SilentAlarmPlayer(),
        makeLocalID: @escaping @Sendable () -> String = { "local-\(UUID().uuidString)" }
    ) {
        self.now = now
        self.persistence = persistence
        self.scheduler = scheduler
        self.player = player
        self.makeLocalID = makeLocalID
        if let snapshot = persistence.load() {
            timers = snapshot.timers.sorted(by: Self.soonestFirst)
            pending = snapshot.pending
            clockOffset = snapshot.clockOffset
        }
        scheduler.onFire = { [weak self] id in self?.localFire(id: id) }
        changed(persist: false)
        fireDueAlarms()
    }

    /// Seconds left, never negative.
    public func remaining(of timer: DisplayTimer, at date: Date? = nil) -> TimeInterval {
        max(0, timer.endsAt.timeIntervalSince(date ?? now()))
    }

    // MARK: - Hub → Display

    public func apply(_ event: Event) {
        switch event.kind {
        case .hello(let hubTimers, _):
            replaceHubTimers(hubTimers, serverTime: event.serverTime)
        case .timerCreated(let timer), .timerUpdated(let timer), .timerFired(let timer):
            measureOffset(event.serverTime)
            upsert(timer)
            changed()
        case .timerDismissed(let timer), .timerCancelled(let timer):
            measureOffset(event.serverTime)
            timers.removeAll { $0.id == timer.id }
            changed()
        case .nowPlaying, .settingsChanged, .spotifyWakeRequested, .unknown:
            return
        }
    }

    /// A `GET /v1/timers` result: authoritative, like `hello`.
    public func apply(_ list: TimersList) {
        replaceHubTimers(list.timers, serverTime: list.serverTime)
    }

    /// The Hub is reachable through `api`; queued work is sent after the next `hello`.
    public func hubConnected(_ api: any HubAPI) {
        self.api = api
    }

    public func hubDisconnected() {
        api = nil
    }

    // MARK: - Touch

    /// Starts a timer now. It exists (and will ring) locally at once; the Hub copy follows when
    /// reachable.
    public func create(durationS: Int, name: String? = nil) async {
        let timer = DisplayTimer(
            id: makeLocalID(), name: name, durationS: durationS,
            endsAt: now().addingTimeInterval(TimeInterval(durationS)), state: .running,
            isLocalOnly: true)
        timers.append(timer)
        pending.append(.create(localID: timer.id))
        changed()
        await syncPending()
    }

    /// Stops a ringing timer (or ends a running one early).
    public func dismiss(id: String) async {
        await finish(id: id, op: PendingTimerOp.dismiss(id:))
    }

    public func cancel(id: String) async {
        await finish(id: id, op: PendingTimerOp.cancel(id:))
    }

    /// Dismisses every ringing timer (the overlay's Stop button, voice "stop").
    public func stopAlarms() async {
        let ids = ringingTimers.map(\.id)
        // Silence first, all at once, before any network round trip.
        for id in ids { removeLocally(id: id, queue: PendingTimerOp.dismiss(id:)) }
        changed()
        await syncPending()
    }

    /// Adds time to a timer that only exists on this iPad. Hub timers change through the Hub
    /// (the protocol has no add-time endpoint; see `AppModel.addTime`).
    public func addTimeLocally(id: String, seconds: Int) {
        guard let index = timers.firstIndex(where: { $0.id == id && $0.isLocalOnly }) else { return }
        let base = max(timers[index].endsAt, now())
        timers[index].endsAt = base.addingTimeInterval(TimeInterval(seconds))
        timers[index].durationS += seconds
        timers[index].state = .running
        changed()
    }

    /// Rings every running timer whose time has passed. Safety net for when the in-process
    /// timer could not run (app suspended); call on launch and when the app becomes active.
    public func fireDueAlarms() {
        let current = now()
        var any = false
        for index in timers.indices where timers[index].state == .running && timers[index].endsAt <= current {
            timers[index].state = .ringing
            any = true
        }
        if any { changed() }
    }

    // MARK: - Sync

    /// Sends queued work to the Hub. Unreachable → kept for later; rejected → dropped.
    public func syncPending() async {
        guard api != nil else { return }
        if syncing {
            syncAgain = true
            return
        }
        syncing = true
        defer { syncing = false }
        repeat {
            syncAgain = false
            await syncOnce()
        } while syncAgain && api != nil
    }

    private func syncOnce() async {
        for op in pending {
            guard let api else { return }
            do throws(HubError) {
                switch op {
                case .create(let localID):
                    guard let local = timers.first(where: { $0.id == localID && $0.isLocalOnly }),
                        local.state == .running
                    else {
                        // Already rang (or gone): nothing for the Hub to do.
                        drop(op)
                        continue
                    }
                    let seconds = max(1, Int(remaining(of: local).rounded(.up)))
                    let hubTimer = try await api.createTimer(
                        TimerCreateRequest(name: local.name, durationS: seconds))
                    drop(op)
                    let stillWanted = timers.contains { $0.id == localID }
                    let adoptedByEvent = timers.contains { $0.id == hubTimer.id }
                    // An event may already have adopted it; either way the local copy goes.
                    timers.removeAll { $0.id == localID }
                    if !stillWanted && !adoptedByEvent {
                        // Cancelled or stopped here while the create was in flight.
                        pending.append(.cancel(id: hubTimer.id))
                        syncAgain = true
                    } else if hubTimer.state == .running || hubTimer.state == .ringing {
                        // The Hub's reply carries no server_time: keep the local end time.
                        var adopted = convert(hubTimer)
                        adopted.endsAt = local.endsAt
                        if let index = timers.firstIndex(where: { $0.id == adopted.id }) {
                            timers[index] = adopted
                        } else {
                            timers.append(adopted)
                        }
                    }
                case .dismiss(let id):
                    _ = try await api.dismissTimer(id: id)
                    drop(op)
                case .cancel(let id):
                    _ = try await api.cancelTimer(id: id)
                    drop(op)
                }
                lastSyncError = nil
                changed()
            } catch {
                lastSyncError = error
                switch error {
                case .unreachable, .unauthorized, .untrustedHub:
                    return  // try again on reconnect
                case .server, .decoding:
                    // The Hub said no (e.g. timer already gone). Keep any local timer ringing
                    // locally, but don't retry forever.
                    drop(op)
                    changed()
                }
            }
        }
    }

    // MARK: - Internals

    private func finish(id: String, op: (String) -> PendingTimerOp) async {
        guard timers.contains(where: { $0.id == id }) else { return }
        removeLocally(id: id, queue: op)
        changed()
        await syncPending()
    }

    /// Removes a timer at once; queues the Hub call unless it never reached the Hub.
    private func removeLocally(id: String, queue op: (String) -> PendingTimerOp) {
        guard let timer = timers.first(where: { $0.id == id }) else { return }
        timers.removeAll { $0.id == id }
        if timer.isLocalOnly {
            pending.removeAll { $0 == .create(localID: id) }
        } else {
            pending.append(op(id))
        }
    }

    private func drop(_ op: PendingTimerOp) {
        if let index = pending.firstIndex(of: op) { pending.remove(at: index) }
    }

    private func localFire(id: String) {
        guard let index = timers.firstIndex(where: { $0.id == id }),
            timers[index].state == .running,
            timers[index].endsAt <= now().addingTimeInterval(0.5)
        else { return }
        timers[index].state = .ringing
        changed()
    }

    private func measureOffset(_ serverTime: Date) {
        clockOffset = serverTime.timeIntervalSince(now())
    }

    private func convert(_ timer: TimerInfo) -> DisplayTimer {
        DisplayTimer(
            id: timer.id, name: timer.name, durationS: timer.durationS,
            endsAt: timer.endsAt.addingTimeInterval(-clockOffset), state: timer.state,
            isLocalOnly: false)
    }

    private static func isActive(_ timer: TimerInfo) -> Bool {
        timer.state == .running || timer.state == .ringing
    }

    private var suppressedIDs: Set<String> {
        Set(
            pending.compactMap {
                switch $0 {
                case .dismiss(let id), .cancel(let id): id
                case .create: nil
                }
            })
    }

    private func replaceHubTimers(_ hubTimers: [TimerInfo], serverTime: Date) {
        measureOffset(serverTime)
        let previous = Dictionary(timers.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var next = timers.filter(\.isLocalOnly)
        let suppressed = suppressedIDs
        for hubTimer in hubTimers where Self.isActive(hubTimer) && !suppressed.contains(hubTimer.id) {
            var timer = convert(hubTimer)
            if let old = previous[timer.id] { timer.state = Self.mergedState(old: old, new: timer) }
            if let match = matchingLocal(for: timer, in: next) {
                next.removeAll { $0.id == match.id }
                pending.removeAll { $0 == .create(localID: match.id) }
                if match.isRinging { timer.state = .ringing }
            }
            next.append(timer)
        }
        timers = next
        changed()
        Task { await self.syncPending() }
    }

    private func upsert(_ hubTimer: TimerInfo) {
        guard Self.isActive(hubTimer), !suppressedIDs.contains(hubTimer.id) else { return }
        var timer = convert(hubTimer)
        if let index = timers.firstIndex(where: { $0.id == timer.id }) {
            timer.state = Self.mergedState(old: timers[index], new: timer)
            timers[index] = timer
            return
        }
        if let match = matchingLocal(for: timer, in: timers) {
            timers.removeAll { $0.id == match.id }
            pending.removeAll { $0 == .create(localID: match.id) }
            if match.isRinging { timer.state = .ringing }
        }
        timers.append(timer)
    }

    /// A late or stale `running` for a timer that already rang here must not silence it.
    private static func mergedState(old: DisplayTimer, new: DisplayTimer) -> TimerInfo.State {
        if old.isRinging, new.state == .running,
            abs(old.endsAt.timeIntervalSince(new.endsAt)) <= matchTolerance
        {
            return .ringing
        }
        return new.state
    }

    /// The local-only timer with a queued create that `hubTimer` is the Hub's copy of.
    private func matchingLocal(for hubTimer: DisplayTimer, in candidates: [DisplayTimer]) -> DisplayTimer? {
        candidates.first { local in
            local.isLocalOnly
                && pending.contains(.create(localID: local.id))
                && (local.name == nil || local.name == hubTimer.name)
                && abs(local.endsAt.timeIntervalSince(hubTimer.endsAt)) <= Self.matchTolerance
        }
    }

    private static func soonestFirst(_ a: DisplayTimer, _ b: DisplayTimer) -> Bool {
        a.endsAt == b.endsAt ? a.id < b.id : a.endsAt < b.endsAt
    }

    private func changed(persist: Bool = true) {
        timers.sort(by: Self.soonestFirst)
        if persist {
            try? persistence.save(
                TimerSnapshot(timers: timers, pending: pending, clockOffset: clockOffset))
        }
        scheduler.schedule(
            runningTimers.map {
                AlarmRequest(id: $0.id, title: $0.displayName, fireDate: $0.endsAt)
            })
        if isAlarmRinging, !player.isRinging {
            player.start()
        } else if !isAlarmRinging, player.isRinging {
            player.stop()
        }
        onTimersChanged?()
    }
}
