import Foundation

// MARK: - Now Playing (#41)

/// A touch control on the Now Playing screen. The Hub owns Spotify / Apple Music, so each one is
/// sent as a touch utterance its media intents understand, with `media_playing: music`.
public enum NowPlayingControl: String, Sendable, CaseIterable {
    case pause, resume, next

    public var utterance: String { rawValue }

    /// The play/pause button's action for the current state.
    public static func playPause(for nowPlaying: NowPlaying?) -> NowPlayingControl {
        nowPlaying?.isPlaying == true ? .pause : .resume
    }
}

/// When the Now Playing screen and the idle strip appear.
public enum NowPlayingRules {
    /// The Now Playing screen goes back to idle after this long with nothing playing.
    public static let idleTimeout: Duration = .seconds(30)

    /// The compact strip on the idle screen shows only while music plays.
    public static func showsIdleStrip(_ nowPlaying: NowPlaying?) -> Bool {
        nowPlaying?.isPlaying ?? false
    }

    /// Paused or nothing loaded: the screen starts its countdown back to idle.
    public static func shouldReturnToIdle(_ nowPlaying: NowPlaying?) -> Bool {
        !(nowPlaying?.isPlaying ?? false)
    }

    /// "Title — Artist", for the idle strip.
    public static func stripText(_ nowPlaying: NowPlaying) -> String {
        "\(nowPlaying.title) — \(nowPlaying.artist)"
    }
}

extension AppModel {
    /// What's playing: the Display's own Apple Music (#42), else what the Hub reports for
    /// Spotify (`hello` and `now_playing` events).
    public var nowPlaying: NowPlaying? { media.musicNowPlaying }

    /// Sends a Now Playing touch control to the Hub, which drives Spotify.
    public func send(_ control: NowPlayingControl) async {
        await submit(text: control.utterance, source: .touch, mediaPlaying: .music)
    }
}
