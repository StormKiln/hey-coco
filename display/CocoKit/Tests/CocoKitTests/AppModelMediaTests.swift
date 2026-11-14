import Foundation
import Testing
@testable import CocoKit

@MainActor
@Suite struct AppModelMediaTests {
    private func makeModel(
        api: FakeHubAPI = FakeHubAPI(), updates: [EventStreamUpdate] = [],
        store: TimerStore = TimerStore()
    ) -> AppModel {
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-media-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        try! config.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test))
        return AppModel(
            config: config, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
            makeClient: { _ in api },
            makeEvents: { _ in
                AsyncStream { continuation in
                    for update in updates { continuation.yield(update) }
                }
            },
            timerStore: store)
    }

    private func action(view: ViewSpec?, commands: [Command] = []) -> Action {
        Action(say: nil, view: view, commands: commands, intent: "test", matchedBy: .rules)
    }

    private let cow = ViewSpec.SoundPayload(
        name: "cow", clipURL: "/v1/media/sounds/clips/cow.m4a",
        imageURL: "/v1/media/sounds/images/cow.jpg", attribution: "Cow by X, CC0")

    @Test func mediaAndVolumeCommandsReachTheMediaCenter() {
        let model = makeModel()
        let youtube = FakeMediaPlayer()
        model.media.register(youtube: youtube)
        model.perform(.mediaPause(target: .youtube))
        model.perform(.volumeSet(level: 40))
        #expect(youtube.calls == ["pause"])
        #expect(model.media.volume == 40)
    }

    @Test func everySoundViewCountsAsAPlayRequestEvenWhenIdentical() {
        // "play it again" resends the same sound view.
        let model = makeModel()
        model.apply(action(view: .sound(cow)))
        model.apply(action(view: .sound(cow)))
        model.apply(action(view: .answer(text: "hi")))
        model.apply(action(view: nil))
        #expect(model.media.soundRequestCount == 2)
    }

    @Test func mediaPlayingIsSentWithUtterances() async throws {
        let api = FakeHubAPI(utterance: .success(action(view: nil)))
        let model = makeModel(api: api, updates: [.connected])
        model.start()
        #expect(await waitUntil { model.connection == .connected })
        model.media.playerStarted(.youtube)
        #expect(model.mediaPlaying == .youtube)
        await model.submit(text: "pause", source: .voice)
        #expect(api.utterances.withLock { $0.last?.context.mediaPlaying } == .youtube)
        model.stop()
    }

    @Test func aFinishedVideoReturnsToIdle() {
        let model = makeModel()
        model.show(.youtube(videoID: "dQw4w9WgXcQ", title: "Song"))
        model.media.playerStarted(.youtube)
        model.media.playerEnded(.youtube)
        #expect(model.currentView == .idle)
    }

    @Test func aFinishedVideoDoesNotLeaveAnotherScreen() {
        let model = makeModel()
        model.show(.timers)
        model.media.playerStarted(.youtube)
        model.media.playerEnded(.youtube)
        #expect(model.currentView == .timers)
    }

    @Test func aRingingTimerPausesMediaAndStoppingItResumes() async {
        let clock = TestClock()
        let store = TimerStore(now: clock.function)
        let model = makeModel(store: store)
        let youtube = FakeMediaPlayer()
        model.media.register(youtube: youtube)
        model.media.playerStarted(.youtube)

        await store.create(durationS: 60)
        clock.advance(by: 61)
        store.fireDueAlarms()
        #expect(store.isAlarmRinging)
        #expect(youtube.calls == ["pause"])

        await store.stopAlarms()
        #expect(!store.isAlarmRinging)
        #expect(youtube.calls == ["pause", "resume"])
    }

    @Test func hubNowPlayingEventsFeedMediaPlaying() async {
        let music = NowPlaying(
            source: .spotify, title: "Song", artist: "A", album: nil, artworkURL: nil, isPlaying: true)
        let model = makeModel(updates: [
            .connected, .event(Event(serverTime: Date(), kind: .hello(timers: [], nowPlaying: music))),
        ])
        model.start()
        #expect(await waitUntil { model.mediaPlaying == .music })
        model.stop()
    }
}
