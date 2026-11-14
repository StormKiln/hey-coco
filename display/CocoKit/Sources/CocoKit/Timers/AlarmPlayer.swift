import AVFoundation
import AudioToolbox
import Foundation
import Observation

/// Plays the alarm: loud and looping until `stop()`. `isRinging` is what voice "stop" (#23) and
/// `UtteranceRequest.context.alarm_ringing` read.
@MainActor
public protocol AlarmPlayer: AnyObject {
    var isRinging: Bool { get }
    func start()
    func stop()
}

/// Tracks `isRinging` without making a sound (tests, previews).
@MainActor
public final class SilentAlarmPlayer: AlarmPlayer {
    public private(set) var isRinging = false
    public init() {}
    public func start() { isRinging = true }
    public func stop() { isRinging = false }
}

/// `AlarmPlayer` over `AVAudioPlayer` looping the synthesised `AlarmTone` at full volume.
///
/// A watchdog restarts playback if something else (an audio interruption, a route change)
/// stopped it while the alarm should still be ringing. If the audio file can't be played at all,
/// it falls back to repeating a system alert sound: an alarm must never be silent.
@MainActor
@Observable
public final class AudioAlarmPlayer: AlarmPlayer {
    public private(set) var isRinging = false

    @ObservationIgnored private var player: AVAudioPlayer?
    @ObservationIgnored private var watchdog: Task<Void, Never>?

    public init() {}

    public func start() {
        guard !isRinging else { return }
        isRinging = true
        activateSession()
        player = makePlayer()
        player?.play()
        watchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard let self, self.isRinging, !Task.isCancelled else { return }
                if let player = self.player {
                    if !player.isPlaying {
                        self.activateSession()
                        player.play()
                    }
                } else {
                    AudioServicesPlayAlertSound(SystemSoundID(1005))
                }
            }
        }
        if player == nil { AudioServicesPlayAlertSound(SystemSoundID(1005)) }
    }

    public func stop() {
        guard isRinging else { return }
        isRinging = false
        watchdog?.cancel()
        watchdog = nil
        player?.stop()
        player = nil
        #if os(iOS)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    private func makePlayer() -> AVAudioPlayer? {
        let player: AVAudioPlayer?
        if let url = try? AlarmTone.installNotificationSound() {
            player = try? AVAudioPlayer(contentsOf: url)
        } else {
            player = try? AVAudioPlayer(data: AlarmTone.wav(duration: AlarmTone.patternDuration * 4))
        }
        player?.numberOfLoops = -1
        player?.volume = 1
        player?.prepareToPlay()
        return player
    }

    private func activateSession() {
        #if os(iOS)
            // `.playback` ignores the silent switch and interrupts other audio (music pauses).
            let session = AVAudioSession.sharedInstance()
            try? session.setCategory(.playback, mode: .default)
            try? session.setActive(true)
        #endif
    }
}
