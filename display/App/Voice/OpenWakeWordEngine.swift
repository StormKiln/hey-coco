import CocoKit
import Foundation

/// `WakeWordEngine` on openWakeWord with the trained "hey coco" model.
///
/// Lives in the app target because it links ONNX Runtime (`OnnxWakeWordModelRunner`); the
/// buffering, scoring and cooldown are CocoKit's `OpenWakeWordRecognizer`. Fed by
/// `PCMFrameMicrophone` in openWakeWord's 80 ms chunks, so the app's `AudioSessionCoordinator`
/// stays the only owner of the audio session and the mic gets echo cancellation.
@MainActor
final class OpenWakeWordEngine: WakeWordEngine {
    private let resources: WakeWordResources
    private let detector: WakeWordDetector
    private let microphone = PCMFrameMicrophone(
        sampleRate: Double(OpenWakeWord.sampleRate), frameLength: OpenWakeWord.chunkSamples)
    /// Created on first start (that is when the models load) and kept for the app's life.
    private var recognizer: OpenWakeWordRecognizer?
    /// Routes the mic's frames to the detector, or after a detection to the speech recognizer
    /// (#112). One per start, so a capture can't outlive the mic it came from.
    private var router: WakeFrameRouter?
    /// An unclaimed capture is dropped after this long (the turn never started).
    private static let captureLimit: Double = 10

    var isRunning: Bool { microphone.isRunning }

    init(resources: WakeWordResources, detector: WakeWordDetector = WakeWordDetector()) {
        self.resources = resources
        self.detector = detector
    }

    func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws {
        let recognizer = try recognizer ?? makeRecognizer()
        self.recognizer = recognizer
        // A restart (after a conversation, an interruption) must not hear audio from before it.
        recognizer.reset()
        let router = WakeFrameRouter(
            sampleRate: microphone.sampleRate,
            maxBufferedFrames: Int(Self.captureLimit * microphone.sampleRate) / microphone.frameLength,
            detect: { recognizer.process($0) },
            resetDetector: { recognizer.reset() })
        self.router = router
        try microphone.start { frame in
            // On the microphone's serial queue.
            if router.process(frame) {
                Task { @MainActor in onDetected() }
            }
        }
    }

    func stop() {
        microphone.stop()
        router = nil
    }

    func claimCapture() -> (any SpeechAudioFeed)? {
        microphone.isRunning ? router?.claimCapture() : nil
    }

    func resumeDetection() {
        router?.resumeDetection()
    }

    private func makeRecognizer() throws -> OpenWakeWordRecognizer {
        OpenWakeWordRecognizer(runner: try OnnxWakeWordModelRunner(resources: resources), detector: detector)
    }
}

extension WakeWordAvailability {
    /// openWakeWord if this build bundles the "hey coco" model (`display/WakeWord/`), else
    /// tap-to-talk only.
    @MainActor
    static func bundled() -> WakeWordAvailability {
        switch WakeWordResources.locate(in: Bundle.main.resourceURL) {
        case .available(let resources):
            .available(OpenWakeWordEngine(resources: resources))
        case .unavailable(let reason):
            .unavailable(reason: reason)
        }
    }
}
