import Foundation

/// One update from a speech recognizer: a live partial guess, or the final transcript.
public struct SpeechTranscript: Sendable, Equatable {
    public var text: String
    public var isFinal: Bool

    public init(text: String, isFinal: Bool) {
        self.text = text
        self.isFinal = isFinal
    }

    public static func partial(_ text: String) -> SpeechTranscript { .init(text: text, isFinal: false) }
    public static func final(_ text: String) -> SpeechTranscript { .init(text: text, isFinal: true) }
}

/// Whether the Display may listen, and if not, why.
public enum SpeechPermission: Sendable, Equatable {
    case granted
    case microphoneDenied
    case speechRecognitionDenied
    /// Blocked by Screen Time / device management; Settings can't fix it from the app.
    case restricted
    /// No recognizer for this language right now (e.g. offline with no on-device model).
    case recognizerUnavailable
}

public enum SpeechRecognitionError: Error, Sendable, Equatable {
    case unavailable
    case failed(String)
}

/// Turns speech into text. `start()` begins listening and returns a stream of partial transcripts
/// that ends with one final transcript once the speaker stops (end-of-utterance is the
/// recognizer's job). A stream that finishes without a final means "nothing more was heard".
@MainActor
public protocol SpeechRecognizer: AnyObject {
    /// Asks for microphone and speech-recognition access (prompts on first use).
    func requestPermission() async -> SpeechPermission
    func start() throws -> AsyncThrowingStream<SpeechTranscript, any Error>
    /// Stops capturing audio now; the stream still delivers the final transcript.
    func stop()
    /// Abandons recognition; the stream finishes without a final transcript.
    func cancel()
}

/// Speaks replies. `speak` returns once speech finishes or is stopped.
@MainActor
public protocol SpeechSynthesizer: AnyObject {
    func speak(_ text: String) async
    func stop()
}

/// Audio-session plumbing around a conversation: claiming the mic/speaker (ducking other audio)
/// and the "I'm listening" chime.
@MainActor
public protocol ConversationAudio: AnyObject {
    func beginConversation() throws
    /// Plays the listening chime and returns when it has finished (so the mic doesn't hear it).
    func playListeningChime() async
    func endConversation()
}

/// `ConversationAudio` that does nothing (previews, tests).
@MainActor
public final class SilentConversationAudio: ConversationAudio {
    public init() {}
    public func beginConversation() throws {}
    public func playListeningChime() async {}
    public func endConversation() {}
}
