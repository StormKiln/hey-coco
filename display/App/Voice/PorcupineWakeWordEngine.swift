import CocoKit
import Foundation
import Porcupine

/// `WakeWordEngine` on Picovoice Porcupine with the custom "hey coco" keyword (#21).
///
/// Lives in the app target because Picovoice's Swift package is iOS-only (CocoKit must also build
/// for `swift test` on macOS). Uses the low-level `Porcupine` class fed from
/// `PCMFrameMicrophone` rather than `PorcupineManager`, so the app's `AudioSessionCoordinator`
/// stays the only owner of the audio session and the mic gets echo cancellation.
@MainActor
final class PorcupineWakeWordEngine: WakeWordEngine {
    private let resources: WakeWordResources
    private let sensitivity: Float32
    private let microphone = PCMFrameMicrophone(
        sampleRate: Double(Porcupine.sampleRate), frameLength: Int(Porcupine.frameLength))
    /// Created on first start (that is when the AccessKey is checked) and kept for the app's life.
    private var detector: Detector?

    var isRunning: Bool { microphone.isRunning }

    init(resources: WakeWordResources, sensitivity: Float32 = 0.6) {
        self.resources = resources
        self.sensitivity = sensitivity
    }

    func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws {
        let detector = try detector ?? makeDetector()
        self.detector = detector
        try microphone.start { frame in
            // On the microphone's serial queue.
            if detector.process(frame) {
                Task { @MainActor in onDetected() }
            }
        }
    }

    func stop() {
        microphone.stop()
    }

    private func makeDetector() throws -> Detector {
        let porcupine = try Porcupine(
            accessKey: resources.accessKey,
            keywordPath: resources.keywordURL.path(percentEncoded: false),
            sensitivity: sensitivity)
        return Detector(porcupine)
    }

    /// Only ever used from the microphone's serial queue after creation.
    private final class Detector: @unchecked Sendable {
        let porcupine: Porcupine
        init(_ porcupine: Porcupine) { self.porcupine = porcupine }
        func process(_ frame: [Int16]) -> Bool {
            ((try? porcupine.process(pcm: frame)) ?? -1) >= 0
        }
    }
}

extension WakeWordAvailability {
    /// Porcupine if this build bundles an AccessKey and keyword (gitignored `display/WakeWord/`),
    /// else tap-to-talk only.
    @MainActor
    static func bundled() -> WakeWordAvailability {
        switch WakeWordResources.locate(
            in: Bundle.main.resourceURL, runningOnMac: ProcessInfo.processInfo.isiOSAppOnMac)
        {
        case .available(let resources):
            .available(PorcupineWakeWordEngine(resources: resources))
        case .unavailable(let reason):
            .unavailable(reason: reason)
        }
    }
}
