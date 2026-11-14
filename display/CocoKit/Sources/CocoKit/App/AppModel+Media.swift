import Foundation

// MARK: - Media (#33, #35, #39)

extension AppModel {
    /// Loads Hub media (with the token) and public pictures; `nil` until a Hub is found.
    public var mediaLoader: HubMediaLoader? {
        endpoint.map { HubMediaLoader(endpoint: $0) }
    }

    /// A finished video leaves its screen; a finished sound clip waits on the Sound screen's own
    /// countdown.
    func mediaFinished(_ kind: MediaTarget) {
        if kind == .youtube, case .youtube = currentView { show(.idle) }
    }

    /// The Hub's music state, so "pause" can target music when nothing plays locally.
    func mediaEventReceived(_ event: Event) {
        switch event.kind {
        case .hello(_, let nowPlaying), .nowPlaying(let nowPlaying):
            media.nowPlayingChanged(nowPlaying)
        default:
            break
        }
    }
}
