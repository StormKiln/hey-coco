@preconcurrency import AVFoundation
import Foundation
@preconcurrency import Speech

/// `SpeechRecognizer` on `SFSpeechRecognizer` + `AVAudioEngine`. On-device recognition when the
/// device supports it for the locale, else Apple's server. End-of-utterance comes from
/// `UtteranceEndpointer` (silence after the last partial, with a hard cap).
///
/// The audio session is configured by `ConversationAudio.beginConversation()`, not here.
@MainActor
public final class AppleSpeechRecognizer: FeedableSpeechRecognizer {
    public let locale: Locale
    public let policy: UtteranceEndpointer.Policy
    /// Echo cancellation on the mic (`VoiceLoop` turns it on so Coco can be heard over its own
    /// media; not for the bare "stop" while the alarm rings, see `AlarmAudioPolicy`).
    public let voiceProcessing: Bool
    /// After the audio ends, how long to wait for the recognizer's final before using the last
    /// partial instead.
    private let finalGrace: Duration = .seconds(2)

    private var engine: AVAudioEngine?
    /// Where the audio comes from instead of `engine` (the wake word's capture, #112).
    private var feed: (any SpeechAudioFeed)?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var endpointer: UtteranceEndpointer?
    private var continuation: AsyncThrowingStream<SpeechTranscript, any Error>.Continuation?
    private var lastText = ""
    private var audioEnded = false
    private var session = 0

    public init(
        locale: Locale = AppleSpeechRecognizer.preferredLocale(), policy: UtteranceEndpointer.Policy = .init(),
        voiceProcessing: Bool = false
    ) {
        self.locale = locale
        self.policy = policy
        self.voiceProcessing = voiceProcessing
    }

    /// The device locale when Speech supports it, else en-US.
    public nonisolated static func preferredLocale() -> Locale {
        let current = Locale.current
        let supported = SFSpeechRecognizer.supportedLocales()
        if supported.contains(where: { $0.identifier == current.identifier }) { return current }
        return Locale(identifier: "en-US")
    }

    /// `SFSpeechRecognizer.supportsOnDeviceRecognition` for `locale` (`nil`: no recognizer for
    /// it). `start()` uses on-device recognition exactly when this is true.
    public static func supportsOnDeviceRecognition(
        locale: Locale = AppleSpeechRecognizer.preferredLocale()
    ) -> Bool? {
        SFSpeechRecognizer(locale: locale)?.supportsOnDeviceRecognition
    }

    public func requestPermission() async -> SpeechPermission {
        switch await Self.requestSpeechAuthorization() {
        case .authorized: break
        case .restricted: return .restricted
        case .denied, .notDetermined: return .speechRecognitionDenied
        @unknown default: return .speechRecognitionDenied
        }
        guard await AVAudioApplication.requestRecordPermission() else { return .microphoneDenied }
        guard SFSpeechRecognizer(locale: locale)?.isAvailable == true else { return .recognizerUnavailable }
        return .granted
    }

