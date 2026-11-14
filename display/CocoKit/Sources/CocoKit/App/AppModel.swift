import Foundation
import Observation

/// How the Display's link to the Hub is doing, for the connection banner.
public enum ConnectionState: Sendable, Equatable {
    /// Looking for a Hub via Bonjour (no manual host set).
    case searching
    /// Have an address; first connection attempt in progress.
    case connecting
    case connected
    /// Can't reach the Hub; retrying in the background.
    case unreachable(reason: String)
    /// Token missing or rejected; needs Settings.
    case unauthorized
    /// Found a Hub but nothing is paired yet: the person must confirm its fingerprint
    /// (`AppModel.pair(with:)`). The token has not been sent anywhere.
    case needsPairing(PairingCandidate)
    /// A server at `host` is not the paired Hub (its certificate doesn't match the pin). It
    /// is ignored and never gets the token; "Forget Hub" in Settings re-pairs.
    case untrustedHub(host: String)
}

/// Owns the Hub client, the event stream and the screen router. The app's single source of truth.
@MainActor
@Observable
public final class AppModel {
    /// What the router shows. Starts on idle so the app is never blank.
    public private(set) var currentView: ViewSpec = .idle
    /// `say` from the most recent Action.
    public private(set) var lastSay: String?
    public private(set) var connection: ConnectionState = .searching
    public private(set) var endpoint: HubEndpoint?
    /// The idle screen's forecast, kept fresh by `idleModel`.
    public var idleInfo: IdleInfo? { idleModel.info }
    /// The latest event from the Hub; later features (timers, now playing) consume these.
    public private(set) var lastEvent: Event?
    public private(set) var settings: HubSettings
    /// The most recent failed request, for showing a hint; cleared by the next success.
    public private(set) var lastError: HubError?

    /// Timers mirrored from the Hub, rung locally (#18).
    public let timerStore: TimerStore
    /// Periodic `/v1/idle` refresh (#16).
    public let idleModel: IdleModel
    /// What's playing, media and volume commands (#33, #35, #39).
    public let media: MediaCenter
    /// Dim / dark on the Hub's night schedule (#24).
    public let night: NightModeController

    @ObservationIgnored private let config: any HubConfigStore
    @ObservationIgnored private let discovery: any HubDiscovery
    @ObservationIgnored private let probe: any HubProbe
    /// The pinned session for everything that carries the token (#54).
    @ObservationIgnored public let hubSession: HubSession
    @ObservationIgnored private let sleeper: any Sleeper
    @ObservationIgnored private let makeClient: @Sendable (HubEndpoint) -> any HubAPI
    @ObservationIgnored private let makeEvents:
        @Sendable (HubEndpoint) -> AsyncStream<EventStreamUpdate>
    @ObservationIgnored private(set) var client: (any HubAPI)?
    @ObservationIgnored private var runTask: Task<Void, Never>?

    public init(
        config: any HubConfigStore,
        discovery: any HubDiscovery,
        probe: any HubProbe,
        hubSession: HubSession = HubSession(),
        sleeper: any Sleeper = TaskSleeper(),
        makeClient: @escaping @Sendable (HubEndpoint) -> any HubAPI,
        makeEvents: @escaping @Sendable (HubEndpoint) -> AsyncStream<EventStreamUpdate>,
        timerStore: TimerStore? = nil,
        idleModel: IdleModel? = nil,
        media: MediaCenter? = nil,
        night: NightModeController? = nil
    ) {
        self.config = config
        self.discovery = discovery
        self.probe = probe
        self.hubSession = hubSession
        self.sleeper = sleeper
        self.makeClient = makeClient
        self.makeEvents = makeEvents
        self.settings = config.load()
        self.timerStore = timerStore ?? TimerStore()
        self.idleModel = idleModel ?? IdleModel()
        self.media = media ?? MediaCenter()
        self.night = night ?? NightModeController(brightness: FakeScreenBrightness())
        self.idleModel.onUpdate = { [weak self] info in self?.night.apply(info) }
        self.timerStore.onTimersChanged = { [weak self] in self?.timersChanged() }
        self.media.onFinished = { [weak self] kind in self?.mediaFinished(kind) }
    }

