import Foundation
import os

// MARK: - Spotify wake (#5, #116)

/// The Hub's `spotify.wake_requested`: Spotify doesn't list the iPad, so open the Spotify app.
/// With `play`, the Hub is about to play `uri` (nil: whatever Spotify last played) and sends
/// the Spotify app's `clientID` so the Display can wake Spotify through the Spotify iOS SDK.
public struct SpotifyWakeRequest: Sendable, Equatable {
    public var deviceName: String?
    public var play: Bool
    public var uri: String?
    public var clientID: String?

    public init(deviceName: String? = nil, play: Bool = false, uri: String? = nil, clientID: String? = nil) {
        self.deviceName = deviceName
        self.play = play
        self.uri = uri
        self.clientID = clientID
    }
}

/// What the Display does with the Hub's `spotify.wake_requested`.
///
/// Spotify drops an idle iPad from its Connect device list; only the Spotify app coming to the
/// foreground brings it back. **Not under Guided Access** (iOS keeps the locked app in front):
/// then the Display does nothing and the Hub, after its wait, says "Open Spotify on the iPad
/// once, then try again."
///
/// Otherwise, for a play request, the Spotify iOS SDK (`SPTAppRemote.authorizeAndPlayURI`)
/// opens Spotify, starts the URI and redirects back to Coco (`redirectURL`), so Coco is in
/// front again and keeps listening (#116). Without the SDK (not configured, Spotify not
/// reachable through it, or a recent SDK failure) it opens the plain `spotify:` scheme, which
/// leaves Spotify in front.
public enum SpotifyWakeDecision: Sendable, Equatable {
    /// Wake through the Spotify iOS SDK: plays `playURI` ("" = the last song) and comes back.
    case appRemote(clientID: String, playURI: String)
    /// Open the plain `spotify:` scheme (Spotify stays in front).
    case open(URL, because: Fallback)
    case ignore(Reason)

    public enum Reason: Sendable, Equatable {
        /// Guided Access is on: opening another app can't work.
        case guidedAccess
        /// Opened Spotify moments ago; don't bounce the person out of Coco again.
        case tooSoon
    }

    /// Why the plain scheme instead of the SDK.
    public enum Fallback: Sendable, Equatable {
        /// Not a play request (e.g. a volume change): the SDK always starts music.
        case notPlaying
        /// The Hub sent no Spotify client ID.
        case noClientID
        /// The URI isn't a `spotify:` URI.
        case invalidURI
        /// The app was built without the Spotify iOS SDK.
        case sdkUnavailable
        /// The SDK failed within `SpotifyWake.appRemoteRetryInterval`.
        case sdkFailedRecently
    }
}

public enum SpotifyWake {
    public static let url = URL(string: "spotify:")!
    /// Where Spotify sends the person back after `authorizeAndPlayURI`. Registered as a URL type
    /// in display/project.yml and as a redirect URI on the Spotify app (docs/spotify.md).
    public static let callbackScheme = "cocokitchen-spotify"
    public static let redirectURL = URL(string: "\(callbackScheme)://callback")!
    /// At most one switch to Spotify per this long, however often the Hub asks.
    public static let minInterval: TimeInterval = 60
    /// After the SDK fails, use the plain scheme for this long before trying the SDK again.
    public static let appRemoteRetryInterval: TimeInterval = 60 * 60

    public static func decide(
        _ request: SpotifyWakeRequest, guidedAccess: Bool, lastOpened: Date?, now: Date,
        appRemoteAvailable: Bool, lastAppRemoteFailure: Date? = nil
    ) -> SpotifyWakeDecision {
        if guidedAccess { return .ignore(.guidedAccess) }
        if let lastOpened, now.timeIntervalSince(lastOpened) < minInterval {
            return .ignore(.tooSoon)
        }
        guard request.play else { return .open(url, because: .notPlaying) }
        guard appRemoteAvailable else { return .open(url, because: .sdkUnavailable) }
        guard let clientID = request.clientID?.trimmingCharacters(in: .whitespaces), !clientID.isEmpty
        else { return .open(url, because: .noClientID) }
        let uri = request.uri ?? ""
        guard uri.isEmpty || uri.hasPrefix("spotify:") else { return .open(url, because: .invalidURI) }
        if let lastAppRemoteFailure, now.timeIntervalSince(lastAppRemoteFailure) < appRemoteRetryInterval {
            return .open(url, because: .sdkFailedRecently)
        }
        return .appRemote(clientID: clientID, playURI: uri)
    }

    /// Spotify's redirect back to Coco after `authorizeAndPlayURI`.
    public static func isCallback(_ url: URL) -> Bool {
        url.scheme?.lowercased() == callbackScheme && url.host?.lowercased() == "callback"
    }
}

/// A URL the app was opened with (`onOpenURL`).
public enum IncomingURL: Sendable, Equatable {
    /// Spotify coming back after the SDK wake (#116).
    case spotifyCallback
    /// A `cocohub://pair` link (#89). Only scanned from the QR code today; the scheme isn't
    /// registered as a URL type, so iOS doesn't open Coco with it.
    case pairing
    case unknown

    public init(_ url: URL) {
        if SpotifyWake.isCallback(url) {
            self = .spotifyCallback
        } else if url.scheme?.lowercased() == PairingURI.scheme {
            self = .pairing
        } else {
            self = .unknown
        }
    }
}

/// What Spotify's redirect said.
public enum SpotifyAuthorizationResult: Sendable, Equatable {
    case authorized
    case failed(String)
}

