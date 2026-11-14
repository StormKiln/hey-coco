@preconcurrency import AVFoundation
import Foundation

/// The single owner of `AVAudioSession` while the voice loop runs (#22). No-ops on macOS, where
/// there is no app audio session.
///
/// - Standby: `.playAndRecord` + `.defaultToSpeaker` + `.mixWithOthers`, so the wake word can
///   listen while Coco's (or Spotify's) audio keeps playing at full volume. Echo cancellation
///   comes from voice processing on each mic engine (`AVAudioInputNode.setVoiceProcessingEnabled`,
///   see `PCMFrameMicrophone` / `AppleSpeechRecognizer`), not from the session mode: `.voiceChat`
///   would also make media quieter and mono.
/// - Conversation: the same plus `.duckOthers`.
/// - Interruptions, route changes and media-server resets are reported through `onEvent`;
///   `VoiceLoop` restarts the wake-word engine.
///
/// `AudioAlarmPlayer` defers to it (`prepareForAlarm()` / `alarmDidStop()`) so a ringing alarm
/// doesn't switch the session to `.playback` and cut the mic the bare-"stop" listener needs.
@MainActor
public final class AudioSessionCoordinator: VoiceAudioSession {
    public static let shared = AudioSessionCoordinator()

    private enum Mode { case inactive, standby, conversation }

    public var onEvent: (@MainActor (AudioSessionEvent) -> Void)?
    private var mode: Mode = .inactive
    private var chime: AVAudioPlayer?
    private var observers: [any NSObjectProtocol] = []

    private init() {
        chime = try? AVAudioPlayer(data: ListeningChime.wavData())
        chime?.prepareToPlay()
        observe()
    }

    /// True while the voice loop holds the session (the alarm player then leaves it alone).
    public var isManaging: Bool { mode != .inactive }

    public func activateStandby() throws {
        guard mode != .standby else { return }
        try apply(duckOthers: false)
        mode = .standby
    }

    public func beginConversation() throws {
        try apply(duckOthers: true)
        mode = .conversation
    }

    public func playListeningChime() async {
        guard let chime else { return }
        chime.currentTime = 0
        chime.play()
        try? await Task.sleep(for: .seconds(chime.duration))
    }

    public func deactivate() {
        guard mode != .inactive else { return }
        mode = .inactive
        #if os(iOS)
            // Fails ("session busy") while something still plays, e.g. the alarm; it stays
            // active then, which is harmless.
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    /// The alarm is about to play. Returns true if the coordinator owns the session (it is made
    /// active as-is); false means the alarm player should configure the session itself.
    public func prepareForAlarm() -> Bool {
        guard isManaging else { return false }
        #if os(iOS)
            try? AVAudioSession.sharedInstance().setActive(true)
        #endif
        return true
    }

    /// The alarm stopped. Returns true if the coordinator owns the session (the alarm player must
    /// not deactivate it: the wake word is still listening).
    public func alarmDidStop() -> Bool { isManaging }

    // MARK: - Private

    private func apply(duckOthers: Bool) throws {
        #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            var options: AVAudioSession.CategoryOptions = [.defaultToSpeaker]
            options.insert(duckOthers ? .duckOthers : .mixWithOthers)
            try session.setCategory(.playAndRecord, mode: .default, options: options)
            try session.setActive(true)
        #endif
    }

    private func observe() {
        #if os(iOS)
            let center = NotificationCenter.default
            let session = AVAudioSession.sharedInstance()
            observers.append(
                center.addObserver(
                    forName: AVAudioSession.interruptionNotification, object: session, queue: .main
                ) { note in
                    let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                    let began = raw.flatMap(AVAudioSession.InterruptionType.init) == .began
                    MainActor.assumeIsolated {
                        let coordinator = AudioSessionCoordinator.shared
                        if began { coordinator.mode = .inactive }
                        coordinator.onEvent?(began ? .interruptionBegan : .interruptionEnded)
                    }
                })
            observers.append(
                center.addObserver(
                    forName: AVAudioSession.routeChangeNotification, object: session, queue: .main
                ) { note in
                    let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
                    let reason = raw.flatMap(AVAudioSession.RouteChangeReason.init)
                    // Our own category changes also post route changes; only react to hardware.
                    guard
                        [.newDeviceAvailable, .oldDeviceUnavailable, .override, .routeConfigurationChange]
                            .contains(reason)
                    else { return }
                    MainActor.assumeIsolated {
                        AudioSessionCoordinator.shared.onEvent?(.routeChanged)
                    }
                })
            observers.append(
                center.addObserver(
                    forName: AVAudioSession.mediaServicesWereResetNotification, object: session,
                    queue: .main
                ) { _ in
                    MainActor.assumeIsolated {
                        let coordinator = AudioSessionCoordinator.shared
                        coordinator.mode = .inactive
                        coordinator.onEvent?(.mediaServicesReset)
                    }
                })
        #endif
    }
}
