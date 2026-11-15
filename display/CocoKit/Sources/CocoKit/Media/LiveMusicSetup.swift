#if os(iOS)
    import UIKit

    extension AppModel {
        /// The app's real music wiring: Apple Music through MusicKit (#42, used only when the
        /// Hub's `music.provider` is `apple_music`) and the Spotify wake handler (#5), which
        /// opens the `spotify:` scheme only outside Guided Access.
        public func installLiveMusic() {
            #if canImport(MusicKit)
                media.register(music: AppleMusicProvider(backend: LiveMusicKit()))
            #endif
            spotifyWaker = SpotifyWaker(
                isGuidedAccessEnabled: { UIAccessibility.isGuidedAccessEnabled },
                open: { UIApplication.shared.open($0) })
        }
    }
#endif