/// The Spotify iOS SDK, as `SpotifyWaker` uses it. The app implements it with `SPTAppRemote`
/// (display/App/Spotify); tests use a fake. CocoKit itself never links the SDK.
@MainActor
public protocol SpotifyAppRemoting: AnyObject {
    /// Open Spotify to authorize Coco and play `uri` ("" = the last song); Spotify then
    /// redirects to `redirectURL`. `completion(false)`: Spotify can't be reached that way
    /// (not installed, or the SDK refused).
    func authorizeAndPlay(
        clientID: String, uri: String, redirectURL: URL,
        completion: @escaping @MainActor @Sendable (Bool) -> Void)
    /// Reads Spotify's redirect; nil when this SDK object can't parse it (e.g. Coco was
    /// relaunched while Spotify was in front).
    func authorizationResult(from url: URL) -> SpotifyAuthorizationResult?
}

/// The last thing a wake did, for the diagnostics screen.
public enum SpotifyWakeOutcome: Sendable, Equatable {
    case ignored(SpotifyWakeDecision.Reason)
    case openedScheme(SpotifyWakeDecision.Fallback)
    /// Asked the SDK; waiting for Spotify to send the person back.
    case appRemoteRequested
    /// Spotify sent the person back with a token: the SDK wake worked.
    case appRemoteAuthorized
    /// Spotify sent the person back without a token (SDK 5.x's `unknown_error`,
    /// spotify/ios-sdk#474). Coco is in front again, so it still counts as a working wake;
    /// Spotify may not have started the music, which the Hub then does through the Web API.
    case appRemoteReturnedWithError(String)
    /// Spotify sent the person back, but nothing could read the redirect.
    case appRemoteReturned
    /// The SDK couldn't reach Spotify, or Spotify never sent the person back: the next wakes
    /// use the plain scheme for `SpotifyWake.appRemoteRetryInterval`.
    case appRemoteFailed(String)
}

/// Applies `SpotifyWake.decide` with the device's real state. The app supplies
/// `UIAccessibility.isGuidedAccessEnabled`, `UIApplication.open` and the SDK; tests supply fakes.
@MainActor
public final class SpotifyWaker {
    public private(set) var lastOpened: Date?
    public private(set) var lastDecision: SpotifyWakeDecision?
    public private(set) var lastOutcome: SpotifyWakeOutcome?
    public private(set) var lastAppRemoteFailure: Date?
    /// An SDK wake is waiting for Spotify's redirect.
    private var awaitingRedirect = false

    private static let log = Logger(subsystem: "com.stormkiln.heycoco", category: "spotify-wake")

    private let isGuidedAccessEnabled: @MainActor () -> Bool
    private let open: @MainActor (URL) -> Void
    private let appRemote: (any SpotifyAppRemoting)?
    private let now: @MainActor () -> Date

    public init(
        isGuidedAccessEnabled: @escaping @MainActor () -> Bool,
        open: @escaping @MainActor (URL) -> Void,
        appRemote: (any SpotifyAppRemoting)? = nil,
        now: @escaping @MainActor () -> Date = { Date() }
    ) {
        self.isGuidedAccessEnabled = isGuidedAccessEnabled
        self.open = open
        self.appRemote = appRemote
        self.now = now
    }

    public func wakeRequested(_ request: SpotifyWakeRequest = SpotifyWakeRequest()) {
        let time = now()
        if awaitingRedirect, let lastOpened, time.timeIntervalSince(lastOpened) >= SpotifyWake.minInterval {
            // Spotify kept the screen last time (someone switched back by hand).
            appRemoteFailed("Spotify didn't send Coco back")
        }
        let decision = SpotifyWake.decide(
            request, guidedAccess: isGuidedAccessEnabled(), lastOpened: lastOpened, now: time,
            appRemoteAvailable: appRemote != nil, lastAppRemoteFailure: lastAppRemoteFailure)
        lastDecision = decision
        switch decision {
        case .ignore(let reason):
            record(.ignored(reason))
        case .open(let url, let fallback):
            lastOpened = time
            record(.openedScheme(fallback))
            open(url)
        case .appRemote(let clientID, let uri):
            guard let appRemote else { return }
            lastOpened = time
            awaitingRedirect = true
            record(.appRemoteRequested)
            appRemote.authorizeAndPlay(clientID: clientID, uri: uri, redirectURL: SpotifyWake.redirectURL) {
                [weak self] reachable in
                guard let self, !reachable else { return }
                self.awaitingRedirect = false
                self.appRemoteFailed("Spotify app not reachable through the SDK")
                self.open(SpotifyWake.url)
            }
        }
    }

    /// Spotify's redirect after an SDK wake. Returns false for any other URL.
    @discardableResult
    public func handleOpenURL(_ url: URL) -> Bool {
        guard SpotifyWake.isCallback(url) else { return false }
        awaitingRedirect = false
        switch appRemote?.authorizationResult(from: url) {
        case .authorized: record(.appRemoteAuthorized)
        case .failed(let reason): record(.appRemoteReturnedWithError(reason))
        case nil: record(.appRemoteReturned)
        }
        return true
    }

    private func appRemoteFailed(_ reason: String) {
        lastAppRemoteFailure = now()
        record(.appRemoteFailed(reason))
    }

    private func record(_ outcome: SpotifyWakeOutcome) {
        lastOutcome = outcome
        Self.log.info("Spotify wake: \(String(describing: outcome), privacy: .public)")
    }
}
