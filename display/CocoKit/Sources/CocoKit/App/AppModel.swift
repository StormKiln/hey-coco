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
    public private(set) var idleInfo: IdleInfo?
    /// The latest event from the Hub; later features (timers, now playing) consume these.
    public private(set) var lastEvent: Event?
    public private(set) var settings: HubSettings
    /// The most recent failed request, for showing a hint; cleared by the next success.
    public private(set) var lastError: HubError?

    @ObservationIgnored private let config: any HubConfigStore
    @ObservationIgnored private let discovery: any HubDiscovery
    @ObservationIgnored private let makeClient: @Sendable (HubEndpoint) -> any HubAPI
    @ObservationIgnored private let makeEvents:
        @Sendable (HubEndpoint) -> AsyncStream<EventStreamUpdate>
    @ObservationIgnored private var client: (any HubAPI)?
    @ObservationIgnored private var runTask: Task<Void, Never>?

    public init(
        config: any HubConfigStore,
        discovery: any HubDiscovery,
        makeClient: @escaping @Sendable (HubEndpoint) -> any HubAPI,
        makeEvents: @escaping @Sendable (HubEndpoint) -> AsyncStream<EventStreamUpdate>
    ) {
        self.config = config
        self.discovery = discovery
        self.makeClient = makeClient
        self.makeEvents = makeEvents
        self.settings = config.load()
    }

    // MARK: - Router

    /// Applies an Action's `view`: replaces the screen, `nil` keeps it, unknown types are ignored.
    public func apply(_ action: Action) {
        lastSay = action.say
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
    }

    /// Saves Settings and reconnects with them.
    public func saveSettings(_ settings: HubSettings) throws {
        try config.save(settings)
        self.settings = config.load()
        start()
    }

    private func run() async {
        client = nil
        endpoint = nil
        let settings = config.load()
        self.settings = settings
        connection = settings.manualHost == nil ? .searching : .connecting
        guard settings.token != nil else {
            connection = .unauthorized
            return
        }
        guard let endpoint = await HubEndpoint.resolve(settings: settings, discovery: discovery),
            !Task.isCancelled
        else {
            if !Task.isCancelled {
                connection = .unreachable(reason: "No Coco Hub found on this network.")
            }
            return
        }
        self.endpoint = endpoint
        let client = makeClient(endpoint)
        self.client = client
        connection = .connecting

        // Idle info is best-effort; the event stream decides the connection state.
        Task { [weak self] in
            if let idle = try? await client.idle() { self?.idleInfo = idle }
        }

        for await update in makeEvents(endpoint) {
            if Task.isCancelled { return }
            handle(update)
        }
    }

    private func handle(_ update: EventStreamUpdate) {
        switch update {
        case .connecting(let attempt):
            if attempt == 1 { connection = .connecting }
        case .connected:
            connection = .connected
        case .event(let event):
            lastEvent = event
        case .waiting(_, let reason):
            connection = .unreachable(reason: reason)
        case .unauthorized:
            connection = .unauthorized
        }
    }

    // MARK: - Talking to the Hub

    /// Sends what was heard (or tapped) and applies the reply. Errors set `lastError`, and
    /// auth/reachability errors also update `connection`.
    public func submit(text: String, source: UtteranceRequest.Source) async {
        guard let client else { return }
        let request = UtteranceRequest(
            text: text, source: source,
            context: UtteranceContext(
                view: currentView.viewType ?? .idle, alarmRinging: false, mediaPlaying: nil))
        do throws(HubError) {
            apply(try await client.postUtterance(request))
            lastError = nil
        } catch {
            lastError = error
            switch error {
            case .unauthorized: connection = .unauthorized
            case .unreachable(let reason): connection = .unreachable(reason: reason)
            case .server, .decoding: break
            }
        }
    }
}

extension AppModel {
    /// The app's real wiring: Keychain + UserDefaults, Bonjour, URLSession.
    public static func live() -> AppModel {
        AppModel(
            config: UserDefaultsHubConfigStore(),
            discovery: BonjourHubDiscovery(),
            makeClient: { HubClient(endpoint: $0) },
            makeEvents: { EventStream(url: $0.eventsURL).updates() })
    }
}

extension ViewSpec {
    /// The protocol `ViewType`, or `nil` for unknown views.
    public var viewType: ViewType? { ViewType(rawValue: type) }
}
