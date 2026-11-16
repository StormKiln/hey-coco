@preconcurrency import AVFoundation
import Foundation
import Speech

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
                recognizer: AppleSpeechRecognizer(
                    policy: stopWindow, voiceProcessing: AlarmAudioPolicy.voiceProcessing(for: .stopWord))),
            recognizer: AppleSpeechRecognizer(
                voiceProcessing: AlarmAudioPolicy.voiceProcessing(for: .conversation)),
            synthesizer: AppleSpeechSynthesizer(),
            session: AudioSessionCoordinator.shared,
            requestMicPermission: { await AVAudioApplication.requestRecordPermission() },
            // The bare "stop" uses the mic and speech recognition; only if both are already
            // granted (tap-to-talk asks for them), never prompting while an alarm rings.
            micAlreadyGranted: {
                AVAudioApplication.shared.recordPermission == .granted
                    && SFSpeechRecognizer.authorizationStatus() == .authorized
            })
    }
}
