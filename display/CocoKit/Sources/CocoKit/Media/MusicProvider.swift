import Foundation

// MARK: - Music on the iPad itself (#42)

/// Music Coco plays on the iPad (Apple Music via MusicKit), as opposed to Spotify, which the
/// Hub drives in the Spotify app. The Hub picks with `music.provider`; with `apple_music` it
/// sends `music.play` and `media.*` commands with target `music`, which `MediaCenter` routes
/// here.
@MainActor
public protocol MusicProvider: MediaPlayerControl {
    /// Searches for `query` (as `kind`) and plays the best match; `query` "" resumes.
    func play(query: String, kind: MusicSearchKind) async throws(MusicProviderError)
    func skip()
    /// Called with the provider's now-playing whenever it changes.
    var onNowPlayingChanged: (@MainActor (NowPlaying?) -> Void)? { get set }
}

public enum MusicProviderError: Error, Sendable, Equatable {
    case notAuthorized
    case noSubscription
    case notFound
    case nothingToResume
    case failed(String)

    /// Shown on the Now Playing screen (the Hub has already said "Playing X").
    public var say: String {
        switch self {
        case .notAuthorized: "Coco isn't allowed to use Apple Music. Allow it in Settings."
        case .noSubscription: "Apple Music needs a subscription for that."
        case .notFound: "I couldn't find that on Apple Music."
        case .nothingToResume: "Tell me what to play on Apple Music."
        case .failed: "Apple Music didn't start."
        }
    }
}

// MARK: - The MusicKit seam

/// A catalog result, without MusicKit types (so the logic tests run on macOS).
public struct MusicCatalogItem: Sendable, Equatable {
    public enum Kind: String, Sendable, Equatable, CaseIterable {
        case artist, song, album, playlist
    }

    public var id: String
    public var kind: Kind
    public var title: String
    public var artist: String?

    public init(id: String, kind: Kind, title: String, artist: String? = nil) {
        self.id = id
        self.kind = kind
        self.title = title
        self.artist = artist
    }
}

public enum MusicAccess: Sendable, Equatable {
    case authorized, denied, noSubscription
}

/// Everything `AppleMusicProvider` needs from MusicKit. `LiveMusicKit` is the real one (iOS);
/// tests use a fake.
@MainActor
public protocol MusicKitBackend: AnyObject {
    /// Asks for Apple Music access the first time; checks the subscription can play.
    func access() async -> MusicAccess
    /// `MusicCatalogSearchRequest` for `kinds`, results in catalog order.
    func search(term: String, kinds: [MusicCatalogItem.Kind]) async throws -> [MusicCatalogItem]
    /// Queues `item` (an artist: its top songs) on `ApplicationMusicPlayer` and plays.
    func play(_ item: MusicCatalogItem) async throws
    func resume() async throws
    func pause()
    func skipToNext() async throws
    func stop()
    /// The player's current entry and state.
    var nowPlaying: NowPlaying? { get }
    /// The backend calls this when the player's state or queue changes.
    var onChange: (@MainActor () -> Void)? { get set }
}

// MARK: - Apple Music

/// `MusicProvider` on MusicKit's `ApplicationMusicPlayer`. The logic (which catalog types a
/// kind searches, which result wins, which call a command makes) is here; MusicKit itself is
/// behind `MusicKitBackend`.
///
/// Volume: MusicKit has no app-level gain, so `setGain` does nothing; `volume.*` and ducking
/// don't change Apple Music (the iPad's own volume does).
@MainActor
public final class AppleMusicProvider: MusicProvider {
    public var onNowPlayingChanged: (@MainActor (NowPlaying?) -> Void)?
    /// True once something was queued; until then transport commands are ignored, so a
    /// Spotify household's "pause the music" never touches Apple Music.
    public private(set) var hasQueue = false

    private let backend: any MusicKitBackend

    public init(backend: any MusicKitBackend) {
        self.backend = backend
        backend.onChange = { [weak self] in self?.publishNowPlaying() }
    }

    /// Catalog types searched for a `music.play` kind.
    public static func catalogKinds(for kind: MusicSearchKind) -> [MusicCatalogItem.Kind] {
        switch kind {
        case .artist: [.artist]
        case .album: [.album]
        case .playlist: [.playlist]
        case .song: [.song]
        case .any: [.artist, .song, .album, .playlist]
        }
    }

