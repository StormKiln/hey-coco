import Foundation
import Observation

/// The listening state machine (#22): the single owner of the microphone.
///
/// Between requests the mic belongs to a *standby* listener: the wake-word engine, or, while an
/// alarm rings, the bare-"stop" listener (#23). On "hey coco" (or a tap) the standby listener is
/// stopped **before** the speech recognizer opens the mic, and restarted as soon as the recognizer
/// is done, so a wake word while Coco is thinking or speaking barges in. Other audio is ducked
/// from wake until the reply has been spoken.
///
/// ```
/// idle(armed) ──wake/tap──▶ woken (chime) ──▶ listening ──▶ thinking ──▶ speaking ──▶ idle(armed)
///                              ▲                                 │            │
///                              └──────────── wake word (barge-in) ┴────────────┘
/// ```
/// Interruptions (Siri, calls) and route changes stop and re-arm the standby listener; a failed
/// start re-arms after an exponential backoff.
@MainActor
@Observable
public final class VoiceLoop {
    public enum WakeWordStatus: Sendable, Equatable {
        /// Not in this build / no mic access: tap-to-talk only.
        case unavailable(reason: String)
        /// Listening for "hey coco".
        case armed
        /// Temporarily off: a conversation, a ringing alarm or an interruption has the mic.
        case paused
        /// Failed to start; trying again after the given delay.
        case retrying(after: Duration)
    }

    public enum State: Sendable, Equatable {
        case idle(armed: Bool)
        /// Wake word heard (or mic tapped): chime playing, recognizer about to start.
        case woken
        case listening
        case thinking
        case speaking
    }

    public static let micDeniedReason = "Wake word off: microphone access is off."

    /// The request loop; the UI drives tap-to-talk and shows its state.
    public let conversation: ConversationController
    public private(set) var wakeWord: WakeWordStatus
    /// True while the bare-"stop" listener has the mic (only ever while an alarm rings).
    public private(set) var stopWordActive = false
    /// True from wake until the reply has been spoken: media players lower their volume.
    public private(set) var ducking = false

    /// Where the loop is, for UI and media.
    public var state: State {
        switch conversation.state {
        case .listening: recognizerActive ? .listening : .woken
        case .thinking: .thinking
        case .speaking: .speaking
        case .idle, .error, .permissionNeeded: .idle(armed: wakeWord == .armed)
        }
    }

    private enum Standby { case none, wakeWord, stopWord }

    private var recognizerActive = false
    @ObservationIgnored private var standby: Standby = .none
    @ObservationIgnored private let host: any ConversationHost
    @ObservationIgnored private let engine: (any WakeWordEngine)?
    @ObservationIgnored private let unavailableReason: String
    @ObservationIgnored private let stopWord: any StopWordListener
    @ObservationIgnored private let session: any VoiceAudioSession
    @ObservationIgnored private let requestMicPermission: @MainActor () async -> Bool
    @ObservationIgnored private let retryBackoff: Duration
    @ObservationIgnored private let maxBackoff: Duration
    @ObservationIgnored private let sleep: @Sendable (Duration) async throws -> Void

    @ObservationIgnored private var started = false
    @ObservationIgnored private var micGranted = false
    @ObservationIgnored private var interrupted = false
    @ObservationIgnored private var alarmRinging = false
    /// From the start of a turn until its recognizer is done.
    @ObservationIgnored private var micHeldByConversation = false
    /// Bumped per turn so a late "recognizer ended" from an abandoned turn is ignored.
    @ObservationIgnored private(set) var turn = 0
    @ObservationIgnored private var nextBackoff: Duration
    @ObservationIgnored private var retryTask: Task<Void, Never>?

