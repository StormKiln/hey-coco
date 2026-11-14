import AVFoundation
import Foundation
import Observation

/// Plays one downloaded sound clip with `AVAudioPlayer` (the clip comes from the Hub with a
/// bearer token, so it is loaded as data, not streamed from a URL). Reports to `MediaCenter`.
///
/// It does not configure the audio session: the session belongs to the listening loop (#22).
@MainActor
@Observable
public final class SoundClipPlayer: NSObject, SoundPlaying {
    public private(set) var isPlaying = false
    /// Bumped each time a play reaches its end, so the screen can start its idle countdown.
    public private(set) var finishCount = 0

    @ObservationIgnored private weak var media: MediaCenter?
    @ObservationIgnored private var player: AVAudioPlayer?
    @ObservationIgnored private var gain: Float = 1

    public init(media: MediaCenter) {
        self.media = media
        super.init()
    }

    public var isLoaded: Bool { player != nil }

    /// Prepares `data` (an m4a/mp3/wav clip). Throws if it can't be decoded.
    public func load(_ data: Data) throws {
        player?.stop()
        player = nil
        isPlaying = false
        let player = try AVAudioPlayer(data: data)
        player.delegate = self
        player.volume = gain
        player.prepareToPlay()
        self.player = player
    }

    /// Plays from the start.
    public func replay() {
        guard let player else { return }
        player.currentTime = 0
        player.volume = gain
        if player.play() {
            isPlaying = true
            media?.playerStarted(.sound)
        }
    }

    public func pause() {
        guard let player, player.isPlaying else { return }
        player.pause()
        isPlaying = false
        media?.playerPaused(.sound)
    }

    public func resume() {
        guard let player, !player.isPlaying else { return }
        if player.play() {
            isPlaying = true
            media?.playerStarted(.sound)
        }
    }

    /// Stops; counts as finished (the screen then heads back to idle).
    public func stop() {
        guard let player else { return }
        let wasActive = player.isPlaying || player.currentTime > 0
        player.stop()
        player.currentTime = 0
        isPlaying = false
        if wasActive { finishCount += 1 }
    }

    public func setGain(_ gain: Double) {
        self.gain = Float(min(1, max(0, gain)))
        player?.volume = self.gain
    }

    private func didFinish() {
        isPlaying = false
        finishCount += 1
        media?.playerEnded(.sound)
    }
}

extension SoundClipPlayer: AVAudioPlayerDelegate {
    nonisolated public func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in self?.didFinish() }
    }

    nonisolated public func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: (any Error)?) {
        Task { @MainActor [weak self] in self?.didFinish() }
    }
}
