import Foundation
import Testing
@testable import CocoKit

/// An AppModel that never connects (for command / event handling tests).
@MainActor
func makeMusicTestModel(media: MediaCenter = MediaCenter()) -> AppModel {
    let config = UserDefaultsHubConfigStore(
        defaults: UserDefaults(suiteName: "coco-music-\(UUID().uuidString)")!,
        tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
    return AppModel(
        config: config, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
        makeClient: { _ in FakeHubAPI() },
        makeEvents: { _ in AsyncStream { $0.finish() } },
        media: media)
}

private let raffiArtist = MusicCatalogItem(id: "a1", kind: .artist, title: "Raffi")
private let raffiTribute = MusicCatalogItem(id: "a2", kind: .artist, title: "Raffi Tribute Band")
private let bananaphone = MusicCatalogItem(id: "s1", kind: .song, title: "Bananaphone", artist: "Raffi")
private let raffiSong = MusicCatalogItem(id: "s2", kind: .song, title: "Raffi", artist: "Someone")
private let frozen = MusicCatalogItem(
    id: "al1", kind: .album, title: "Frozen (Original Motion Picture Soundtrack)", artist: "Various")
private let dinner = MusicCatalogItem(id: "p1", kind: .playlist, title: "Dinner Jazz")

@MainActor
@Suite struct AppleMusicProviderTests {
    // MARK: Search kinds

    @Test(arguments: [
        (MusicSearchKind.artist, [MusicCatalogItem.Kind.artist]),
        (.album, [.album]),
        (.playlist, [.playlist]),
        (.song, [.song]),
        (.any, [.artist, .song, .album, .playlist]),
    ])
    func kindMapsToCatalogTypes(kind: MusicSearchKind, expected: [MusicCatalogItem.Kind]) {
        #expect(AppleMusicProvider.catalogKinds(for: kind) == expected)
    }

    @Test func playSearchesTheMappedKindsAndPlaysTheBestMatch() async throws {
        let kit = FakeMusicKit()
        kit.results = [bananaphone, frozen, raffiTribute, raffiArtist]
        let provider = AppleMusicProvider(backend: kit)
        try await provider.play(query: "raffi", kind: .any)
        #expect(kit.searches.map(\.term) == ["raffi"])
        #expect(kit.searches[0].kinds == [.artist, .song, .album, .playlist])
        #expect(kit.calls == ["play artist:a1"])
        #expect(provider.hasQueue)

        try await provider.play(query: "frozen soundtrack", kind: .album)
        #expect(kit.searches[1].kinds == [.album])
        #expect(kit.calls.last == "play album:al1")
    }

    // MARK: Ranking

    @Test func exactNameBeatsAllWordsBeatsTheRestThenKindOrder() {
        let ranked = AppleMusicProvider.rank([bananaphone, raffiTribute, raffiSong, raffiArtist], query: "raffi")
        // Exact "Raffi": artist before song; then every word ("Raffi Tribute Band"); then the rest.
        #expect(ranked.map(\.id) == ["a1", "s2", "a2", "s1"])
    }

    @Test func rankIgnoresCaseAccentsAndALeadingThe() {
        let beatles = MusicCatalogItem(id: "b", kind: .artist, title: "The Beatles")
        let cafe = MusicCatalogItem(id: "c", kind: .song, title: "Café Del Mar")
        #expect(AppleMusicProvider.rank([cafe, beatles], query: "beatles").first == beatles)
        #expect(AppleMusicProvider.rank([beatles, cafe], query: "cafe del mar").first == cafe)
    }

    @Test func playlistOnlyWhenAskedFor() async throws {
        let kit = FakeMusicKit()
        kit.results = [dinner, bananaphone]
        let provider = AppleMusicProvider(backend: kit)
        try await provider.play(query: "dinner", kind: .playlist)
        #expect(kit.calls == ["play playlist:p1"])
    }

    // MARK: Failures

    @Test func noResultsIsNotFound() async {
        let kit = FakeMusicKit()
        let provider = AppleMusicProvider(backend: kit)
        await #expect(throws: MusicProviderError.notFound) {
            try await provider.play(query: "zzqqxx", kind: .any)
        }
        #expect(kit.calls.isEmpty)
    }

    @Test func deniedAndNoSubscriptionNeverSearch() async {
        let kit = FakeMusicKit()
        kit.results = [raffiArtist]
        let provider = AppleMusicProvider(backend: kit)
        kit.accessResult = .denied
        await #expect(throws: MusicProviderError.notAuthorized) {
            try await provider.play(query: "raffi", kind: .any)
        }
        kit.accessResult = .noSubscription
        await #expect(throws: MusicProviderError.noSubscription) {
            try await provider.play(query: "raffi", kind: .any)
        }
        #expect(kit.searches.isEmpty)
    }

    @Test func musicKitErrorsBecomeFailed() async {
        struct Boom: Error {}
        let kit = FakeMusicKit()
        kit.failure = Boom()
        let provider = AppleMusicProvider(backend: kit)
        await #expect(throws: MusicProviderError.failed("Boom()")) {
            try await provider.play(query: "raffi", kind: .any)
        }
    }

    @Test func emptyQueryResumesOnlyAfterSomethingWasPlayed() async throws {
        let kit = FakeMusicKit()
        kit.results = [raffiArtist]
        let provider = AppleMusicProvider(backend: kit)
        await #expect(throws: MusicProviderError.nothingToResume) {
            try await provider.play(query: "", kind: .any)
        }
        try await provider.play(query: "raffi", kind: .any)
        provider.pause()
        try await provider.play(query: "", kind: .any)
        #expect(kit.calls == ["play artist:a1", "pause", "resume"])
    }

    // MARK: Transport

    @Test func transportIsIgnoredUntilSomethingWasQueued() async throws {
        let kit = FakeMusicKit()
        kit.results = [raffiArtist]
        let provider = AppleMusicProvider(backend: kit)
        provider.pause()
        provider.stop()
        provider.skip()
        provider.resume()
        await Task.yield()
        #expect(kit.calls.isEmpty)

        try await provider.play(query: "raffi", kind: .any)
        provider.pause()
        provider.skip()
        provider.resume()
        provider.stop()
        #expect(await waitUntil { kit.calls.count == 5 })
        #expect(Set(kit.calls) == ["play artist:a1", "pause", "skip", "resume", "stop"])
    }

    @Test func nowPlayingIsReportedAsAppleMusic() async throws {
        let kit = FakeMusicKit()
        kit.results = [bananaphone]
        let provider = AppleMusicProvider(backend: kit)
        var reported: [NowPlaying?] = []
        provider.onNowPlayingChanged = { reported.append($0) }
        try await provider.play(query: "bananaphone", kind: .song)
        #expect(reported.last??.source == .appleMusic)
        #expect(reported.last??.title == "Bananaphone")
        kit.nowPlaying?.title = "Down by the Bay"
        kit.changed()
        #expect(reported.last??.title == "Down by the Bay")
    }
}

