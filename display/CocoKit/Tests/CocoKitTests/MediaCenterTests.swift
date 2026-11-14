import Foundation
import Testing
@testable import CocoKit

/// Records what `MediaCenter` asked it to do.
@MainActor
final class FakeMediaPlayer: YouTubeControlling, SoundPlaying {
    private(set) var calls: [String] = []
    private(set) var gains: [Double] = []

    func pause() { calls.append("pause") }
    func resume() { calls.append("resume") }
    func stop() { calls.append("stop") }
    func replay() { calls.append("replay") }
    func setGain(_ gain: Double) { gains.append(gain) }
}

@MainActor
@Suite struct MediaCenterTests {
    private func makeCenter() -> (MediaCenter, youtube: FakeMediaPlayer, sound: FakeMediaPlayer) {
        let media = MediaCenter()
        let youtube = FakeMediaPlayer()
        let sound = FakeMediaPlayer()
        media.register(youtube: youtube)
        media.register(sound: sound)
        return (media, youtube, sound)
    }

    // MARK: Command routing

    @Test func pauseResumeStopGoToTheTargetedPlayerOnly() {
        let (media, youtube, sound) = makeCenter()
        media.perform(.mediaPause(target: .youtube))
        media.perform(.mediaResume(target: .youtube))
        media.perform(.mediaStop(target: .youtube))
        #expect(youtube.calls == ["pause", "resume", "stop"])
        #expect(sound.calls.isEmpty)

        media.perform(.mediaPause(target: .sound))
        media.perform(.mediaResume(target: .sound))
        media.perform(.mediaStop(target: .sound))
        #expect(sound.calls == ["pause", "resume", "stop"])
        #expect(youtube.calls.count == 3)
    }

    @Test func nextReplaysASoundAndIsIgnoredForYouTube() {
        let (media, youtube, sound) = makeCenter()
        media.perform(.mediaNext(target: .sound))
        media.perform(.mediaNext(target: .youtube))
        #expect(sound.calls == ["replay"])
        #expect(youtube.calls.isEmpty)
    }

    @Test func musicCommandsAreLeftToTheHub() {
        let (media, youtube, sound) = makeCenter()
        for command: Command in [
            .mediaPause(target: .music), .mediaResume(target: .music), .mediaNext(target: .music),
            .mediaStop(target: .music),
        ] {
            media.perform(command)
        }
        #expect(youtube.calls.isEmpty)
        #expect(sound.calls.isEmpty)
    }

    @Test func commandsWithNoRegisteredPlayerAreHarmless() {
        let media = MediaCenter()
        media.perform(.mediaPause(target: .youtube))
        media.perform(.mediaStop(target: .sound))
        #expect(media.mediaPlaying == nil)
    }

    @Test func playersAreHeldWeakly() {
        let media = MediaCenter()
        var player: FakeMediaPlayer? = FakeMediaPlayer()
        media.register(youtube: player!)
        player = nil
        media.perform(.mediaPause(target: .youtube))  // must not crash
        #expect(player == nil)
    }

    // MARK: What's playing

    @Test func mediaPlayingFollowsPlayerReports() {
        let (media, _, _) = makeCenter()
        #expect(media.mediaPlaying == nil)
        media.playerStarted(.youtube)
        #expect(media.mediaPlaying == .youtube)
        #expect(!media.isPaused)
        media.playerPaused(.youtube)
        // Paused still counts, so "resume" targets the video.
        #expect(media.mediaPlaying == .youtube)
        #expect(media.isPaused)
        media.playerEnded(.youtube)
        #expect(media.mediaPlaying == nil)
    }

    @Test func endingAPlayerThatIsNotCurrentKeepsTheCurrentOne() {
        let (media, _, _) = makeCenter()
        media.playerStarted(.youtube)
        media.playerEnded(.sound)
        #expect(media.mediaPlaying == .youtube)
    }

    @Test func pauseCommandMarksThePlayerPaused() {
        let (media, _, sound) = makeCenter()
        media.playerStarted(.sound)
        media.perform(.mediaPause(target: .sound))
        #expect(media.isPaused)
        media.perform(.mediaResume(target: .sound))
        #expect(!media.isPaused)
        #expect(sound.calls == ["pause", "resume"])
    }

    @Test func hubMusicCountsWhenNothingLocalPlays() {
        let media = MediaCenter()
        let music = NowPlaying(
            source: .spotify, title: "Song", artist: "A", album: nil, artworkURL: nil, isPlaying: true)
        media.nowPlayingChanged(music)
        #expect(media.mediaPlaying == .music)
        media.playerStarted(.youtube)
        #expect(media.mediaPlaying == .youtube)
        media.playerEnded(.youtube)
        #expect(media.mediaPlaying == .music)
        var paused = music
        paused.isPlaying = false
        media.nowPlayingChanged(paused)
        #expect(media.mediaPlaying == nil)
        media.nowPlayingChanged(music)
        media.nowPlayingChanged(nil)
        #expect(media.mediaPlaying == nil)
    }