    // MARK: - Router

    /// Applies an Action's `view`: replaces the screen, `nil` keeps it, unknown types are ignored.
    public func apply(_ action: Action) {
        lastSay = action.say
        // Every sound view plays, even an identical resend ("play it again").
        if case .sound? = action.view { media.soundRequested() }
        switch action.view {
        case nil, .unknown?: break
        case let view?: currentView = view
        }
    }

    /// Shows `view` directly (touch navigation, e.g. back to idle).
    public func show(_ view: ViewSpec) {
        if case .unknown = view { return }
        currentView = view
    }

    // MARK: - Connection lifecycle

    /// Finds the Hub and keeps the event stream open until `stop()`. Calling again restarts.
    public func start() {
        runTask?.cancel()
        runTask = Task { [weak self] in await self?.run() }
    }

    public func stop() {
        runTask?.cancel()
        runTask = nil
        timerStore.hubDisconnected()
        idleModel.disconnect()
    }

    /// Saves Settings and reconnects with them.
    public func saveSettings(_ settings: HubSettings) throws {
        try config.save(settings)
        self.settings = config.load()
        start()
    }

    /// The person confirmed `candidate`'s fingerprint matches `make hub-fingerprint`: pin it
    /// and connect (only now does the token go to the Hub).
    public func pair(with candidate: PairingCandidate) throws {
        var settings = config.load()
        settings.pin = candidate.pin
        try saveSettings(settings)
    }

    /// "Forget Hub / Re-pair": clears the pin and the token (see `HubSettings.forgettingHub`).
    public func forgetHub() throws {
        try saveSettings(config.load().forgettingHub())
    }

    private func run() async {
        client = nil
        endpoint = nil
        timerStore.hubDisconnected()
        let settings = config.load()
        self.settings = settings
        hubSession.setPin(settings.pin)
        connection = settings.manualHost == nil ? .searching : .connecting
        guard let token = settings.token else {
            connection = .unauthorized
            return
        }
        guard let address = await trustedHub(settings), !Task.isCancelled else { return }
        let endpoint = HubEndpoint(host: address.host, port: address.port, token: token)
        self.endpoint = endpoint
        let client = makeClient(endpoint)
        self.client = client
        connection = .connecting

        // Idle info is best-effort; the event stream decides the connection state.
        idleModel.connect(client)

        for await update in makeEvents(endpoint) {
            if Task.isCancelled { return }
            handle(update)
        }
    }

    /// The address of the paired Hub: the manual host, or the first discovered Hub whose
    /// certificate matches the pin. Sets `connection` and returns `nil` when pairing is needed,
    /// the manual host is someone else, or discovery ends. Never sends the token.
    private func trustedHub(_ settings: HubSettings) async -> HubAddress? {
        let addresses: AsyncStream<HubAddress>
        let manual = settings.manualHost != nil
        if let host = settings.manualHost {
            addresses = AsyncStream { continuation in
                continuation.yield(HubAddress(host: host, port: settings.port))
                continuation.finish()
            }
        } else {
            addresses = discovery.browse()
        }
        var sawUntrusted = false
        for await address in addresses {
            var backoff = Backoff()
            // An unreachable address is retried (the Hub may be starting); a definite answer
            // (trusted, untrusted, unpaired) ends the retries.
            while !Task.isCancelled {
                let probed = await probe.identify(address)
                if Task.isCancelled { return nil }
                switch HubAssessment.assess(probed, pin: settings.pin) {
                case .trusted:
                    return address
                case .needsPairing(let presented):
                    connection = .needsPairing(PairingCandidate(address: address, pin: presented))
                    return nil
                case .untrusted:
                    connection = .untrustedHub(host: address.host)
                    sawUntrusted = true
                    if manual { return nil }
                case .unreachable(let reason):
                    connection = .unreachable(reason: reason)
                    let delay = backoff.nextDelay()
                    do { try await sleeper.sleep(for: delay) } catch { return nil }
                    continue
                }
                break
            }
        }
        if !Task.isCancelled && !sawUntrusted {
            connection = .unreachable(reason: "No Coco Hub found on this network.")
        }
        return nil
    }

