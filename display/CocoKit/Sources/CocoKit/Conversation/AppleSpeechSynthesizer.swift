@preconcurrency import AVFoundation
import Foundation

/// `SpeechSynthesizer` on `AVSpeechSynthesizer`, always in the same voice: the best installed
/// premium/enhanced English voice, else the system default for the language.
@MainActor
public final class AppleSpeechSynthesizer: NSObject, SpeechSynthesizer {
    public struct VoiceInfo: Sendable, Equatable {
        public enum Quality: Int, Sendable, Comparable {
            case `default` = 1, enhanced, premium
            public static func < (a: Quality, b: Quality) -> Bool { a.rawValue < b.rawValue }
        }
        public var identifier: String
        public var language: String
        public var quality: Quality
        public var isNovelty: Bool

        public init(identifier: String, language: String, quality: Quality, isNovelty: Bool) {
            self.identifier = identifier
            self.language = language
            self.quality = quality
            self.isNovelty = isNovelty
        }
    }

    public let language: String
    public let voiceIdentifier: String?

    private let synthesizer = AVSpeechSynthesizer()
    private var current: (id: ObjectIdentifier, continuation: CheckedContinuation<Void, Never>)?

    public init(language: String = AppleSpeechSynthesizer.preferredLanguage()) {
        self.language = language
        self.voiceIdentifier = Self.bestVoice(
            in: AVSpeechSynthesisVoice.speechVoices().map(VoiceInfo.init), language: language)
        super.init()
        synthesizer.delegate = self
    }

    /// The device language if it's English, else en-US.
    public nonisolated static func preferredLanguage() -> String {
        let current = AVSpeechSynthesisVoice.currentLanguageCode()
        return current.hasPrefix("en") ? current : "en-US"
    }

    /// The best non-novelty enhanced/premium voice: same language first, then any English.
    /// `nil` means "use the system default for the language". Ties break by identifier so the
    /// choice is stable.
    public nonisolated static func bestVoice(in voices: [VoiceInfo], language: String) -> String? {
        let candidates = voices.filter { !$0.isNovelty && $0.quality >= .enhanced && $0.language.hasPrefix("en") }
        return candidates.min { a, b in
            let aKey = (a.language == language ? 0 : 1, -a.quality.rawValue)
            let bKey = (b.language == language ? 0 : 1, -b.quality.rawValue)
            return aKey != bKey ? aKey < bKey : a.identifier < b.identifier
        }?.identifier
    }

    public func speak(_ text: String) async {
        stop()
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice =
            voiceIdentifier.flatMap(AVSpeechSynthesisVoice.init(identifier:))
            ?? AVSpeechSynthesisVoice(language: language)
        let id = ObjectIdentifier(utterance)
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                current = (id, continuation)
                synthesizer.speak(utterance)
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.finish(id) }
        }
    }

    public func stop() {
        guard let current else { return }
        synthesizer.stopSpeaking(at: .immediate)
        finish(current.id)
    }

    private func finish(_ id: ObjectIdentifier) {
        guard let current, current.id == id else { return }
        self.current = nil
        current.continuation.resume()
    }
}

extension AppleSpeechSynthesizer: AVSpeechSynthesizerDelegate {
    public nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance
    ) {
        let id = ObjectIdentifier(utterance)
        Task { @MainActor in self.finish(id) }
    }

    public nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance
    ) {
        let id = ObjectIdentifier(utterance)
        Task { @MainActor in self.finish(id) }
    }
}

extension AppleSpeechSynthesizer.VoiceInfo {
    init(_ voice: AVSpeechSynthesisVoice) {
        let quality: Quality =
            switch voice.quality {
            case .premium: .premium
            case .enhanced: .enhanced
            default: .default
            }
        self.init(
            identifier: voice.identifier, language: voice.language, quality: quality,
            isNovelty: voice.voiceTraits.contains(.isNoveltyVoice))
    }
}

/// The real audio session: play-and-record through the speaker, ducking other audio while Coco
/// listens and talks, plus the generated listening chime.
@MainActor
public final class AppleConversationAudio: ConversationAudio {
    private var chime: AVAudioPlayer?

    public init() {
        chime = try? AVAudioPlayer(data: ListeningChime.wavData())
        chime?.prepareToPlay()
    }

    public func beginConversation() throws {
        #if os(iOS)
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .duckOthers])
            try session.setActive(true)
        #endif
    }

    public func playListeningChime() async {
        guard let chime else { return }
        chime.currentTime = 0
        chime.play()
        try? await Task.sleep(for: .seconds(chime.duration))
    }

    public func endConversation() {
        #if os(iOS)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }
}
