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
        try microphone.start { frame in
            // On the microphone's serial queue.
            if recognizer.process(frame) {
                Task { @MainActor in onDetected() }
            }
        }
    }

    func stop() {
        microphone.stop()
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
