import Foundation
import Observation

/// A player Coco owns on the iPad. `MediaCenter` drives it; the player reports back with
/// `MediaCenter.playerStarted/Paused/Ended`.
@MainActor
public protocol MediaPlayerControl: AnyObject {
    func pause()
    func resume()
    func stop()
    /// Output gain 0...1 (`MediaCenter.effectiveGain`).
    func setGain(_ gain: Double)
}

/// The embedded YouTube player (#39).
@MainActor
public protocol YouTubeControlling: MediaPlayerControl {}

/// The sound-clip player (#35).
@MainActor
public protocol SoundPlaying: MediaPlayerControl {
    /// Plays the current clip again from the start.
    func replay()
}

/// What Coco is playing on the iPad, and the one place media and volume commands go.
///
/// - Tracks the playing kind for `UtteranceContext.media_playing`, so "pause" / "next" reach the
///   right thing. Local players (YouTube, sound clips) win over music the Hub reports through
///   `now_playing` events.
/// - Routes `media.*` commands to the registered player for the target. `music` lives on the
///   Hub's speakers (Spotify / Apple Music), so music commands are left to the Hub. There is no
///   local YouTube queue, so `media.next` for YouTube is ignored (the Hub sends a new
///   `youtube` view instead); `media.next` for a sound replays it.
/// - Volume is an app-level output gain (0–100) applied to Coco's own players. iPadOS has no
///   public API to set the system volume (only the `MPVolumeView` slider hack), so the system
///   volume stays where the user put it and `volume.set` / `volume.change` scale Coco's media
///   within it. Spoken replies and the alarm are not scaled: the alarm must stay loud.
/// - While an alarm rings, whatever was playing is paused and resumed afterwards, unless it was
///   paused or stopped by someone in between.
@MainActor
@Observable
public final class MediaCenter {
    public static let defaultVolume = 100
    /// Gain while ducked (relative to `volume`).
    public static let duckedGain = 0.2

    /// The local player that is playing or paused, if any.
    public private(set) var nowPlayingKind: MediaTarget?
    /// True when `nowPlayingKind` is paused.
    public private(set) var isPaused = false
    /// App output volume, 0–100.
    public private(set) var volume = MediaCenter.defaultVolume
    /// Lowered while Coco listens (set by the listening loop, #22).
    public private(set) var isDucked = false
    /// Bumped for every `sound` view the Hub sends, including an identical resend
    /// ("play it again"); the Sound screen replays on each bump.
    public private(set) var soundRequestCount = 0
    /// Whether the Hub reports music playing on its speakers.
    public private(set) var hubMusicPlaying = false

    /// Called when a player reports it reached its end (or was stopped by a command).
    @ObservationIgnored public var onFinished: (@MainActor (MediaTarget) -> Void)?

    @ObservationIgnored private weak var youtube: (any YouTubeControlling)?
    @ObservationIgnored private weak var sound: (any SoundPlaying)?
    @ObservationIgnored private var alarmRinging = false
    @ObservationIgnored private var pausedForAlarm = false

    public init() {}

    /// Sent as `context.media_playing`.
    public var mediaPlaying: MediaTarget? {
        nowPlayingKind ?? (hubMusicPlaying ? .music : nil)
    }

    /// `volume` as 0...1, lowered while ducked.
    public var effectiveGain: Double {
        Double(volume) / 100 * (isDucked ? Self.duckedGain : 1)
    }

    // MARK: - Players

    public func register(youtube player: any YouTubeControlling) {
        youtube = player
        player.setGain(effectiveGain)
    }

    public func register(sound player: any SoundPlaying) {
        sound = player
        player.setGain(effectiveGain)
    }

    /// The player is going away (its screen closed). Clears its state without `onFinished`.
    public func unregister(youtube player: any YouTubeControlling) {
        guard youtube === player else { return }
        youtube = nil
        clear(.youtube)
    }

    public func unregister(sound player: any SoundPlaying) {
        guard sound === player else { return }
        sound = nil
        clear(.sound)
    }

    public func playerStarted(_ kind: MediaTarget) {
        nowPlayingKind = kind
        isPaused = false
        if alarmRinging {
            // Never play over a ringing alarm.
            pausedForAlarm = true
            isPaused = true
            player(for: kind)?.pause()
        }
    }

    public func playerPaused(_ kind: MediaTarget) {
        guard nowPlayingKind == kind else { return }
        isPaused = true
    }

    public func playerEnded(_ kind: MediaTarget) {
        guard nowPlayingKind == kind else { return }
        clear(kind)
        onFinished?(kind)
    }

    // MARK: - Commands

    /// Runs a `media.*` or `volume.*` command; anything else is ignored.
    public func perform(_ command: Command) {
        switch command {
        case .mediaPause(let target): pause(target)
        case .mediaResume(let target): resume(target)
        case .mediaNext(let target): next(target)
        case .mediaStop(let target): stop(target)
        case .volumeSet(let level): setVolume(level)
        case .volumeChange(let delta): setVolume(volume + delta)
        case .alarmStop, .screenWake, .screenSleep, .unknown: break
        }
    }

    public func pause(_ target: MediaTarget) {
        guard let player = player(for: target) else { return }
        player.pause()
        if nowPlayingKind == target {
            isPaused = true
            pausedForAlarm = false
        }
    }

    public func resume(_ target: MediaTarget) {
        guard let player = player(for: target) else { return }
        player.resume()
        if nowPlayingKind == target {
            isPaused = false
            pausedForAlarm = false
        }
    }

    public func next(_ target: MediaTarget) {
        guard target == .sound else { return }
        sound?.replay()
    }

    public func stop(_ target: MediaTarget) {
        guard let player = player(for: target) else { return }
        player.stop()
        playerEnded(target)
    }

    public func setVolume(_ level: Int) {
        volume = min(100, max(0, level))
        applyGain()
    }

    /// Hook for the listening loop (#22): lowers Coco's media while it listens.
    public func setDucked(_ ducked: Bool) {
        guard ducked != isDucked else { return }
        isDucked = ducked
        applyGain()
    }

    // MARK: - Inputs from the rest of the app

    /// A `sound` view was applied.
    public func soundRequested() {
        soundRequestCount += 1
    }

    /// The Hub's `now_playing` (from `hello` and `now_playing` events).
    public func nowPlayingChanged(_ nowPlaying: NowPlaying?) {
        hubMusicPlaying = nowPlaying?.isPlaying ?? false
    }

    /// Feed `TimerStore.isAlarmRinging` on every change.
    public func alarmChanged(ringing: Bool) {
        guard ringing != alarmRinging else { return }
        alarmRinging = ringing
        if ringing {
            guard let kind = nowPlayingKind, !isPaused, let player = player(for: kind) else { return }
            player.pause()
            isPaused = true
            pausedForAlarm = true
        } else if pausedForAlarm {
            pausedForAlarm = false
            guard let kind = nowPlayingKind, let player = player(for: kind) else { return }
            player.resume()
            isPaused = false
        }
    }

    // MARK: - Private

    private func player(for target: MediaTarget) -> (any MediaPlayerControl)? {
        switch target {
        case .youtube: youtube
        case .sound: sound
        case .music: nil
        }
    }

    private func clear(_ kind: MediaTarget) {
        guard nowPlayingKind == kind else { return }
        nowPlayingKind = nil
        isPaused = false
        pausedForAlarm = false
    }

    private func applyGain() {
        let gain = effectiveGain
        youtube?.setGain(gain)
        sound?.setGain(gain)
    }
}