    public init(
        host: any ConversationHost,
        wakeWord: WakeWordAvailability,
        stopWord: any StopWordListener,
        recognizer: any SpeechRecognizer,
        synthesizer: any SpeechSynthesizer,
        session: any VoiceAudioSession,
        requestMicPermission: @escaping @MainActor () async -> Bool,
        retryBackoff: Duration = .seconds(2),
        maxBackoff: Duration = .seconds(60),
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        switch wakeWord {
        case .available(let engine):
            self.engine = engine
            self.unavailableReason = ""
            self.wakeWord = .paused
        case .unavailable(let reason):
            self.engine = nil
            self.unavailableReason = reason
            self.wakeWord = .unavailable(reason: reason)
        }
        self.host = host
        self.stopWord = stopWord
        self.session = session
        self.requestMicPermission = requestMicPermission
        self.retryBackoff = retryBackoff
        self.maxBackoff = maxBackoff
        self.nextBackoff = retryBackoff
        self.sleep = sleep

        let audio = LoopConversationAudio(session: session)
        let handoff = HandoffRecognizer(base: recognizer)
        conversation = ConversationController(
            host: host, recognizer: handoff, synthesizer: synthesizer, audio: audio)
        audio.loop = self
        handoff.loop = self
    }

    // MARK: - Lifecycle

    /// Asks for the mic (if there is a wake word to run) and arms the standby listener.
    public func start() async {
        guard !started else { return }
        started = true
        session.onEvent = { [weak self] event in self?.handle(event) }
        if engine != nil { micGranted = await requestMicPermission() }
        reconcile()
    }

    /// Back in the foreground: the system may have stopped the mic without telling us.
    public func resume() async {
        guard started else { return }
        interrupted = false
        if engine != nil { micGranted = await requestMicPermission() }
        reconcile(restart: true)
    }

    /// Releases the mic and the audio session.
    public func stop() {
        guard started else { return }
        conversation.cancel()
        started = false
        reconcile()
    }

    /// Feed the app's "an alarm is ringing" state on every change.
    public func alarmRingingChanged(_ ringing: Bool) {
        guard alarmRinging != ringing else { return }
        alarmRinging = ringing
        reconcile()
    }

    // MARK: - Standby listener

    private var desiredStandby: Standby {
        guard started, !interrupted, !micHeldByConversation else { return .none }
        if alarmRinging { return .stopWord }
        if engine != nil, micGranted { return .wakeWord }
        return .none
    }

    /// Makes the running standby listener match what is wanted. `restart` restarts it even if it
    /// is already the right one (its audio engine may have been stopped by the system).
    private func reconcile(restart: Bool = false) {
        let wanted = desiredStandby
        if wanted == standby, !restart || wanted == .none {
            refreshWakeWordStatus()
            return
        }
        stopStandby()
        guard wanted != .none else {
            if !ducking { session.deactivate() }
            refreshWakeWordStatus()
            return
        }
        do {
            // During a conversation the session is already active (ducking).
            if !ducking { try session.activateStandby() }
            switch wanted {
            case .wakeWord:
                try engine?.start { [weak self] in self?.wakeWordHeard() }
            case .stopWord:
                try stopWord.start { [weak self] in self?.stopWordHeard() }
                stopWordActive = true
            case .none:
                break
            }
            standby = wanted
            retryTask?.cancel()
            retryTask = nil
            nextBackoff = retryBackoff
            refreshWakeWordStatus()
        } catch {
            scheduleRetry(for: wanted)
        }
    }

    private func stopStandby() {
        switch standby {
        case .wakeWord: engine?.stop()
        case .stopWord: stopWord.stop()
        case .none: break
        }
        standby = .none
        stopWordActive = false
    }

    private func scheduleRetry(for wanted: Standby) {
        let delay = nextBackoff
        nextBackoff = min(nextBackoff * 2, maxBackoff)
        if wanted == .wakeWord { wakeWord = .retrying(after: delay) }
        retryTask?.cancel()
        retryTask = Task { [weak self, sleep] in
            do { try await sleep(delay) } catch { return }
            guard let self, !Task.isCancelled else { return }
            self.retryTask = nil
            self.reconcile()
        }
    }

    private func refreshWakeWordStatus() {
        if engine == nil {
            wakeWord = .unavailable(reason: unavailableReason)
        } else if started, !micGranted {
            wakeWord = .unavailable(reason: Self.micDeniedReason)
        } else if standby == .wakeWord {
            wakeWord = .armed
        } else if case .retrying = wakeWord, retryTask != nil, desiredStandby == .wakeWord {
            // Keep showing the pending retry.
        } else {
            wakeWord = .paused
        }
    }

