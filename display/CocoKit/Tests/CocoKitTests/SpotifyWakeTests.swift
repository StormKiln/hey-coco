import Foundation
import Testing
@testable import CocoKit

private let t0 = Date(timeIntervalSince1970: 1_794_668_400)
private let raffi = "spotify:artist:4RaffiArtistId0000000a"
private let playRaffi = SpotifyWakeRequest(deviceName: "iPad", play: true, uri: raffi, clientID: "client-123")

private func decide(
    _ request: SpotifyWakeRequest = playRaffi, guidedAccess: Bool = false, lastOpened: Date? = nil,
    now: Date = t0, appRemote: Bool = true, lastAppRemoteFailure: Date? = nil
) -> SpotifyWakeDecision {
    SpotifyWake.decide(
        request, guidedAccess: guidedAccess, lastOpened: lastOpened, now: now,
        appRemoteAvailable: appRemote, lastAppRemoteFailure: lastAppRemoteFailure)
}

// MARK: - Guided Access and rate limit (#5): unchanged

@Test func guidedAccessNeverOpensSpotify() {
    #expect(decide(guidedAccess: true) == .ignore(.guidedAccess))
    #expect(decide(SpotifyWakeRequest(), guidedAccess: true, appRemote: false) == .ignore(.guidedAccess))
}

@Test func opensAtMostOncePerInterval() {
    let soon = t0.addingTimeInterval(SpotifyWake.minInterval - 1)
    let later = t0.addingTimeInterval(SpotifyWake.minInterval)
    #expect(decide(lastOpened: t0, now: soon) == .ignore(.tooSoon))
    #expect(decide(lastOpened: t0, now: later) == .appRemote(clientID: "client-123", playURI: raffi))
}

// MARK: - SDK or plain scheme (#116)

@Test func aPlayWakeUsesTheSpotifySDK() {
    #expect(decide() == .appRemote(clientID: "client-123", playURI: raffi))
}

@Test func aPlayWakeWithoutURIResumesTheLastSong() {
    // The SDK's "blank string": play the user's last song.
    let resume = SpotifyWakeRequest(play: true, clientID: "client-123")
    #expect(decide(resume) == .appRemote(clientID: "client-123", playURI: ""))
}

@Test func withoutGuidedAccessFallsBackToTheSpotifyScheme() {
    let url = URL(string: "spotify:")!
    #expect(decide(SpotifyWakeRequest(deviceName: "iPad")) == .open(url, because: .notPlaying))
    #expect(decide(appRemote: false) == .open(url, because: .sdkUnavailable))
    let noClient = SpotifyWakeRequest(play: true, uri: raffi, clientID: nil)
    #expect(decide(noClient) == .open(url, because: .noClientID))
    let blankClient = SpotifyWakeRequest(play: true, uri: raffi, clientID: "  ")
    #expect(decide(blankClient) == .open(url, because: .noClientID))
    let notSpotify = SpotifyWakeRequest(play: true, uri: "https://example.com/x", clientID: "c")
    #expect(decide(notSpotify) == .open(url, because: .invalidURI))
}

@Test func aRecentSDKFailureFallsBackForAWhile() {
    let failed = t0.addingTimeInterval(-SpotifyWake.appRemoteRetryInterval + 1)
    #expect(decide(lastAppRemoteFailure: failed) == .open(SpotifyWake.url, because: .sdkFailedRecently))
    let longAgo = t0.addingTimeInterval(-SpotifyWake.appRemoteRetryInterval)
    #expect(decide(lastAppRemoteFailure: longAgo) == .appRemote(clientID: "client-123", playURI: raffi))
}

@Test func onlySpotifysRedirectIsACallback() {
    #expect(SpotifyWake.redirectURL.absoluteString == "cocokitchen-spotify://callback")
    #expect(SpotifyWake.isCallback(URL(string: "cocokitchen-spotify://callback?spotify_version=9#access_token=x")!))
    #expect(SpotifyWake.isCallback(URL(string: "CocoKitchen-Spotify://callback?error=unknown_error")!))
    #expect(!SpotifyWake.isCallback(URL(string: "cocokitchen-spotify://other")!))
    #expect(!SpotifyWake.isCallback(URL(string: "cocohub://pair?v=1&host=h&port=1&token=t&cert=ab")!))
    #expect(!SpotifyWake.isCallback(URL(string: "spotify:")!))
}

