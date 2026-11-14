import Foundation
import Observation

/// Where the conversation is, for the listening overlay and mic button.
public enum ConversationState: Sendable, Equatable {
    case idle
    /// Mic open; `partial` is the live transcript so far.
    case listening(partial: String)
    /// Waiting for the Hub.
    case thinking(heard: String)
    /// Speaking the Action's `say`.
    case speaking(reply: String)
    /// Something went wrong; shown (and, for Hub errors, spoken) briefly, then back to idle.
    case error(message: String)
    /// Mic or speech access is missing; stays until the next tap or `cancel()`.
    case permissionNeeded(SpeechPermission)

    /// True while a request is in flight (listening, thinking or speaking).
    public var isActive: Bool {
        switch self {
        case .listening, .thinking, .speaking: true
        case .idle, .error, .permissionNeeded: false
        }
    }
}

/// What the conversation needs from the rest of the app. `AppModel` is the real one.
@MainActor
public protocol ConversationHost: AnyObject {
    var currentView: ViewSpec { get }
    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action
    /// Runs one of `Action.commands`.
    func perform(_ command: Command)
    /// Applies `Action.view` (and records `say`).
    func apply(_ action: Action)
}

/// The request loop: listen → send the transcript to the Hub → run commands, show the view →
/// speak `say` → idle. Triggered by a tap today and by the wake word later (`wakeWordDetected()`).
@MainActor
@Observable
public final class ConversationController {
    public static let hubUnreachableMessage = "I can't reach the Coco Hub right now."
    static let unauthorizedMessage = "Coco Hub needs a valid token. Check Settings."
    static let hubFailedMessage = "Sorry, something went wrong at the Coco Hub."
    static let didNotHearMessage = "Sorry, I couldn't hear that."

    public private(set) var state: ConversationState = .idle

    @ObservationIgnored private let host: any ConversationHost
    @ObservationIgnored private let recognizer: any SpeechRecognizer
    @ObservationIgnored private let synthesizer: any SpeechSynthesizer
    @ObservationIgnored private let audio: any ConversationAudio
    @ObservationIgnored private let messageHold: Duration
    @ObservationIgnored private let sleep: @Sendable (Duration) async throws -> Void
    @ObservationIgnored private var run: Task<Void, Never>?
    /// Bumped on every start/cancel; a run only touches state while its id is current.
    @ObservationIgnored private var runID = 0

    /// - Parameter messageHold: how long an error stays on screen before returning to idle.
    public init(
        host: any ConversationHost,
        recognizer: any SpeechRecognizer,
        synthesizer: any SpeechSynthesizer,
        audio: any ConversationAudio = SilentConversationAudio(),
        messageHold: Duration = .seconds(4),
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.host = host
        self.recognizer = recognizer
        self.synthesizer = synthesizer
        self.audio = audio
        self.messageHold = messageHold
        self.sleep = sleep
    }

    // MARK: - Triggers

    /// The mic button: starts listening, or cancels whatever is in progress (second tap).
    public func micTapped() {
        if state.isActive {
            cancel()
        } else {
            startListening()
        }
    }

    /// Starts a new request unless one is already in progress. Clears an error or permission
    /// message first.
    public func startListening() {
        guard !state.isActive else { return }
        begin()
    }

    /// Hook for the wake word (#22): like a tap, but barges in on a reply being spoken.
    public func wakeWordDetected() {
        begin()
    }

    /// Abandons the current request (if any) and returns to idle. Whatever the Hub already
    /// applied stays on screen.
    public func cancel() {
        let wasRunning = run != nil || state != .idle
        stopRun()
        state = .idle
        if wasRunning { audio.endConversation() }
    }

    /// Waits for the current run to end (tests, and callers that need to sequence after it).
    public func waitUntilFinished() async {
        await run?.value
    }

    // MARK: - The loop

    private func begin() {
        stopRun()
        let id = runID
        run = Task { [weak self] in await self?.converse(id) }
    }

    private func stopRun() {
        runID += 1
        run?.cancel()
        recognizer.cancel()
        synthesizer.stop()
    }

    private func isCurrent(_ id: Int) -> Bool { id == runID && !Task.isCancelled }

    private func converse(_ id: Int) async {
        let permission = await recognizer.requestPermission()
        guard isCurrent(id) else { return }
        guard permission == .granted else {
            state = .permissionNeeded(permission)
            return
        }

        let transcript: String
        do {
            try audio.beginConversation()
            state = .listening(partial: "")
            await audio.playListeningChime()
            guard isCurrent(id) else { return }
            let stream = try recognizer.start()
            var heard = ""
            for try await update in stream {
                guard isCurrent(id) else { return }
                heard = update.text
                if update.isFinal { break }
                state = .listening(partial: update.text)
            }
            guard isCurrent(id) else { return }
            transcript = heard.trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            guard isCurrent(id) else { return }
            await fail(id, message: Self.didNotHearMessage, speak: false)
            return
        }

        guard !transcript.isEmpty else {
            finish(id)
            return
        }

        state = .thinking(heard: transcript)
        let request = UtteranceRequest(
            text: transcript, source: .voice,
            context: UtteranceContext(
                view: host.currentView.viewType ?? .idle, alarmRinging: false, mediaPlaying: nil))
        let action: Action
        do throws(HubError) {
            action = try await host.postUtterance(request)
        } catch {
            guard isCurrent(id) else { return }
            await fail(id, message: Self.message(for: error), speak: true)
            return
        }
        guard isCurrent(id) else { return }

        // Protocol: commands first, then the view, then speech.
        for command in action.commands { host.perform(command) }
        host.apply(action)
        if let say = action.say?.trimmingCharacters(in: .whitespacesAndNewlines), !say.isEmpty {
            state = .speaking(reply: say)
            await synthesizer.speak(say)
            guard isCurrent(id) else { return }
        }
        finish(id)
    }

    private func fail(_ id: Int, message: String, speak: Bool) async {
        state = .error(message: message)
        if speak {
            await synthesizer.speak(message)
            guard isCurrent(id) else { return }
        }
        try? await sleep(messageHold)
        guard isCurrent(id) else { return }
        finish(id)
    }

    private func finish(_ id: Int) {
        guard isCurrent(id) else { return }
        state = .idle
        audio.endConversation()
    }

    static func message(for error: HubError) -> String {
        switch error {
        case .unreachable: hubUnreachableMessage
        case .unauthorized: unauthorizedMessage
        case .server, .decoding: hubFailedMessage
        }
    }
}

extension ConversationController {
    /// The real wiring: Apple speech recognition and synthesis, the system audio session.
    public static func live(host: any ConversationHost) -> ConversationController {
        ConversationController(
            host: host,
            recognizer: AppleSpeechRecognizer(),
            synthesizer: AppleSpeechSynthesizer(),
            audio: AppleConversationAudio())
    }
}