    @Test func onFinishedFiresWhenAPlayerEndsButNotOnUnregister() {
        let (media, youtube, _) = makeCenter()
        var finished: [MediaTarget] = []
        media.onFinished = { finished.append($0) }
        media.playerStarted(.youtube)
        media.playerEnded(.youtube)
        #expect(finished == [.youtube])

        media.playerStarted(.youtube)
        media.unregister(youtube: youtube)
        #expect(finished == [.youtube])
        #expect(media.mediaPlaying == nil)
    }

    @Test func unregisteringAStalePlayerKeepsTheNewOne() {
        let media = MediaCenter()
        let old = FakeMediaPlayer()
        let new = FakeMediaPlayer()
        media.register(youtube: old)
        media.register(youtube: new)
        media.playerStarted(.youtube)
        media.unregister(youtube: old)
        #expect(media.mediaPlaying == .youtube)
        media.perform(.mediaPause(target: .youtube))
        #expect(new.calls == ["pause"])
        #expect(old.calls.isEmpty)
    }

    @Test func soundRequestsAreCounted() {
        let media = MediaCenter()
        media.soundRequested()
        media.soundRequested()
        #expect(media.soundRequestCount == 2)
    }

    // MARK: Alarm

    @Test func alarmPausesPlayingMediaAndResumesAfter() {
        let (media, youtube, _) = makeCenter()
        media.playerStarted(.youtube)
        media.alarmChanged(ringing: true)
        #expect(youtube.calls == ["pause"])
        #expect(media.isPaused)
        media.alarmChanged(ringing: true)  // repeated report: no double pause
        #expect(youtube.calls == ["pause"])
        media.alarmChanged(ringing: false)
        #expect(youtube.calls == ["pause", "resume"])
        #expect(!media.isPaused)
    }

    @Test func alarmDoesNotResumeWhatTheUserPaused() {
        let (media, youtube, _) = makeCenter()
        media.playerStarted(.youtube)
        media.perform(.mediaPause(target: .youtube))
        media.alarmChanged(ringing: true)
        media.alarmChanged(ringing: false)
        #expect(youtube.calls == ["pause"])
    }

    @Test func alarmDoesNotResumeWhatWasStoppedMeanwhile() {
        let (media, youtube, _) = makeCenter()
        media.playerStarted(.youtube)
        media.alarmChanged(ringing: true)
        media.perform(.mediaStop(target: .youtube))
        media.alarmChanged(ringing: false)
        #expect(youtube.calls == ["pause", "stop"])
    }

    @Test func alarmWithNothingPlayingDoesNothing() {
        let (media, youtube, sound) = makeCenter()
        media.alarmChanged(ringing: true)
        media.alarmChanged(ringing: false)
        #expect(youtube.calls.isEmpty)
        #expect(sound.calls.isEmpty)
    }

    @Test func mediaStartedDuringAnAlarmIsPausedRightAway() {
        let (media, _, sound) = makeCenter()
        media.alarmChanged(ringing: true)
        media.playerStarted(.sound)
        #expect(sound.calls == ["pause"])
        media.alarmChanged(ringing: false)
        #expect(sound.calls == ["pause", "resume"])
    }

    // MARK: Volume

    @Test func volumeIsClampedTo0Through100() {
        let media = MediaCenter()
        #expect(media.volume == MediaCenter.defaultVolume)
        media.perform(.volumeSet(level: 150))
        #expect(media.volume == 100)
        media.perform(.volumeSet(level: -5))
        #expect(media.volume == 0)
        media.perform(.volumeSet(level: 90))
        media.perform(.volumeChange(delta: 20))
        #expect(media.volume == 100)
        media.perform(.volumeChange(delta: -130))
        #expect(media.volume == 0)
        media.perform(.volumeChange(delta: 30))
        #expect(media.volume == 30)
    }

    @Test func volumeIsAppliedToEveryPlayerAndToNewOnes() {
        let (media, youtube, sound) = makeCenter()
        media.perform(.volumeSet(level: 50))
        #expect(youtube.gains.last == 0.5)
        #expect(sound.gains.last == 0.5)
        let later = FakeMediaPlayer()
        media.register(sound: later)
        #expect(later.gains == [0.5])
    }

    @Test func duckingLowersTheGainWithoutChangingTheVolume() {
        let (media, youtube, _) = makeCenter()
        media.setDucked(true)
        #expect(media.volume == 100)
        #expect(media.effectiveGain == MediaCenter.duckedGain)
        #expect(youtube.gains.last == MediaCenter.duckedGain)
        media.setDucked(false)
        #expect(youtube.gains.last == 1)
    }

    @Test func otherCommandsAreIgnored() {
        let (media, youtube, sound) = makeCenter()
        media.perform(.alarmStop)
        media.perform(.screenWake)
        #expect(youtube.calls.isEmpty)
        #expect(sound.calls.isEmpty)
        #expect(media.volume == 100)
    }
}
