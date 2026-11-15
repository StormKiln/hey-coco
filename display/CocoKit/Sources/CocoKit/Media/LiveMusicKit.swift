#if canImport(MusicKit) && os(iOS)
    import Combine
    import Foundation
    // ApplicationMusicPlayer isn't Sendable but its async methods are nonisolated; every call
    // here is from the main actor on the one shared player.
    @preconcurrency import MusicKit

    /// The real `MusicKitBackend`: Apple Music catalog search and `ApplicationMusicPlayer`
    /// (#42). Needs `NSAppleMusicUsageDescription` (project.yml), the MusicKit service on the
    /// App ID in the developer portal, and an Apple Music subscription on the iPad
    /// (docs/apple-music.md). Not covered by `swift test` (macOS); `AppleMusicProvider`'s logic
    /// is, through `FakeMusicKit`.
    @MainActor
    public final class LiveMusicKit: MusicKitBackend {
        public var onChange: (@MainActor () -> Void)?

        // Touched on first use only, so a Spotify household never wakes MusicKit.
        private lazy var player: ApplicationMusicPlayer = {
            let player = ApplicationMusicPlayer.shared
            // objectWillChange fires before the change: read the new state on the next turn.
            let notify: () -> Void = { [weak self] in
                Task { @MainActor in self?.onChange?() }
            }
            player.state.objectWillChange.sink { _ in notify() }.store(in: &subscriptions)
            player.queue.objectWillChange.sink { _ in notify() }.store(in: &subscriptions)
            return player
        }()
        private var subscriptions: Set<AnyCancellable> = []
        // The MusicKit values behind the last search's results, by "<kind>:<id>".
        private var artists: [String: Artist] = [:]
        private var songs: [String: Song] = [:]
        private var albums: [String: Album] = [:]
        private var playlists: [String: Playlist] = [:]

        public init() {}

        public func access() async -> MusicAccess {
            var status = MusicAuthorization.currentStatus
            if status == .notDetermined { status = await MusicAuthorization.request() }
            guard status == .authorized else { return .denied }
            let subscription = try? await MusicSubscription.current
            return subscription?.canPlayCatalogContent == true ? .authorized : .noSubscription
        }

        public func search(term: String, kinds: [MusicCatalogItem.Kind]) async throws
            -> [MusicCatalogItem]
        {
            var types: [any MusicCatalogSearchable.Type] = []
            for kind in kinds {
                switch kind {
                case .artist: types.append(Artist.self)
                case .song: types.append(Song.self)
                case .album: types.append(Album.self)
                case .playlist: types.append(Playlist.self)
                }
            }
            var request = MusicCatalogSearchRequest(term: term, types: types)
            request.limit = 10
            let response = try await request.response()
            artists = [:]
            songs = [:]
            albums = [:]
            playlists = [:]
            var items: [MusicCatalogItem] = []
            for kind in kinds {
                switch kind {
                case .artist:
                    for artist in response.artists {
                        artists[artist.id.rawValue] = artist
                        items.append(.init(id: artist.id.rawValue, kind: .artist, title: artist.name))
                    }
                case .song:
                    for song in response.songs {
                        songs[song.id.rawValue] = song
                        items.append(
                            .init(id: song.id.rawValue, kind: .song, title: song.title, artist: song.artistName))
                    }
                case .album:
                    for album in response.albums {
                        albums[album.id.rawValue] = album
                        items.append(
                            .init(id: album.id.rawValue, kind: .album, title: album.title, artist: album.artistName))
                    }
                case .playlist:
                    for playlist in response.playlists {
                        playlists[playlist.id.rawValue] = playlist
                        items.append(
                            .init(
                                id: playlist.id.rawValue, kind: .playlist, title: playlist.name,
                                artist: playlist.curatorName))
                    }
                }
            }
            return items
        }

        public func play(_ item: MusicCatalogItem) async throws {
            switch item.kind {
            case .song:
                guard let song = songs[item.id] else { throw LiveMusicKitError.unknownItem }
                player.queue = ApplicationMusicPlayer.Queue(for: [song])
            case .album:
                guard let album = albums[item.id] else { throw LiveMusicKitError.unknownItem }
                player.queue = ApplicationMusicPlayer.Queue(for: [album])
            case .playlist:
                guard let playlist = playlists[item.id] else { throw LiveMusicKitError.unknownItem }
                player.queue = ApplicationMusicPlayer.Queue(for: [playlist])
            case .artist:
                // An artist isn't playable: queue its top songs.
                guard let artist = artists[item.id] else { throw LiveMusicKitError.unknownItem }
                let detailed = try await artist.with([.topSongs])
                guard let top = detailed.topSongs, !top.isEmpty else {
                    throw LiveMusicKitError.nothingPlayable
                }
                player.queue = ApplicationMusicPlayer.Queue(for: top)
            }
            try await player.play()
        }

        public func resume() async throws { try await player.play() }
        public func pause() { player.pause() }
        public func skipToNext() async throws { try await player.skipToNextEntry() }
        public func stop() { player.stop() }

        public var nowPlaying: NowPlaying? {
            guard let entry = player.queue.currentEntry else { return nil }
            var album: String?
            if case .song(let song)? = entry.item { album = song.albumTitle }
            return NowPlaying(
                source: .appleMusic, title: entry.title, artist: entry.subtitle ?? "", album: album,
                artworkURL: entry.artwork?.url(width: 600, height: 600)?.absoluteString,
                isPlaying: player.state.playbackStatus == .playing)
        }
    }

    enum LiveMusicKitError: Error {
        case unknownItem
        case nothingPlayable
    }
#endif
