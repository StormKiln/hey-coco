import Foundation
import Testing
@testable import CocoKit

private func song(playing: Bool = true) -> NowPlaying {
    NowPlaying(
        source: .spotify, title: "Here Comes the Sun", artist: "The Beatles", album: "Abbey Road",
        artworkURL: "https://i.scdn.co/image/abc", isPlaying: playing)
}

@MainActor
@Suite struct NowPlayingStateTests {
    @Test func mediaCenterKeepsTheLatestHubNowPlaying() {
        let media = MediaCenter()
        #expect(media.hubNowPlaying == nil)
        media.nowPlayingChanged(song())
        #expect(media.hubNowPlaying == song())
        #expect(media.hubMusicPlaying)
        media.nowPlayingChanged(song(playing: false))
        #expect(media.hubNowPlaying == song(playing: false))
        #expect(!media.hubMusicPlaying)
        media.nowPlayingChanged(nil)
        #expect(media.hubNowPlaying == nil)
        #expect(!media.hubMusicPlaying)
    }

    private func makeModel(api: FakeHubAPI = FakeHubAPI(), updates: [EventStreamUpdate]) -> AppModel {
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-np-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore())
        try! config.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t"))
        return AppModel(
            config: config, discovery: FakeDiscovery(results: []),
            makeClient: { _ in api },
            makeEvents: { _ in
                AsyncStream { continuation in
                    for update in updates { continuation.yield(update) }
                }
            })
    }

    @Test func helloThenEventThenNullDriveNowPlaying() async throws {
        let hello = try Fixtures.decode(Event.self, "event_hello")
        let paused = Event(serverTime: Date(), kind: .nowPlaying(song(playing: false)))
        let cleared = try Fixtures.decode(Event.self, "event_now_playing_null")
        let model = makeModel(updates: [.connected, .event(hello)])
        model.start()
        #expect(await waitUntil { model.nowPlaying?.title == "Here Comes the Sun" })
        #expect(model.nowPlaying?.artworkURL?.hasPrefix("https://") == true)
        model.stop()

        let later = makeModel(updates: [.connected, .event(hello), .event(paused), .event(cleared)])
        later.start()
        #expect(await waitUntil { later.lastEvent == cleared })
        #expect(later.nowPlaying == nil)
        later.stop()
    }

    @Test func nowPlayingEventFixtureIsApplied() async throws {
        let event = try Fixtures.decode(Event.self, "event_now_playing")
        let model = makeModel(updates: [.connected, .event(event)])
        model.start()
        #expect(await waitUntil { model.nowPlaying?.isPlaying == true })
        #expect(model.nowPlaying?.album == "Abbey Road")
        model.stop()
    }
}

@Suite struct NowPlayingRulesTests {
    @Test func idleStripShowsOnlyWhileMusicPlays() {
        #expect(NowPlayingRules.showsIdleStrip(song()))
        #expect(!NowPlayingRules.showsIdleStrip(song(playing: false)))
        #expect(!NowPlayingRules.showsIdleStrip(nil))
    }

    @Test func screenReturnsToIdleOnlyWhenNothingPlays() {
        #expect(!NowPlayingRules.shouldReturnToIdle(song()))
        #expect(NowPlayingRules.shouldReturnToIdle(song(playing: false)))
        #expect(NowPlayingRules.shouldReturnToIdle(nil))
        #expect(NowPlayingRules.idleTimeout == .seconds(30))
    }

    @Test func stripTextJoinsTitleAndArtist() {
        #expect(NowPlayingRules.stripText(song()) == "Here Comes the Sun — The Beatles")
    }

    @Test func controlUtterances() {
        #expect(NowPlayingControl.pause.utterance == "pause")
        #expect(NowPlayingControl.resume.utterance == "resume")
        #expect(NowPlayingControl.next.utterance == "next")
        #expect(NowPlayingControl.playPause(for: song()) == .pause)
        #expect(NowPlayingControl.playPause(for: song(playing: false)) == .resume)
    }
}

@MainActor
@Suite struct NowPlayingControlTests {
    private func makeModel(api: FakeHubAPI) -> AppModel {
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-npc-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore())
        try! config.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t"))
        return AppModel(
            config: config, discovery: FakeDiscovery(results: []),
            makeClient: { _ in api },
            makeEvents: { _ in AsyncStream { $0.yield(.connected) } })
    }

    @Test(arguments: [NowPlayingControl.pause, .resume, .next])
    func touchControlsSendTheUtteranceWithMusicContext(_ control: NowPlayingControl) async {
        let api = FakeHubAPI(
            utterance: .success(Action(say: nil, view: nil, commands: [], intent: "media", matchedBy: .rules)))
        let model = makeModel(api: api)
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        model.show(.nowPlaying)
        // Music is paused (so `mediaPlaying` alone would be nil) and a local clip is loaded:
        // controls on this screen still target the Hub's music.
        model.media.nowPlayingChanged(song(playing: false))
        model.media.playerStarted(.sound)

        await model.send(control)

        let sent = api.utterances.withLock { $0.last }
        #expect(sent?.text == control.utterance)
        #expect(sent?.source == .touch)
        #expect(sent?.context.mediaPlaying == .music)
        #expect(sent?.context.view == .nowPlaying)
        model.stop()
    }

    @Test func ordinarySubmitStillUsesTheCurrentMediaPlaying() async {
        let api = FakeHubAPI(
            utterance: .success(Action(say: nil, view: nil, commands: [], intent: "x", matchedBy: .rules)))
        let model = makeModel(api: api)
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        await model.submit(text: "hello", source: .voice)
        #expect(api.utterances.withLock { $0.last?.context.mediaPlaying } == nil)
        model.stop()
    }
}