@Test func incomingURLsAreRouted() {
    #expect(IncomingURL(URL(string: "cocokitchen-spotify://callback#access_token=x")!) == .spotifyCallback)
    #expect(IncomingURL(URL(string: "cocohub://pair?v=1&host=h&port=1&token=t&cert=ab")!) == .pairing)
    #expect(IncomingURL(URL(string: "https://example.com")!) == .unknown)
}

// MARK: - SpotifyWaker

@MainActor
private final class FakeAppRemote: SpotifyAppRemoting {
    var calls: [(clientID: String, uri: String, redirect: URL)] = []
    var completion: (@MainActor @Sendable (Bool) -> Void)?
    var result: SpotifyAuthorizationResult? = .authorized

    func authorizeAndPlay(
        clientID: String, uri: String, redirectURL: URL,
        completion: @escaping @MainActor @Sendable (Bool) -> Void
    ) {
        calls.append((clientID, uri, redirectURL))
        self.completion = completion
    }

    func authorizationResult(from url: URL) -> SpotifyAuthorizationResult? { result }
}

@MainActor
private final class Harness {
    var guided = false
    var opened: [URL] = []
    var now = t0
    let remote = FakeAppRemote()
    lazy var waker = SpotifyWaker(
        isGuidedAccessEnabled: { [unowned self] in self.guided },
        open: { [unowned self] in self.opened.append($0) },
        appRemote: remote,
        now: { [unowned self] in self.now })
}

@MainActor
@Test func wakerOpensOnlyOutsideGuidedAccess() {
    let h = Harness()
    h.guided = true
    h.waker.wakeRequested(SpotifyWakeRequest(deviceName: "iPad"))
    #expect(h.opened.isEmpty)
    #expect(h.waker.lastOutcome == .ignored(.guidedAccess))

    h.guided = false
    h.waker.wakeRequested(SpotifyWakeRequest(deviceName: "iPad"))
    #expect(h.opened == [SpotifyWake.url])
    #expect(h.waker.lastOutcome == .openedScheme(.notPlaying))
    h.now = h.now.addingTimeInterval(5)
    h.waker.wakeRequested(SpotifyWakeRequest(deviceName: "iPad"))
    #expect(h.opened.count == 1)
}

@MainActor
@Test func wakerPlaysThroughTheSDKAndCountsTheCallback() {
    let h = Harness()
    h.waker.wakeRequested(playRaffi)
    #expect(h.opened.isEmpty)
    #expect(h.remote.calls.count == 1)
    #expect(h.remote.calls.first?.clientID == "client-123")
    #expect(h.remote.calls.first?.uri == raffi)
    #expect(h.remote.calls.first?.redirect == SpotifyWake.redirectURL)
    #expect(h.waker.lastOpened == t0)
    #expect(h.waker.lastOutcome == .appRemoteRequested)

    h.remote.completion?(true)
    #expect(h.waker.lastOutcome == .appRemoteRequested)
    #expect(h.waker.handleOpenURL(URL(string: "cocokitchen-spotify://callback#access_token=x")!))
    #expect(h.waker.lastOutcome == .appRemoteAuthorized)
    #expect(h.waker.lastAppRemoteFailure == nil)
}

@MainActor
@Test func spotifyNotReachableThroughTheSDKFallsBackToTheScheme() {
    let h = Harness()
    h.waker.wakeRequested(playRaffi)
    h.remote.completion?(false)
    #expect(h.opened == [SpotifyWake.url])
    #expect(h.waker.lastOutcome == .appRemoteFailed("Spotify app not reachable through the SDK"))
    #expect(h.waker.lastAppRemoteFailure == t0)

    // The next wake (after the rate limit) goes straight to the scheme.
    h.now = t0.addingTimeInterval(SpotifyWake.minInterval)
    h.waker.wakeRequested(playRaffi)
    #expect(h.remote.calls.count == 1)
    #expect(h.opened.count == 2)
    #expect(h.waker.lastOutcome == .openedScheme(.sdkFailedRecently))
}

@MainActor
@Test func anSDKErrorCallbackIsRecordedButStillCountsAsBack() {
    // SDK 5.x can redirect with error=unknown_error and no token (spotify/ios-sdk#474). Spotify
    // was opened and Coco is in front again, which is what the wake is for: the Hub plays the
    // URI through the Web API once the iPad is listed. So keep using the SDK.
    let h = Harness()
    h.waker.wakeRequested(playRaffi)
    h.remote.result = .failed("unknown_error")
    #expect(h.waker.handleOpenURL(URL(string: "cocokitchen-spotify://callback?error=unknown_error")!))
    #expect(h.waker.lastOutcome == .appRemoteReturnedWithError("unknown_error"))
    #expect(h.waker.lastAppRemoteFailure == nil)
    #expect(h.opened.isEmpty)

    h.now = t0.addingTimeInterval(SpotifyWake.minInterval)
    h.waker.wakeRequested(playRaffi)
    #expect(h.remote.calls.count == 2)
}

