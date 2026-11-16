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
/// Every `AVAudioSession` call runs on `AudioSessionQueue` (never the main thread): the async
/// methods return once the session has been configured, and the main actor stays free if that
/// takes long or never happens.
///
/// `AudioAlarmPlayer` reads `isManaging` so a ringing alarm doesn't switch the session to
/// `.playback` and cut the mic the bare-"stop" listener needs.
@MainActor
public final class AudioSessionCoordinator: VoiceAudioSession {
    public static let shared = AudioSessionCoordinator()

    private enum Mode { case inactive, standby, conversation }

    public var onEvent: (@MainActor (AudioSessionEvent) -> Void)?
    /// The mode last asked for (claimed at once, before the session work has run).
    private var mode: Mode = .inactive
    /// The session work for `mode`; a repeated `activateStandby()` joins it instead of queueing
    /// another call behind one that may be wedged.
    private var work: Task<Void, any Error>?
    private let queue: AudioSessionQueue
    private var chime: AVAudioPlayer?
    private var observers: [any NSObjectProtocol] = []

    private init(queue: AudioSessionQueue = .shared) {
        self.queue = queue
        chime = try? AVAudioPlayer(data: ListeningChime.wavData())
        chime?.prepareToPlay()
        observe()
    }

    /// True while the voice loop holds (or is claiming) the session; the alarm player then leaves
    /// the category alone.
    public var isManaging: Bool { mode != .inactive }

    public func activateStandby() async throws {
        if mode == .standby, let work {
            try await work.value
            return
        }
        try await claim(.standby, duckOthers: false)
    }

    public func beginConversation() async throws {
        try await claim(.conversation, duckOthers: true)
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
        work = nil
        #if os(iOS)
            queue.enqueue {
                // Fails ("session busy") while something still plays, e.g. the alarm; it stays
                // active then, which is harmless.
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            }
        #endif
    }

    // MARK: - Private

    private func claim(_ newMode: Mode, duckOthers: Bool) async throws {
        mode = newMode
        let task = Task { [queue] in try await queue.run { try Self.apply(duckOthers: duckOthers) } }
        work = task
        do {
            try await task.value
        } catch {
            if work == task {
                mode = .inactive
                work = nil
            }
            throw error
        }
    }

    private nonisolated static func apply(duckOthers: Bool) throws {
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
                        if began {
                            coordinator.mode = .inactive
                            coordinator.work = nil
                        }
                        coordinator.onEvent?(began ? .interruptionBegan : .interruptionEnded)
                    }
                })
            observers.append(
                center.addObserver(
                    forName: AVAudioSession.routeChangeNotification, object: session, queue: .main
                ) { note in
                    let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
                    // Our own category and voice-processing changes also post route changes;
                    // only react to hardware (#108).
                    guard AudioRouteChangePolicy.restartsListener(reasonRawValue: raw) else { return }
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
                        coordinator.work = nil
                        coordinator.onEvent?(.mediaServicesReset)
                    }
                })
        #endif
    }
}
