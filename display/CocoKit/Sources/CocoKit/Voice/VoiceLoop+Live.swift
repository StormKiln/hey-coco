@preconcurrency import AVFoundation
import Foundation

extension VoiceLoop {
    /// The real wiring: Apple speech with echo cancellation, the shared audio-session
    /// coordinator, and a short-window recognizer for the bare "stop".
    public static func live(host: any ConversationHost, wakeWord: WakeWordAvailability) -> VoiceLoop {
        let stopWindow = UtteranceEndpointer.Policy(
            silenceTimeout: .milliseconds(700), noSpeechTimeout: .seconds(4), maxDuration: .seconds(4))
        return VoiceLoop(
            host: host,
            wakeWord: wakeWord,
            stopWord: RecognizerStopWordListener(
                recognizer: AppleSpeechRecognizer(policy: stopWindow, voiceProcessing: true)),
            recognizer: AppleSpeechRecognizer(voiceProcessing: true),
            synthesizer: AppleSpeechSynthesizer(),
            session: AudioSessionCoordinator.shared,
            requestMicPermission: { await AVAudioApplication.requestRecordPermission() })
    }
}
