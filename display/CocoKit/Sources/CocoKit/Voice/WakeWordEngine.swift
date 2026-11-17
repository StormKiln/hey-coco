import Foundation

/// Listens for the wake phrase ("hey coco") and calls back on every detection. The real one is
/// `OpenWakeWordEngine` (app target: it links ONNX Runtime, which CocoKit stays free of; the
/// pipeline logic is `OpenWakeWordRecognizer` here).
///
/// `start` opens the microphone; it must not be running while the speech recognizer opens its
/// own (`VoiceLoop` hands the mic back and forth). After a detection the engine may instead keep
/// the mic open and hand its audio to the recognizer (`claimCapture()`, #112).
@MainActor
public protocol WakeWordEngine: AnyObject {
    var isRunning: Bool { get }
    /// Starts listening. `onDetected` is called on the main actor for each detection.
    func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws
    func stop()
    /// What the mic heard since the last detection (buffered, then live), while the engine is
    /// still capturing it; detection is paused until `resumeDetection()` or `stop()`. Nil if
    /// there is none (not running, no recent detection, or not supported).
    func claimCapture() -> (any SpeechAudioFeed)?
    /// Ends the capture and listens for the wake word again on the running mic.
    func resumeDetection()
}

extension WakeWordEngine {
    public func claimCapture() -> (any SpeechAudioFeed)? { nil }
    public func resumeDetection() {}
}

/// Whether the wake word can run on this build, and if not, why (shown subtly in the UI).
public enum WakeWordAvailability {
    case available(any WakeWordEngine)
    case unavailable(reason: String)
}

/// A wake-word engine the test (or a preview) triggers by hand.
@MainActor
public final class FakeWakeWordEngine: WakeWordEngine {
    public private(set) var isRunning = false
    public private(set) var startCount = 0
    /// Thrown by the next `start()` while set.
    public var startError: (any Error)?
    /// Records "start" / "stop" (tests use it for ordering).
    public var log: ((String) -> Void)?
    private var onDetected: (@MainActor @Sendable () -> Void)?
    /// The real routing, with a detector that never fires on its own (`detect()` does).
    private let router = WakeFrameRouter(
        sampleRate: 16_000, maxBufferedFrames: 1_000, detect: { _ in false }, resetDetector: {})

    public init() {}

    public func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws {
        startCount += 1
        if let startError { throw startError }
        router.resumeDetection()
        isRunning = true
        self.onDetected = onDetected
        log?("start")
    }

    public func stop() {
        guard isRunning else { return }
        isRunning = false
        onDetected = nil
        router.resumeDetection()
        log?("stop")
    }

    public func claimCapture() -> (any SpeechAudioFeed)? {
        isRunning ? router.claimCapture() : nil
    }

    public func resumeDetection() {
        router.resumeDetection()
        log?("resume")
    }

    /// Simulates hearing "hey coco". Ignored while stopped, like a real engine.
    public func detect() {
        guard isRunning else { return }
        router.triggerDetection()
        onDetected?()
    }

    /// Simulates one mic frame (captured after a detection). Ignored while stopped.
    public func hear(_ frame: [Int16]) {
        guard isRunning else { return }
        router.process(frame)
    }
}

/// The three openWakeWord models the wake word needs, bundled from `display/WakeWord/` (see
/// `docs/wake-word.md`). The two feature models are openWakeWord's published ones and are
/// committed; `hey_coco.onnx` is trained with `make wakeword-train`.
public struct WakeWordResources: Sendable, Equatable {
    public static let melSpectrogramFile = "melspectrogram.onnx"
    public static let embeddingFile = "embedding_model.onnx"
    public static let wakeModelFile = "hey_coco.onnx"
    public static let noFeatureModelsReason =
        "Wake word off: openWakeWord's feature models are missing from this build."
    public static let noWakeModelReason = "Wake word off: no \"hey coco\" model in this build."

    public enum Lookup: Sendable, Equatable {
        case available(WakeWordResources)
        case unavailable(String)
    }

    public var melSpectrogramURL: URL
    public var embeddingURL: URL
    public var classifierURL: URL

    public init(melSpectrogramURL: URL, embeddingURL: URL, classifierURL: URL) {
        self.melSpectrogramURL = melSpectrogramURL
        self.embeddingURL = embeddingURL
        self.classifierURL = classifierURL
    }

    /// Looks in `directory` (the app bundle's resources). An empty file does not count.
    public static func locate(in directory: URL?) -> Lookup {
        func model(_ name: String) -> URL? {
            guard let url = directory?.appending(path: name),
                let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size > 0
            else { return nil }
            return url
        }
        guard let mel = model(melSpectrogramFile), let embedding = model(embeddingFile) else {
            return .unavailable(noFeatureModelsReason)
        }
        guard let classifier = model(wakeModelFile) else { return .unavailable(noWakeModelReason) }
        return .available(
            WakeWordResources(melSpectrogramURL: mel, embeddingURL: embedding, classifierURL: classifier))
    }
}