@MainActor
@Test func noRedirectByTheNextWakeFallsBackToTheScheme() {
    // Spotify kept the screen (no redirect); someone switched back by hand. Next time, the
    // plain scheme: it can't do worse, and the SDK gets another go after the retry interval.
    let h = Harness()
    h.waker.wakeRequested(playRaffi)
    h.remote.completion?(true)
    h.now = t0.addingTimeInterval(SpotifyWake.minInterval)
    h.waker.wakeRequested(playRaffi)
    #expect(h.remote.calls.count == 1)
    #expect(h.opened == [SpotifyWake.url])
    #expect(h.waker.lastAppRemoteFailure == h.now)
    #expect(h.waker.lastOutcome == .openedScheme(.sdkFailedRecently))
}

@MainActor
@Test func aWakeTooSoonDoesNotCountAsAMissingRedirect() {
    let h = Harness()
    h.waker.wakeRequested(playRaffi)
    h.now = t0.addingTimeInterval(5)
    h.waker.wakeRequested(playRaffi)  // ignored: too soon
    #expect(h.waker.lastAppRemoteFailure == nil)
    #expect(h.waker.handleOpenURL(URL(string: "cocokitchen-spotify://callback#access_token=x")!))
    #expect(h.waker.lastOutcome == .appRemoteAuthorized)
}

@MainActor
@Test func anUnparsedCallbackStillCountsAsBack() {
    // Coco was relaunched while in Spotify: no SDK object to parse it, but Coco is in front.
    let h = Harness()
    h.remote.result = nil
    #expect(h.waker.handleOpenURL(URL(string: "cocokitchen-spotify://callback#access_token=x")!))
    #expect(h.waker.lastOutcome == .appRemoteReturned)
    #expect(h.waker.lastAppRemoteFailure == nil)
}

@MainActor
@Test func otherURLsAreNotTheWakersBusiness() {
    let h = Harness()
    #expect(!h.waker.handleOpenURL(URL(string: "cocohub://pair?v=1")!))
    #expect(h.waker.lastOutcome == nil)
}

@MainActor
@Test func withoutTheSDKAPlayWakeOpensTheScheme() {
    var opened: [URL] = []
    let waker = SpotifyWaker(isGuidedAccessEnabled: { false }, open: { opened.append($0) }, now: { t0 })
    waker.wakeRequested(playRaffi)
    #expect(opened == [SpotifyWake.url])
    #expect(waker.lastOutcome == .openedScheme(.sdkUnavailable))
}

@MainActor
@Test func appModelHandsTheWakeEventAndTheCallbackToTheWaker() {
    let model = makeMusicTestModel()
    let remote = FakeAppRemote()
    var opened = 0
    model.spotifyWaker = SpotifyWaker(
        isGuidedAccessEnabled: { false }, open: { _ in opened += 1 }, appRemote: remote)
    model.mediaEventReceived(Event(serverTime: t0, kind: .spotifyWakeRequested(playRaffi)))
    #expect(remote.calls.count == 1)
    model.mediaEventReceived(Event(serverTime: t0, kind: .nowPlaying(nil)))
    #expect(remote.calls.count == 1)
    #expect(opened == 0)
    #expect(model.handleOpenURL(URL(string: "cocokitchen-spotify://callback#access_token=x")!))
    #expect(model.spotifyWaker?.lastOutcome == .appRemoteAuthorized)
    #expect(!model.handleOpenURL(URL(string: "https://example.com")!))
}

@Test func diagnosticsDescribeTheLastWake() {
    #expect(DiagnosticsFormat.spotifyWake(nil) == "None yet")
    #expect(DiagnosticsFormat.spotifyWake(.appRemoteAuthorized) == "Spotify SDK: back in Coco")
    #expect(DiagnosticsFormat.spotifyWake(.appRemoteFailed("no redirect")) == "Spotify SDK failed: no redirect")
    #expect(
        DiagnosticsFormat.spotifyWake(.appRemoteReturnedWithError("unknown_error"))
            == "Spotify SDK: back in Coco, Spotify said unknown_error")
    #expect(DiagnosticsFormat.spotifyWake(.openedScheme(.noClientID)) == "Opened Spotify (no client ID from the Hub)")
    #expect(DiagnosticsFormat.spotifyWake(.ignored(.guidedAccess)) == "Skipped (Guided Access)")
}
