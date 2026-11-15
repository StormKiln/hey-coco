import Foundation

// MARK: - Spotify wake (#5)

/// What the Display does with the Hub's `spotify.wake_requested`.
///
/// Spotify drops an idle iPad from its Connect device list; only the Spotify app coming to the
/// foreground brings it back. The Display can open the `spotify:` URL scheme, but **not under
/// Guided Access** (iOS keeps the locked app in front), so it then does nothing and the Hub,
/// after its wait, says "Open Spotify on the iPad once, then try again." Even when it works,
/// Spotify stays in front: iOS gives Coco no way to come back by itself. That is why spike #5
/// measures whether the iPad stays listed at all (docs/spikes/spotify-test-procedure.md).
public enum SpotifyWakeDecision: Sendable, Equatable {
    case open(URL)
    case ignore(Reason)

    public enum Reason: Sendable, Equatable {
        /// Guided Access is on: opening another app can't work.
        case guidedAccess
        /// Opened Spotify moments ago; don't bounce the person out of Coco again.
        case tooSoon
    }
}

public enum SpotifyWake {
    public static let url = URL(string: "spotify:")!
    /// At most one switch to Spotify per this long, however often the Hub asks.
    public static let minInterval: TimeInterval = 60

    public static func decide(guidedAccess: Bool, lastOpened: Date?, now: Date) -> SpotifyWakeDecision {
        if guidedAccess { return .ignore(.guidedAccess) }
        if let lastOpened, now.timeIntervalSince(lastOpened) < minInterval {
            return .ignore(.tooSoon)
        }
        return .open(url)
    }
}

/// Applies `SpotifyWake.decide` with the device's real state. The app supplies
/// `UIAccessibility.isGuidedAccessEnabled` and `UIApplication.open`; tests supply fakes.
@MainActor
public final class SpotifyWaker {
    public private(set) var lastOpened: Date?
    public private(set) var lastDecision: SpotifyWakeDecision?

    private let isGuidedAccessEnabled: @MainActor () -> Bool
    private let open: @MainActor (URL) -> Void
    private let now: @MainActor () -> Date

    public init(
        isGuidedAccessEnabled: @escaping @MainActor () -> Bool,
        open: @escaping @MainActor (URL) -> Void,
        now: @escaping @MainActor () -> Date = { Date() }
    ) {
        self.isGuidedAccessEnabled = isGuidedAccessEnabled
        self.open = open
        self.now = now
    }

    public func wakeRequested() {
        let time = now()
        let decision = SpotifyWake.decide(
            guidedAccess: isGuidedAccessEnabled(), lastOpened: lastOpened, now: time)
        lastDecision = decision
        if case .open(let url) = decision {
            lastOpened = time
            open(url)
        }
    }
}