    private func handle(_ update: EventStreamUpdate) {
        switch update {
        case .connecting(let attempt):
            if attempt == 1 { connection = .connecting }
        case .connected:
            connection = .connected
            // Queued touch-timer work is sent after the `hello` that follows.
            if let client {
                timerStore.hubConnected(client)
                idleModel.reconnected(client)
            }
        case .event(let event):
            lastEvent = event
            timerStore.apply(event)
            mediaEventReceived(event)
            if case .settingsChanged(let schedule) = event.kind { night.update(night: schedule) }
        case .waiting(_, let reason):
            connection = .unreachable(reason: reason)
            timerStore.hubDisconnected()
        case .unauthorized:
            connection = .unauthorized
            timerStore.hubDisconnected()
        case .untrustedHub(let host):
            connection = .untrustedHub(host: host)
            timerStore.hubDisconnected()
        }
    }

    // MARK: - Talking to the Hub

    /// Sends what was heard (or tapped) and applies the reply. Errors set `lastError`, and
    /// auth/reachability errors also update `connection`. `mediaPlaying` overrides
    /// `context.media_playing` (the Now Playing controls always target music).
    public func submit(
        text: String, source: UtteranceRequest.Source, mediaPlaying override: MediaTarget? = nil
    ) async {
        guard let client else { return }
        let request = UtteranceRequest(
            text: text, source: source,
            context: UtteranceContext(
                view: currentView.viewType ?? .idle, alarmRinging: alarmRinging,
                mediaPlaying: override ?? mediaPlaying))
        do throws(HubError) {
            apply(try await client.postUtterance(request))
            lastError = nil
        } catch {
            lastError = error
            switch error {
            case .unauthorized: connection = .unauthorized
            case .unreachable(let reason): connection = .unreachable(reason: reason)
            case .untrustedHub(let host): connection = .untrustedHub(host: host)
            case .server, .decoding: break
            }
        }
    }
}

// MARK: - Timers (#18)

extension AppModel {
    /// The timers screen goes back to idle once no timers remain.
    func timersChanged() {
        if currentView == .timers, timerStore.timers.isEmpty { currentView = .idle }
        media.alarmChanged(ringing: timerStore.isAlarmRinging)
    }

    /// Touch "+1 min". Local-only timers change here; Hub timers change through the Hub. The
    /// protocol has no add-time endpoint, so this sends the equivalent touch utterance.
    public func addTime(to timer: DisplayTimer, seconds: Int) async {
        if timer.isLocalOnly {
            timerStore.addTimeLocally(id: timer.id, seconds: seconds)
        } else {
            await submit(text: Self.addTimePhrase(for: timer, seconds: seconds), source: .touch)
        }
    }

    /// e.g. "add 1 minute to the pasta timer".
    public static func addTimePhrase(for timer: DisplayTimer, seconds: Int) -> String {
        let minutes = max(1, seconds / 60)
        let amount = minutes == 1 ? "1 minute" : "\(minutes) minutes"
        let target = timer.name.map { "the \($0) timer" } ?? "the timer"
        return "add \(amount) to \(target)"
    }
}

extension AppModel {
    /// The app's real wiring: Keychain + UserDefaults, Bonjour, and one pinned URLSession for
    /// the client, the event stream and Hub media.
    public static func live() -> AppModel {
        let session = HubSession()
        return AppModel(
            config: UserDefaultsHubConfigStore(),
            discovery: BonjourHubDiscovery(),
            probe: HealthzHubProbe(),
            hubSession: session,
            makeClient: { session.client(for: $0) },
            makeEvents: { session.events(for: $0).updates() },
            timerStore: TimerStore(
                persistence: FileTimerPersistence.standard(),
                scheduler: LocalAlarmScheduler.live(),
                player: AudioAlarmPlayer()),
            night: NightModeController(brightness: liveScreenBrightness()))
    }

    private static func liveScreenBrightness() -> any ScreenBrightness {
        #if os(iOS)
            UIKitScreenBrightness()
        #else
            FakeScreenBrightness()
        #endif
    }
}

extension ViewSpec {
    /// The protocol `ViewType`, or `nil` for unknown views.
    public var viewType: ViewType? { ViewType(rawValue: type) }
}