    /// Best first, like the Hub's Spotify ranking: exact name > every query word in the name >
    /// the rest; then artist > song > album > playlist; then catalog order.
    public static func rank(_ items: [MusicCatalogItem], query: String) -> [MusicCatalogItem] {
        let wanted = words(query)
        func tier(_ item: MusicCatalogItem) -> Int {
            let name = words(item.title)
            if name == wanted { return 2 }
            if !wanted.isEmpty && Set(wanted).isSubset(of: Set(name)) { return 1 }
            return 0
        }
        let order = MusicCatalogItem.Kind.allCases
        return items.enumerated().sorted { a, b in
            let (ta, tb) = (tier(a.element), tier(b.element))
            if ta != tb { return ta > tb }
            let (ka, kb) = (order.firstIndex(of: a.element.kind)!, order.firstIndex(of: b.element.kind)!)
            if ka != kb { return ka < kb }
            return a.offset < b.offset
        }.map(\.element)
    }

    static func words(_ text: String) -> [String] {
        let folded = text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
        let words = folded.split { !$0.isLetter && !$0.isNumber }.map(String.init)
        return words.first == "the" && words.count > 1 ? Array(words.dropFirst()) : words
    }

    // MARK: MusicProvider

    public func play(query: String, kind: MusicSearchKind) async throws(MusicProviderError) {
        switch await backend.access() {
        case .authorized: break
        case .denied: throw .notAuthorized
        case .noSubscription: throw .noSubscription
        }
        do {
            if query.isEmpty {
                guard hasQueue else { throw MusicProviderError.nothingToResume }
                try await backend.resume()
            } else {
                let results = try await backend.search(term: query, kinds: Self.catalogKinds(for: kind))
                guard let best = Self.rank(results, query: query).first else {
                    throw MusicProviderError.notFound
                }
                try await backend.play(best)
                hasQueue = true
            }
        } catch let error as MusicProviderError {
            throw error
        } catch {
            throw .failed(String(describing: error))
        }
        publishNowPlaying()
    }

    public func pause() {
        guard hasQueue else { return }
        backend.pause()
        publishNowPlaying()
    }

    public func resume() {
        guard hasQueue else { return }
        Task { try? await backend.resume(); publishNowPlaying() }
    }

    public func skip() {
        guard hasQueue else { return }
        Task { try? await backend.skipToNext(); publishNowPlaying() }
    }

    public func stop() {
        guard hasQueue else { return }
        backend.stop()
        publishNowPlaying()
    }

    public func setGain(_ gain: Double) {}

    private func publishNowPlaying() {
        onNowPlayingChanged?(hasQueue ? backend.nowPlaying : nil)
    }
}

// MARK: - Fake (tests, previews, macOS)

/// A scripted `MusicKitBackend`: records calls and returns `results`.
@MainActor
public final class FakeMusicKit: MusicKitBackend {
    public var accessResult: MusicAccess = .authorized
    public var results: [MusicCatalogItem] = []
    public var failure: (any Error)?
    public private(set) var calls: [String] = []
    public private(set) var searches: [(term: String, kinds: [MusicCatalogItem.Kind])] = []
    public var nowPlaying: NowPlaying?
    public var onChange: (@MainActor () -> Void)?

    public init() {}

    public func access() async -> MusicAccess { accessResult }

    public func search(term: String, kinds: [MusicCatalogItem.Kind]) async throws -> [MusicCatalogItem] {
        searches.append((term, kinds))
        if let failure { throw failure }
        return results.filter { kinds.contains($0.kind) }
    }

    public func play(_ item: MusicCatalogItem) async throws {
        calls.append("play \(item.kind.rawValue):\(item.id)")
        if let failure { throw failure }
        nowPlaying = NowPlaying(
            source: .appleMusic, title: item.title, artist: item.artist ?? item.title, album: nil,
            artworkURL: nil, isPlaying: true)
    }

    public func resume() async throws {
        calls.append("resume")
        nowPlaying?.isPlaying = true
    }

    public func pause() {
        calls.append("pause")
        nowPlaying?.isPlaying = false
    }

    public func skipToNext() async throws { calls.append("skip") }

    public func stop() {
        calls.append("stop")
        nowPlaying = nil
    }

    /// Simulates MusicKit reporting a change (e.g. the next song started).
    public func changed() { onChange?() }
}
