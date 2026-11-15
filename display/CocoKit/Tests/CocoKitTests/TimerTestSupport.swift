import Foundation
import Synchronization
@testable import CocoKit

/// A settable clock shared between a test and the code under test.
final class TestClock: Sendable {
    private let current: Mutex<Date>

    init(_ start: Date = Date(timeIntervalSince1970: 1_800_000_000)) {
        current = Mutex(start)
    }

    var now: Date { current.withLock { $0 } }
    func advance(by seconds: TimeInterval) { current.withLock { $0 += seconds } }
    var function: @Sendable () -> Date { { [self] in now } }
}

/// A Hub that keeps timers in memory and can be "stopped" (every call throws `.unreachable`).
final class FakeTimerHub: HubAPI {
    struct State {
        var reachable = true
        var timers: [String: TimerInfo] = [:]
        var created: [TimerCreateRequest] = []
        var dismissed: [String] = []
        var cancelled: [String] = []
        var added: [AddedTime] = []
        var nextID = 1
    }

    let state = Mutex(State())
    /// The Hub's clock (may be skewed from the Display's).
    let serverNow: @Sendable () -> Date

    init(serverNow: @escaping @Sendable () -> Date) {
        self.serverNow = serverNow
    }

    var reachable: Bool {
        get { state.withLock { $0.reachable } }
        set { state.withLock { $0.reachable = newValue } }
    }
    var created: [TimerCreateRequest] { state.withLock { $0.created } }
    var dismissed: [String] { state.withLock { $0.dismissed } }
    var cancelled: [String] { state.withLock { $0.cancelled } }
    var added: [AddedTime] { state.withLock { $0.added } }

    struct AddedTime: Equatable, Sendable {
        var id: String
        var seconds: Int
    }

    private func checkReachable() throws(HubError) {
        if !reachable { throw .unreachable("Could not connect to Coco Hub.") }
    }

    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        throw .unreachable("unused")
    }
    func idle() async throws(HubError) -> IdleInfo { throw .unreachable("unused") }
    func timers() async throws(HubError) -> TimersList {
        try checkReachable()
        return TimersList(timers: state.withLock { Array($0.timers.values) }, serverTime: serverNow())
    }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo {
        try checkReachable()
        let now = serverNow()
        return state.withLock { state in
            let id = "t_\(state.nextID)"
            state.nextID += 1
            let timer = TimerInfo(
                id: id, name: request.name ?? "timer", durationS: request.durationS, createdAt: now,
                endsAt: now.addingTimeInterval(TimeInterval(request.durationS)), state: .running)
            state.timers[id] = timer
            state.created.append(request)
            return timer
        }
    }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo {
        try checkReachable()
        return try finish(id: id, as: .dismissed)
    }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo {
        try checkReachable()
        return try finish(id: id, as: .cancelled)
    }
    /// Like the Hub: a running timer ends later; a ringing one restarts `seconds` from now.
    func addTime(id: String, seconds: Int) async throws(HubError) -> TimerInfo {
        try checkReachable()
        let now = serverNow()
        let result: TimerInfo? = state.withLock { state in
            state.added.append(AddedTime(id: id, seconds: seconds))
            guard var timer = state.timers[id] else { return nil }
            let base = timer.state == .ringing ? now : timer.endsAt
            timer.endsAt = base.addingTimeInterval(TimeInterval(seconds))
            timer.durationS = Int(timer.endsAt.timeIntervalSince(timer.createdAt))
            timer.state = .running
            state.timers[id] = timer
            return timer
        }
        guard let result else { throw .server(code: "not_found", message: "No timer \(id).") }
        return result
    }

    private func finish(id: String, as newState: TimerInfo.State) throws(HubError) -> TimerInfo {
        let result: TimerInfo? = state.withLock { state in
            if newState == .dismissed { state.dismissed.append(id) } else { state.cancelled.append(id) }
            guard var timer = state.timers.removeValue(forKey: id) else { return nil }
            timer.state = newState
            return timer
        }
        guard let result else { throw .server(code: "not_found", message: "No timer \(id).") }
        return result
    }
}

/// Records what was scheduled; `fireDue(at:)` plays the in-process timer.
@MainActor
final class FakeAlarmScheduler: AlarmScheduling {
    var onFire: (@MainActor (String) -> Void)?
    private(set) var scheduled: [AlarmRequest] = []
    private(set) var scheduleCalls = 0

    func schedule(_ alarms: [AlarmRequest]) {
        scheduled = alarms
        scheduleCalls += 1
    }

    /// Fires every scheduled alarm whose date has passed, like the real in-process timer would.
    func fireDue(at now: Date) {
        for alarm in scheduled where alarm.fireDate <= now {
            onFire?(alarm.id)
        }
    }
}

@MainActor
final class FakeAlarmPlayer: AlarmPlayer {
    private(set) var isRinging = false
    private(set) var starts = 0
    private(set) var stops = 0

    func start() {
        isRinging = true
        starts += 1
    }
    func stop() {
        isRinging = false
        stops += 1
    }
}

extension TimerInfo {
    static func make(
        id: String, name: String? = "pasta", duration: Int = 600, endsAt: Date,
        state: TimerInfo.State = .running
    ) -> TimerInfo {
        TimerInfo(
            id: id, name: name, durationS: duration,
            createdAt: endsAt.addingTimeInterval(-TimeInterval(duration)), endsAt: endsAt, state: state)
    }
}
