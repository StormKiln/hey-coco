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

/// The alarm's sound and its audio-session work. Every method is called on `AudioSessionQueue`,
/// never on the main thread, and may block there.
public protocol AlarmSound: AnyObject, Sendable {
    /// Configures the session (just activates it when the voice loop owns it) and starts looping.
    /// Returns whether it is playing.
    func start(voiceOwnsSession: Bool) -> Bool
    /// Restarts playback if something (an interruption, a route change) stopped it. Returns
    /// whether it is playing.
    func ensurePlaying(voiceOwnsSession: Bool) -> Bool
    /// Stops; releases the session unless the voice loop owns it (the wake word still listens).
    func stop(voiceOwnsSession: Bool)
}

/// The system alert sound the alarm falls back to.
public let cocoAlertSound: @MainActor @Sendable () -> Void = {
    AudioServicesPlayAlertSound(SystemSoundID(1005))
}

/// `AlarmPlayer` looping the synthesised `AlarmTone` at full volume.
///
/// `start()` never waits for audio: `isRinging` (and so the alarm overlay) flips at once on the
/// main actor, while the session and `AVAudioPlayer` work runs on `AudioSessionQueue` (see
/// `AlarmSound`). The alarm does not depend on the voice loop: the order is TimerStore fires →
/// `start()` → overlay, and only afterwards does the voice loop (`alarmRingingChanged`) swap the
/// wake word for the stop word, in the background.
///
/// A watchdog checks every `watchdogInterval`. Until the queue has confirmed the tone is playing
/// (the session work is slow, wedged behind another session call, or the tone can't play at all)
/// it plays the system alert sound instead: an alarm must never be silent. It also restarts the
/// tone if something else stopped it.
@MainActor
@Observable
public final class AudioAlarmPlayer: AlarmPlayer {
    public private(set) var isRinging = false

    @ObservationIgnored private let sound: any AlarmSound
    @ObservationIgnored private let queue: AudioSessionQueue
    @ObservationIgnored private let voiceOwnsSession: @MainActor () -> Bool
    @ObservationIgnored private let alertSound: @MainActor () -> Void
    @ObservationIgnored private let sleep: @Sendable (Duration) async throws -> Void
    @ObservationIgnored private let watchdogInterval: Duration
    @ObservationIgnored private var watchdog: Task<Void, Never>?
    /// Bumped on every start / stop, so a late report from the queue is ignored.
    @ObservationIgnored private var generation = 0
    /// The queue confirmed the tone is playing.
    @ObservationIgnored private var confirmedPlaying = false
    /// A start / ensure is queued and hasn't reported back yet (don't pile more behind it).
    @ObservationIgnored private var queued = false

    public convenience init() {
        self.init(
            sound: AVAlarmSound(), queue: .shared,
            voiceOwnsSession: { AudioSessionCoordinator.shared.isManaging },
            alertSound: cocoAlertSound, sleep: cocoRealSleep)
    }

    init(
        sound: any AlarmSound,
        queue: AudioSessionQueue,
        voiceOwnsSession: @escaping @MainActor () -> Bool,
        alertSound: @escaping @MainActor () -> Void,
        sleep: @escaping @Sendable (Duration) async throws -> Void,
        watchdogInterval: Duration = .seconds(1)
    ) {
        self.sound = sound
        self.queue = queue
        self.voiceOwnsSession = voiceOwnsSession
        self.alertSound = alertSound
        self.sleep = sleep
        self.watchdogInterval = watchdogInterval
    }

    public func start() {
        guard !isRinging else { return }
        isRinging = true
        generation += 1
        confirmedPlaying = false
        let owns = voiceOwnsSession()
        enqueue { sound in sound.start(voiceOwnsSession: owns) }
        watchdog = Task { [weak self, sleep, watchdogInterval] in
            while !Task.isCancelled {
                do { try await sleep(watchdogInterval) } catch { return }
                guard let self, self.isRinging, !Task.isCancelled else { return }
                self.check()
            }
        }
    }

    public func stop() {
        guard isRinging else { return }
        isRinging = false
        generation += 1
        watchdog?.cancel()
        watchdog = nil
        confirmedPlaying = false
        queued = false
        let owns = voiceOwnsSession()
        queue.enqueue { [sound] in sound.stop(voiceOwnsSession: owns) }
    }

    private func check() {
        if !confirmedPlaying { alertSound() }
        guard !queued else { return }
        let owns = voiceOwnsSession()
        enqueue { sound in sound.ensurePlaying(voiceOwnsSession: owns) }
    }

    private func enqueue(_ work: @escaping @Sendable (any AlarmSound) -> Bool) {
        queued = true
        let generation = generation
        queue.enqueue { [sound, weak self] in
            let playing = work(sound)
            Task { @MainActor in self?.report(playing, generation: generation) }
        }
    }

    private func report(_ playing: Bool, generation: Int) {
        guard generation == self.generation else { return }
        queued = false
        confirmedPlaying = playing
    }
}

/// `AlarmSound` over `AVAudioPlayer`. Only touched on `AudioSessionQueue` (hence unchecked).
public final class AVAlarmSound: AlarmSound, @unchecked Sendable {
    private var player: AVAudioPlayer?

    public init() {}

    public func start(voiceOwnsSession: Bool) -> Bool {
        activateSession(voiceOwnsSession: voiceOwnsSession)
        player = Self.makePlayer()
        player?.play()
        return player?.isPlaying ?? false
    }

    public func ensurePlaying(voiceOwnsSession: Bool) -> Bool {
        guard let player else { return false }
        if !player.isPlaying {
            activateSession(voiceOwnsSession: voiceOwnsSession)
            player.play()
        }
        return player.isPlaying
    }

    public func stop(voiceOwnsSession: Bool) {
        player?.stop()
        player = nil
        // The voice loop keeps the session for the wake word.
        guard !voiceOwnsSession else { return }
        #if os(iOS)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    private static func makePlayer() -> AVAudioPlayer? {
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

    private func activateSession(voiceOwnsSession: Bool) {
        #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            // While the voice loop owns the session (play-and-record, so a bare "stop" can be
            // heard over the alarm), don't switch it to `.playback`.
            if !voiceOwnsSession {
                // `.playback` ignores the silent switch and interrupts other audio (music pauses).
                try? session.setCategory(.playback, mode: .default)
            }
            try? session.setActive(true)
        #endif
    }
}