    /// Opens its own mic.
    public func start() throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        try begin(audio: nil)
    }

    /// Listens to audio already captured (from the wake word onward) instead of opening the mic,
    /// so there is only ever one voice-processing engine on the input.
    public func start(audio: any SpeechAudioFeed) throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        try begin(audio: audio)
    }

    private func begin(audio feed: (any SpeechAudioFeed)?) throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        cancel()
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            throw SpeechRecognitionError.unavailable
        }
        session += 1
        let session = session
        lastText = ""
        audioEnded = false

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        request.addsPunctuation = true

        if feed == nil {
            let engine = AVAudioEngine()
            let input = engine.inputNode
            // Best effort: without it recognition still works, just with more echo.
            if voiceProcessing { try? VoiceProcessing.enable(on: input) }
            input.installTap(
                onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0),
                block: Self.tapBlock(appendingTo: request))
            engine.prepare()
            do {
                try engine.start()
            } catch {
                input.removeTap(onBus: 0)
                throw SpeechRecognitionError.failed(error.localizedDescription)
            }
            self.engine = engine
        }

        let (stream, continuation) = AsyncThrowingStream.makeStream(of: SpeechTranscript.self)
        continuation.onTermination = { [weak self] termination in
            guard case .cancelled = termination else { return }
            Task { @MainActor in
                guard let self, self.session == session else { return }
                self.cancel()
            }
        }
        self.request = request
        self.continuation = continuation
        self.task = recognizer.recognitionTask(
            with: request,
            resultHandler: Self.resultHandler { [weak self] event in
                guard let self, self.session == session else { return }
                self.handle(event)
            })
        if let feed {
            // Everything captured since the wake word goes in first (faster than real time), then
            // live frames, until `stopAudio()` detaches it.
            self.feed = feed
            feed.attach(Self.frameSink(appendingTo: request, sampleRate: feed.sampleRate))
        }
        let endpointer = UtteranceEndpointer(policy: policy) { [weak self] in
            guard let self, self.session == session else { return }
            self.stop()
        }
        self.endpointer = endpointer
        endpointer.start()
        return stream
    }

    public func stop() {
        guard continuation != nil, !audioEnded else { return }
        audioEnded = true
        endpointer?.cancel()
        stopAudio()
        request?.endAudio()
        // If the recognizer never sends a final, settle for what we have.
        let session = session
        Task { [weak self, finalGrace] in
            try? await Task.sleep(for: finalGrace)
            guard let self, self.session == session else { return }
            self.complete(with: self.lastText)
        }
    }

    public func cancel() {
        task?.cancel()
        let continuation = continuation
        tearDown()
        continuation?.finish()
    }

    // MARK: - Private

    private enum RecognitionEvent: Sendable {
        case result(text: String, isFinal: Bool)
        case failure(domain: String, code: Int, message: String)
    }

    private func handle(_ event: RecognitionEvent) {
        switch event {
        case .result(let text, let isFinal):
            if isFinal {
                complete(with: text)
            } else if text != lastText {
                lastText = text
                continuation?.yield(.partial(text))
                if !text.isEmpty { endpointer?.heardSpeech() }
            }
        case .failure(let domain, let code, let message):
            // 1110 = "No speech detected". After we ended the audio, any error just means
            // "that's all": use what we heard.
            if audioEnded || (domain == "kAFAssistantErrorDomain" && code == 1110) {
                complete(with: lastText)
            } else {
                let continuation = continuation
                tearDown()
                continuation?.finish(throwing: SpeechRecognitionError.failed(message))
            }
        }
    }

    private func complete(with text: String) {
        guard let continuation else { return }
        tearDown()
        continuation.yield(.final(text))
        continuation.finish()
    }

    private func stopAudio() {
        feed?.detach()
        feed = nil
        guard let engine else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        self.engine = nil
    }

    private func tearDown() {
        session += 1
        endpointer?.cancel()
        endpointer = nil
        stopAudio()
        request = nil
        task = nil
        continuation = nil
    }

    // Callbacks below run on audio / Speech queues, so they are built outside the main actor and
    // only hand Sendable values back to it.

    private nonisolated static func tapBlock(
        appendingTo request: SFSpeechAudioBufferRecognitionRequest
    ) -> AVAudioNodeTapBlock {
        nonisolated(unsafe) let request = request
        return { buffer, _ in request.append(buffer) }
    }

    private nonisolated static func frameSink(
        appendingTo request: SFSpeechAudioBufferRecognitionRequest, sampleRate: Double
    ) -> @Sendable ([Int16]) -> Void {
        nonisolated(unsafe) let request = request
        return { frame in
            guard let buffer = CapturedAudioBuffer.make(from: frame, sampleRate: sampleRate) else { return }
            request.append(buffer)
        }
    }

    private nonisolated static func resultHandler(
        _ deliver: @escaping @MainActor @Sendable (RecognitionEvent) -> Void
    ) -> (SFSpeechRecognitionResult?, (any Error)?) -> Void {
        { result, error in
            let event: RecognitionEvent
            if let result {
                event = .result(text: result.bestTranscription.formattedString, isFinal: result.isFinal)
            } else if let error {
                let nsError = error as NSError
                event = .failure(domain: nsError.domain, code: nsError.code, message: nsError.localizedDescription)
            } else {
                return
            }
            Task { @MainActor in deliver(event) }
        }
    }

    private nonisolated static func requestSpeechAuthorization() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
    }
}

/// Captured 16-bit mono frames as the float PCM buffers `SFSpeechAudioBufferRecognitionRequest`
/// takes.
enum CapturedAudioBuffer {
    static func make(from frame: [Int16], sampleRate: Double) -> AVAudioPCMBuffer? {
        guard !frame.isEmpty,
            let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1),
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frame.count)),
            let channel = buffer.floatChannelData?[0]
        else { return nil }
        for (index, sample) in frame.enumerated() { channel[index] = Float(sample) / 32_768 }
        buffer.frameLength = AVAudioFrameCount(frame.count)
        return buffer
    }
}
