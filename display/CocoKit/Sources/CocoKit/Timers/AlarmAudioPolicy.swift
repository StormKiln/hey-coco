import Foundation

/// How the alarm shares audio with the voice loop (#97).
///
/// While an alarm rings and the voice loop runs, the mic belongs to the bare-"stop" listener
/// (`RecognizerStopWordListener`), which opens a fresh `AVAudioEngine` for every few-second
/// recognition window. With voice processing on, each window switches the app's audio I/O to the
/// voice-processing unit and back, and the alarm plays through it (AEC/AGC on a loud tone): the
/// likely source of the crackling and glitches heard on the kitchen iPad. So that listener runs
/// without voice processing. The trade-off: no echo cancellation for "stop", which the chime's
/// quiet start and the pauses between chimes leave room for (and the Stop button always works).
public enum AlarmAudioPolicy {
    public enum Session: Sendable, Equatable {
        /// `.playback`: ignores the silent switch and pauses other audio.
        case playback
        /// Leave the voice loop's play-and-record session as it is, so the mic stays open for
        /// the bare "stop".
        case shareVoiceSession
    }

    public static func session(voiceOwnsSession: Bool) -> Session {
        voiceOwnsSession ? .shareVoiceSession : .playback
    }

    /// Who has the mic.
    public enum MicListener: Sendable {
        case wakeWord
        case conversation
        /// Only ever while an alarm rings.
        case stopWord
    }

    /// Whether `listener`'s mic engine uses voice processing (echo cancellation).
    public static func voiceProcessing(for listener: MicListener) -> Bool {
        switch listener {
        case .wakeWord, .conversation: true
        case .stopWord: false
        }
    }
}
