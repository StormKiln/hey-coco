import Foundation
import Testing
@testable import CocoKit

private let t0 = Date(timeIntervalSince1970: 1_794_668_400)

@Test func guidedAccessNeverOpensSpotify() {
    #expect(SpotifyWake.decide(guidedAccess: true, lastOpened: nil, now: t0) == .ignore(.guidedAccess))
}

@Test func withoutGuidedAccessOpensTheSpotifyScheme() {
    #expect(SpotifyWake.decide(guidedAccess: false, lastOpened: nil, now: t0) == .open(URL(string: "spotify:")!))
}

@Test func opensAtMostOncePerInterval() {
    let soon = t0.addingTimeInterval(SpotifyWake.minInterval - 1)
    let later = t0.addingTimeInterval(SpotifyWake.minInterval)
    #expect(SpotifyWake.decide(guidedAccess: false, lastOpened: t0, now: soon) == .ignore(.tooSoon))
    #expect(SpotifyWake.decide(guidedAccess: false, lastOpened: t0, now: later) == .open(SpotifyWake.url))
}

@MainActor
@Test func wakerOpensOnlyOutsideGuidedAccess() {
    var guided = true
    var opened: [URL] = []
    var now = t0
    let waker = SpotifyWaker(
        isGuidedAccessEnabled: { guided }, open: { opened.append($0) }, now: { now })
    waker.wakeRequested()
    #expect(opened.isEmpty)
    #expect(waker.lastDecision == .ignore(.guidedAccess))

    guided = false
    waker.wakeRequested()
    #expect(opened == [SpotifyWake.url])
    now = now.addingTimeInterval(5)
    waker.wakeRequested()
    #expect(opened.count == 1)
}

@MainActor
@Test func appModelHandsTheWakeEventToTheWaker() {
    let model = makeMusicTestModel()
    var opened = 0
    model.spotifyWaker = SpotifyWaker(isGuidedAccessEnabled: { false }, open: { _ in opened += 1 })
    model.mediaEventReceived(Event(serverTime: t0, kind: .spotifyWakeRequested(deviceName: "Kitchen iPad")))
    #expect(opened == 1)
    model.mediaEventReceived(Event(serverTime: t0, kind: .nowPlaying(nil)))
    #expect(opened == 1)
}
