import Foundation
import Observation

/// Keeps `/v1/idle` (forecast + night settings) fresh for the idle screen. The clock itself
/// never depends on this: the screen draws the time from the iPad's own clock.
@MainActor
@Observable
public final class IdleModel {
    public enum ForecastStatus: Sendable, Equatable {
        /// Nothing fetched yet.
        case loading
        case fresh
        /// Showing an older value: the last fetch failed, or the Hub's own weather fetch did.
        case stale
        /// No forecast to show (Hub never had weather, never reached, or the last value is too old).
        case unavailable
    }

    /// The last good answer; kept when later fetches fail.
    public private(set) var info: IdleInfo?
    /// When `info` was fetched (local clock).
    public private(set) var lastUpdated: Date?
    public private(set) var lastFetchFailed = false
    @ObservationIgnored private var attempted = false
    /// Called with every successful fetch (night mode follows the schedule and timezone).
    @ObservationIgnored public var onUpdate: (@MainActor (IdleInfo) -> Void)?

    @ObservationIgnored private let refreshInterval: Duration
    @ObservationIgnored private let retryInterval: Duration
    /// Older than this, a forecast is hidden rather than shown as stale.
    @ObservationIgnored private let maxAge: TimeInterval
    @ObservationIgnored private let sleeper: any Sleeper
    @ObservationIgnored private let now: @Sendable () -> Date
    @ObservationIgnored private var loop: Task<Void, Never>?

    public init(
        refreshInterval: Duration = .seconds(300),
        retryInterval: Duration = .seconds(60),
        maxAge: TimeInterval = 6 * 3600,
        sleeper: any Sleeper = TaskSleeper(),
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.refreshInterval = refreshInterval
        self.retryInterval = retryInterval
        self.maxAge = maxAge
        self.sleeper = sleeper
        self.now = now
    }

    public convenience init(now: @escaping @Sendable () -> Date, sleeper: any Sleeper) {
        self.init(sleeper: sleeper, now: now)
    }

    /// Fetches now and then every `refreshInterval` (every `retryInterval` after a failure)
    /// until `disconnect()`. Calling again (on reconnect) restarts with an immediate fetch.
    public func connect(_ api: any HubAPI) {
        loop?.cancel()
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh(using: api)
                let wait = self.lastFetchFailed ? self.retryInterval : self.refreshInterval
                do { try await self.sleeper.sleep(for: wait) } catch { return }
            }
        }
    }

    /// The Hub link came back: refresh at once if the last fetch failed (otherwise the value is
    /// still current and the regular schedule continues).
    public func reconnected(_ api: any HubAPI) {
        if lastFetchFailed || loop == nil { connect(api) }
    }

    public func disconnect() {
        loop?.cancel()
        loop = nil
    }

    /// One fetch. Success replaces `info`; failure keeps it.
    public func refresh(using api: any HubAPI) async {
        attempted = true
        do {
            let fresh = try await api.idle()
            info = fresh
            lastUpdated = now()
            lastFetchFailed = false
            onUpdate?(fresh)
        } catch {
            lastFetchFailed = true
        }
    }

    public func status(at date: Date? = nil) -> ForecastStatus {
        guard let weather = info?.weather, let lastUpdated else {
            return attempted ? .unavailable : .loading
        }
        if (date ?? now()).timeIntervalSince(lastUpdated) > maxAge { return .unavailable }
        return lastFetchFailed || weather.stale ? .stale : .fresh
    }

    /// The next `count` forecast hours, starting with the hour in progress.
    public func upcomingHours(count: Int = 6, at date: Date? = nil) -> [WeatherHour] {
        guard let hourly = info?.weather?.hourly else { return [] }
        let cutoff = (date ?? now()).addingTimeInterval(-3600)
        return Array(hourly.filter { $0.time > cutoff }.sorted { $0.time < $1.time }.prefix(count))
    }
}
