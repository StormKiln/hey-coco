import Foundation
import Testing
@testable import CocoKit

@Suite struct YouTubeEmbedTests {
    @Test func acceptsRealVideoIDs() {
        #expect(YouTubeEmbed(videoID: "dQw4w9WgXcQ") != nil)
        #expect(YouTubeEmbed(videoID: "a-b_C1") != nil)
    }

    @Test func rejectsIDsThatCouldEscapeTheHTML() {
        for bad in ["", "abc'def", "abc\"><script>", "a b", "abc/def", "abc;x", String(repeating: "a", count: 65)] {
            #expect(YouTubeEmbed(videoID: bad) == nil, "\(bad)")
        }
    }

    @Test func embedURLHasThePlayerParameters() throws {
        let embed = try #require(YouTubeEmbed(videoID: "dQw4w9WgXcQ"))
        let url = embed.embedURL(origin: URL(string: "https://example.test")!)
        let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))
        #expect(components.host == "www.youtube.com")
        #expect(components.path == "/embed/dQw4w9WgXcQ")
        let items = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })
        #expect(items["playsinline"] == "1")
        #expect(items["enablejsapi"] == "1")
        #expect(items["rel"] == "0")
        #expect(items["modestbranding"] == "1")
        #expect(items["autoplay"] == "1")
        #expect(items["origin"] == "https://example.test")
    }

    @Test func htmlLoadsTheIFrameAPIAndPostsStateChanges() throws {
        let embed = try #require(YouTubeEmbed(videoID: "dQw4w9WgXcQ"))
        let html = embed.html(origin: URL(string: "https://example.test")!)
        #expect(html.contains("https://www.youtube.com/iframe_api"))
        #expect(html.contains("/embed/dQw4w9WgXcQ?"))
        #expect(html.contains("onStateChange"))
        #expect(html.contains("webkit.messageHandlers.\(YouTubeEmbed.messageHandlerName)"))
    }

    @Test func playerStatesMapFromIFrameCodes() {
        #expect(YouTubePlayerState(code: -1) == .unstarted)
        #expect(YouTubePlayerState(code: 0) == .ended)
        #expect(YouTubePlayerState(code: 1) == .playing)
        #expect(YouTubePlayerState(code: 2) == .paused)
        #expect(YouTubePlayerState(code: 3) == .buffering)
        #expect(YouTubePlayerState(code: 5) == .cued)
        #expect(YouTubePlayerState(code: 42) == nil)
    }

    @MainActor
    @Test func playerStatesDriveTheMediaCenter() {
        let media = MediaCenter()
        var finished = 0
        media.onFinished = { _ in finished += 1 }
        YouTubePlayerState.playing.report(to: media)
        #expect(media.mediaPlaying == .youtube)
        YouTubePlayerState.paused.report(to: media)
        #expect(media.isPaused)
        YouTubePlayerState.buffering.report(to: media)
        #expect(media.isPaused)
        YouTubePlayerState.ended.report(to: media)
        #expect(media.mediaPlaying == nil)
        #expect(finished == 1)
    }

    @Test func volumeScriptClampsTheGain() {
        #expect(YouTubeEmbed.script(.setGain(0.5)) == "cocoSetVolume(50);")
        #expect(YouTubeEmbed.script(.setGain(2)) == "cocoSetVolume(100);")
        #expect(YouTubeEmbed.script(.setGain(-1)) == "cocoSetVolume(0);")
        #expect(YouTubeEmbed.script(.pause) == "cocoPause();")
        #expect(YouTubeEmbed.script(.play) == "cocoPlay();")
        #expect(YouTubeEmbed.script(.stop) == "cocoStop();")
    }
}
