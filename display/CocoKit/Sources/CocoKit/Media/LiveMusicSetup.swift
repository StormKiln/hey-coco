#if os(iOS)
    import UIKit

    extension AppModel {
        /// The app's real music wiring: Apple Music through MusicKit (#42, used only when the
        /// Hub's `music.provider` is `apple_music`) and the Spotify wake handler (#5), which
        /// opens Spotify only outside Guided Access: through the Spotify iOS SDK when the app
        /// passes it (`spotifyAppRemote`, #116), else the plain `spotify:` scheme.
        public func installLiveMusic(spotifyAppRemote: (any SpotifyAppRemoting)? = nil) {
            #if canImport(MusicKit)
                media.register(music: AppleMusicProvider(backend: LiveMusicKit()))
            #endif
            spotifyWaker = SpotifyWaker(
                isGuidedAccessEnabled: { UIAccessibility.isGuidedAccessEnabled },
                open: { UIApplication.shared.open($0) },
                appRemote: spotifyAppRemote)
        }
    }
#endif
