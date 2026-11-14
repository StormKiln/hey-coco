import Foundation
import Testing
@testable import CocoKit

private func image(_ title: String) -> PictureImage {
    PictureImage(
        url: "https://upload.wikimedia.org/\(title).jpg",
        thumbURL: "https://upload.wikimedia.org/thumb/\(title).jpg", title: title, width: 800,
        height: 600, license: "CC BY-SA 4.0", author: "Someone",
        sourceURL: "https://commons.wikimedia.org/wiki/File:\(title).jpg")
}

@MainActor
@Suite struct PictureCarouselTests {
    private final class Counter { var count = 0 }

    @Test func showsTheFirstImageAndWrapsBothWays() {
        let carousel = PictureCarousel()
        #expect(carousel.current == nil)
        carousel.show([image("a"), image("b"), image("c")])
        #expect(carousel.current?.title == "a")
        carousel.next()
        carousel.next()
        #expect(carousel.current?.title == "c")
        carousel.next()
        #expect(carousel.current?.title == "a")
        carousel.previous()
        #expect(carousel.current?.title == "c")
        #expect(carousel.canAdvance)
    }

    @Test func aNewSetStartsAtItsFirstImage() {
        // The Hub's "next one" resends the view with the next image first.
        let carousel = PictureCarousel()
        carousel.show([image("a"), image("b")])
        carousel.next()
        carousel.show([image("b"), image("a")])
        #expect(carousel.index == 0)
        #expect(carousel.current?.title == "b")
    }

    @Test func emptyAndSingleSetsDoNotMove() {
        let carousel = PictureCarousel()
        carousel.next()
        carousel.previous()
        #expect(carousel.current == nil)
        carousel.show([image("a")])
        carousel.next()
        #expect(carousel.current?.title == "a")
        #expect(!carousel.canAdvance)
    }

    @Test func returnsToIdleAfterTheTimeout() async {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let carousel = PictureCarousel(idleAfter: .seconds(60), sleep: sleeper.sleep)
        carousel.onIdle = { counter.count += 1 }
        carousel.show([image("a")])
        #expect(await waitUntil { sleeper.pending == [.seconds(60)] })
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func swipingRestartsTheCountdown() async {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let carousel = PictureCarousel(idleAfter: .seconds(60), sleep: sleeper.sleep)
        carousel.onIdle = { counter.count += 1 }
        carousel.show([image("a"), image("b")])
        #expect(await waitUntil { sleeper.pending.count == 1 })
        carousel.next()
        // The old sleep is cancelled; exactly one new one waits.
        #expect(await waitUntil { sleeper.pending.count == 1 })
        try? await Task.sleep(for: .milliseconds(20))
        #expect(counter.count == 0)
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func stopCancelsTheCountdown() async {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let carousel = PictureCarousel(idleAfter: .seconds(60), sleep: sleeper.sleep)
        carousel.onIdle = { counter.count += 1 }
        carousel.show([image("a")])
        #expect(await waitUntil { sleeper.pending.count == 1 })
        carousel.stop()
        #expect(await waitUntil { sleeper.pending.isEmpty })
        #expect(counter.count == 0)
    }

    @Test func attributionLine() {
        #expect(image("a").attributionLine == "Someone · CC BY-SA 4.0")
        var anonymous = image("a")
        anonymous.author = nil
        #expect(anonymous.attributionLine == "CC BY-SA 4.0")
        anonymous.author = "  "
        #expect(anonymous.attributionLine == "CC BY-SA 4.0")
    }

    @Test func captionTidiesCommonsFileNames() {
        var picture = image("a")
        picture.title = "File:Red_fox_in_snow.jpeg"
        #expect(picture.caption == "Red fox in snow")
        picture.title = "A cow"
        #expect(picture.caption == "A cow")
        picture.title = "St. Lawrence River"
        #expect(picture.caption == "St. Lawrence River")
    }

    @Test func displayURLPrefersTheThumbnail() {
        var picture = image("a")
        #expect(picture.displayURL == picture.thumbURL)
        picture.thumbURL = ""
        #expect(picture.displayURL == picture.url)
    }
}

@MainActor
@Suite struct IdleCountdownTests {
    private final class Counter { var count = 0 }

    @Test func armFiresOnceAndDisarmCancels() async {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let countdown = IdleCountdown(timeout: .seconds(10), sleep: sleeper.sleep) { counter.count += 1 }
        #expect(!countdown.isArmed)
        countdown.arm()
        #expect(countdown.isArmed)
        #expect(await waitUntil { sleeper.pending == [.seconds(10)] })
        countdown.disarm()
        #expect(await waitUntil { sleeper.pending.isEmpty })
        countdown.arm()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
        #expect(!countdown.isArmed)
    }

    @Test func touchOnlyRestartsAnArmedCountdown() async {
        let sleeper = ManualSleeper()
        let countdown = IdleCountdown(timeout: .seconds(10), sleep: sleeper.sleep) {}
        countdown.touch()
        #expect(!countdown.isArmed)
        countdown.arm()
        countdown.touch()
        #expect(countdown.isArmed)
        #expect(await waitUntil { sleeper.pending.count == 1 })
    }
}
