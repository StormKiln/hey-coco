import Foundation

/// What a `ConversationController` turn went through, for measurement (the voice test, #6).
/// Carries no audio; the transcript is only passed so its length can be counted.
public enum ConversationEvent: Sendable, Equatable {
    /// Permission granted, the mic is opening.
    case started
    /// A non-empty partial transcript arrived (the person is still talking).
    case heardSpeech
    /// The recognizer is done: the final transcript, trimmed ("" when nothing was heard).
    case finalTranscript(String)
    /// The recognizer failed ("Sorry, I couldn't hear that.").
    case recognitionFailed
    /// The Hub answered the utterance (or failed to).
    case hubReplied(succeeded: Bool)
    /// The turn is over: finished, failed or cancelled. A barge-in (wake word during a turn)
    /// starts the next turn without ending this one.
    case ended
}