    private func handle(_ event: AudioSessionEvent) {
        switch event {
        case .interruptionBegan:
            interrupted = true
            conversation.cancel()
            reconcile()
        case .interruptionEnded:
            interrupted = false
            reconcile(restart: true)
        case .routeChanged:
            reconcile(restart: true)
        case .mediaServicesReset:
            conversation.cancel()
            reconcile(restart: true)
        }
    }

    // MARK: - Triggers

    private func wakeWordHeard() {
        guard standby == .wakeWord else { return }
        conversation.wakeWordDetected()
    }

    private func stopWordHeard() {
        guard standby == .stopWord, alarmRinging else { return }
        stopStandby()
        // Silence now, then tell the Hub so it dismisses its timers too.
        host.perform(.alarmStop)
        let request = UtteranceRequest(
            text: "stop", source: .voice,
            context: UtteranceContext(
                view: host.currentView.viewType ?? .idle, alarmRinging: true, mediaPlaying: nil))
        Task { [weak self, host] in
            do throws(HubError) {
                let action = try await host.postUtterance(request)
                for command in action.commands { host.perform(command) }
                host.apply(Action(say: nil, view: action.view, commands: [], intent: action.intent, matchedBy: action.matchedBy))
            } catch {
                // Already silent locally; the timer store syncs the dismissal when it can.
            }
            // Still ringing (another timer)? Listen again.
            self?.reconcile()
        }
    }

    // MARK: - Conversation hooks

    fileprivate func conversationWillBegin() throws {
        turn += 1
        micHeldByConversation = true
        ducking = true
        reconcile()
        try session.beginConversation()
    }

    fileprivate func conversationDidEnd() {
        micHeldByConversation = false
        recognizerActive = false
        stopStandby()
        ducking = false
        session.deactivate()
        reconcile()
    }

    fileprivate func recognizerWillStart() -> Int {
        micHeldByConversation = true
        recognizerActive = true
        reconcile()
        return turn
    }

    fileprivate func recognizerDidEnd(turn: Int) {
        guard turn == self.turn, recognizerActive else { return }
        recognizerActive = false
        micHeldByConversation = false
        reconcile()
    }

    fileprivate func playListeningChime() async {
        await session.playListeningChime()
    }
}

/// `ConversationAudio` for the loop's controller: hands the mic over and ducks.
@MainActor
private final class LoopConversationAudio: ConversationAudio {
    weak var loop: VoiceLoop?
    let session: any VoiceAudioSession

    init(session: any VoiceAudioSession) { self.session = session }

    func beginConversation() throws { try loop?.conversationWillBegin() }
    func playListeningChime() async { await loop?.playListeningChime() }
    func endConversation() { loop?.conversationDidEnd() }
}

/// Wraps the conversation's recognizer so the loop knows exactly when it holds the mic: the
/// standby listener is stopped before `start()` and restarted when the stream ends.
@MainActor
private final class HandoffRecognizer: SpeechRecognizer {
    weak var loop: VoiceLoop?
    let base: any SpeechRecognizer
    private var activeTurn: Int?
    private var forward: Task<Void, Never>?

    init(base: any SpeechRecognizer) { self.base = base }

    func requestPermission() async -> SpeechPermission { await base.requestPermission() }

    func start() throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        let turn = loop?.recognizerWillStart() ?? 0
        activeTurn = turn
        let inner: AsyncThrowingStream<SpeechTranscript, any Error>
        do {
            inner = try base.start()
        } catch {
            ended(turn)
            throw error
        }
        let (stream, continuation) = AsyncThrowingStream.makeStream(of: SpeechTranscript.self)
        let forward = Task { [weak self] in
            do {
                for try await update in inner {
                    continuation.yield(update)
                    if update.isFinal { break }
                }
                continuation.finish()
            } catch {
                continuation.finish(throwing: error)
            }
            self?.ended(turn)
        }
        self.forward = forward
        continuation.onTermination = { _ in forward.cancel() }
        return stream
    }

    func stop() { base.stop() }

    func cancel() {
        base.cancel()
        forward?.cancel()
        forward = nil
        if let activeTurn { ended(activeTurn) }
    }

    private func ended(_ turn: Int) {
        guard activeTurn == turn else { return }
        activeTurn = nil
        loop?.recognizerDidEnd(turn: turn)
    }
}

