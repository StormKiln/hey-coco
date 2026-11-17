import Foundation

// MARK: - Media (#33, #35, #39)

extension AppModel {
    /// Loads Hub media (with the token, pinned) and public pictures; `nil` until a Hub is found.
    public var mediaLoader: HubMediaLoader? {
        endpoint.map { hubSession.mediaLoader(for: $0) }
    }

    /// A finished video leaves its screen; a finished sound clip waits on the Sound screen's own
    /// countdown.
    func mediaFinished(_ kind: MediaTarget) {
        if kind == .youtube, case .youtube = currentView { show(.idle) }
    }

    /// The Hub's music state, so "pause" can target music when nothing plays locally; and the
    /// Hub's request to open Spotify (#5), which `spotifyWaker` ignores under Guided Access.
    func mediaEventReceived(_ event: Event) {
        switch event.kind {
        case .hello(_, let nowPlaying), .nowPlaying(let nowPlaying):
            media.nowPlayingChanged(nowPlaying)
        case .spotifyWakeRequested(let request):
            spotifyWaker?.wakeRequested(request)
        default:
            break
        }
    }

    /// A URL the app was opened with: Spotify's redirect after an SDK wake (#116). Returns
    /// false for anything else (a `cocohub://` pairing link isn't registered as a URL type;
    /// pairing links come from the QR scanner).
    @discardableResult
    public func handleOpenURL(_ url: URL) -> Bool {
        switch IncomingURL(url) {
        case .spotifyCallback: spotifyWaker?.handleOpenURL(url) ?? false
        case .pairing, .unknown: false
        }
    }
}