// MARK: - MediaCenter + AppModel routing

@MainActor
@Suite struct AppleMusicRoutingTests {
    private func setUp() -> (AppModel, FakeMusicKit) {
        let kit = FakeMusicKit()
        kit.results = [raffiArtist, bananaphone]
        let model = makeMusicTestModel()
        model.media.register(music: AppleMusicProvider(backend: kit))
        return (model, kit)
    }

    @Test func musicPlayCommandPlaysLocallyAndShowsNowPlaying() async {
        let (model, kit) = setUp()
        model.perform(.musicPlay(query: "raffi", kind: .artist))
        #expect(await waitUntil { model.nowPlaying?.title == "Raffi" })
        #expect(kit.calls == ["play artist:a1"])
        #expect(model.nowPlaying?.source == .appleMusic)
        #expect(model.mediaPlaying == .music)
        #expect(model.media.nowPlayingKind == .music)
    }

    @Test func mediaCommandsWithTargetMusicReachAppleMusic() async {
        let (model, kit) = setUp()
        model.perform(.musicPlay(query: "raffi", kind: .any))
        #expect(await waitUntil { kit.calls.count == 1 })
        model.perform(.mediaPause(target: .music))
        #expect(model.nowPlaying?.isPlaying == false)
        #expect(model.media.isPaused)
        model.perform(.mediaNext(target: .music))
        model.perform(.mediaResume(target: .music))
        #expect(await waitUntil { kit.calls.count == 4 })
        model.perform(.mediaStop(target: .music))
        #expect(kit.calls.last == "stop")
        #expect(model.nowPlaying == nil)
        #expect(model.mediaPlaying == nil)
    }

    @Test func aFailedSearchLeavesANotice() async {
        let (model, kit) = setUp()
        kit.results = []
        model.perform(.musicPlay(query: "zzqqxx", kind: .any))
        #expect(await waitUntil { model.media.musicNotice != nil })
        #expect(model.media.musicNotice == "I couldn't find that on Apple Music.")
    }

    @Test func withoutAProviderMusicPlayLeavesANotice() async {
        let model = makeMusicTestModel()
        model.perform(.musicPlay(query: "raffi", kind: .any))
        #expect(await waitUntil { model.media.musicNotice != nil })
    }

    @Test func spotifyMusicCommandsDoNotTouchAnIdleAppleMusicPlayer() {
        let (model, kit) = setUp()
        model.media.nowPlayingChanged(
            NowPlaying(source: .spotify, title: "X", artist: "Y", album: nil, artworkURL: nil, isPlaying: true))
        model.perform(.mediaPause(target: .music))
        #expect(kit.calls.isEmpty)
        #expect(model.nowPlaying?.source == .spotify)
    }

    @Test func aRingingAlarmPausesAppleMusic() async {
        let (model, kit) = setUp()
        model.perform(.musicPlay(query: "raffi", kind: .any))
        #expect(await waitUntil { kit.calls.count == 1 })
        model.media.alarmChanged(ringing: true)
        #expect(kit.calls.last == "pause")
        model.media.alarmChanged(ringing: false)
        #expect(await waitUntil { kit.calls.last == "resume" })
    }
}
