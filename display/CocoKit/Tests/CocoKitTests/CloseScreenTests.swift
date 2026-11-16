import Foundation
import Testing
@testable import CocoKit

/// #92: full-screen results with no way out but a timeout get a big Close (X) button.
@MainActor
@Suite struct CloseScreenTests {
    private func makeModel() -> AppModel {
        let config = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-close-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        try! config.save(HubSettings(manualHost: "10.0.0.5", port: 8765, token: "t", pin: .test))
        return AppModel(
            config: config, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
            makeClient: { _ in FakeHubAPI() },
            makeEvents: { _ in AsyncStream { _ in } },
            timerStore: TimerStore())
    }

    private let cow = ViewSpec.SoundPayload(
        name: "cow", clipURL: "/v1/media/sounds/clips/cow.m4a",
        imageURL: "/v1/media/sounds/images/cow.jpg", attribution: "Cow by X, CC0")
    private let giraffe = ViewSpec.PicturePayload(query: "giraffe", images: [])

    @Test func resultScreensWithoutAWayOutGetTheCloseButton() {
        #expect(ViewSpec.picture(giraffe).showsCloseButton)
        #expect(ViewSpec.sound(cow).showsCloseButton)
        #expect(ViewSpec.answer(text: "Paris.").showsCloseButton)
    }

    @Test func screensWithTheirOwnWayOutDoNot() {
        // Idle is home; weather, timers and now playing have a Home button; YouTube its own X.
        #expect(!ViewSpec.idle.showsCloseButton)
        #expect(!ViewSpec.timers.showsCloseButton)
        #expect(!ViewSpec.nowPlaying.showsCloseButton)
        #expect(!ViewSpec.youtube(videoID: "abc", title: "Bluey").showsCloseButton)
        #expect(!ViewSpec.unknown("future").showsCloseButton)
    }

    @Test func closingAPictureGoesHome() {
        let model = makeModel()
        model.show(.picture(giraffe))
        model.closeScreen()
        #expect(model.currentView == .idle)
    }

    @Test func closingASoundStopsTheClipAndGoesHome() {
        let model = makeModel()
        let sound = FakeMediaPlayer()
        model.media.register(sound: sound)
        model.show(.sound(cow))
        model.closeScreen()
        #expect(sound.calls == ["stop"])
        #expect(model.currentView == .idle)
    }

    @Test func closingAnAnswerGoesHome() {
        let model = makeModel()
        model.show(.answer(text: "Paris is the capital of France."))
        model.closeScreen()
        #expect(model.currentView == .idle)
    }
}
