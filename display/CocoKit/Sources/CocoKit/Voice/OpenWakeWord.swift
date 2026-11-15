import Foundation

/// openWakeWord's streaming pipeline (<https://github.com/dscripka/openWakeWord>), minus the
/// models: 16 kHz Int16 audio in 80 ms chunks → mel spectrogram → speech embeddings → the
/// "hey coco" classifier. Mirrors `openwakeword/utils.py` (`AudioFeatures._streaming_features`)
/// and `openwakeword/model.py` (`Model.predict`) at commit 368c037. The ONNX Runtime side lives
/// in the app target (`OnnxWakeWordModelRunner`) so CocoKit stays testable on macOS.
public enum OpenWakeWord {
    public static let sampleRate = 16_000
    /// 80 ms: the unit the models are run on.
    public static let chunkSamples = 1280
    /// Extra samples before each chunk fed to the mel model (`160 * 3`), so frames line up
    /// across chunks.
    public static let melContextSamples = 480
    public static let melBins = 32
    /// Mel frames per embedding window, and the stride between windows (8 frames per chunk).
    public static let melWindow = 76
    public static let embeddingSize = 96
    /// openWakeWord's `melspec_transform`: brings the ONNX mel model close to Google's original.
    static func transformMel(_ value: Float) -> Float { value / 10 + 2 }
}

/// Runs the three models. Called only from one thread at a time (`OpenWakeWordRecognizer`
/// serialises), never on the main actor.
public protocol WakeWordModelRunner: AnyObject {
    /// Embeddings the classifier takes (its input's second dimension; 16 for openWakeWord's
    /// training pipeline).
    var classifierWindow: Int { get }
    /// `melspectrogram.onnx`: raw sample values as floats (not scaled to -1...1), shape [1, n].
    /// Returns frames × 32 mel bins, before `OpenWakeWord.transformMel`.
    func melSpectrogram(_ samples: [Float]) throws -> [[Float]]
    /// `embedding_model.onnx`: one 76 × 32 window → 96 features.
    func embedding(_ melWindow: [[Float]]) throws -> [Float]
    /// The wake-word classifier: `classifierWindow` × 96 embeddings, oldest first → score 0...1.
    func wakeScore(_ embeddings: [[Float]]) throws -> Float
}

/// Buffers audio into chunks and turns each into a classifier score. Not thread-safe.
///
/// One deliberate difference from openWakeWord: its feature buffer starts with embeddings of
/// random noise and it zeroes the first 5 predictions; this one simply returns no score until
/// `classifierWindow` real embeddings exist (1.28 s after a start or reset), so a restart can
/// never re-detect audio from before it.
public final class OpenWakeWordPipeline {
    private let runner: any WakeWordModelRunner
    private var pending: [Int16] = []
    /// The last `melContextSamples` of audio already turned into mel frames.
    private var context: [Float] = []
    private var melFrames: [[Float]] = []
    private var embeddings: [[Float]] = []

    public init(runner: any WakeWordModelRunner) {
        self.runner = runner
        reset()
    }

    public func reset() {
        pending = []
        context = []
        // openWakeWord starts its mel buffer as 76 frames of ones.
        melFrames = Array(
            repeating: Array(repeating: 1, count: OpenWakeWord.melBins), count: OpenWakeWord.melWindow)
        embeddings = []
    }

    /// Adds audio of any length; returns one score per completed 80 ms chunk once warmed up.
    public func process(_ samples: [Int16]) throws -> [Float] {
        pending.append(contentsOf: samples)
        var scores: [Float] = []
        while pending.count >= OpenWakeWord.chunkSamples {
            let chunk = pending.prefix(OpenWakeWord.chunkSamples).map { Float($0) }
            pending.removeFirst(OpenWakeWord.chunkSamples)
            if let score = try processChunk(chunk) { scores.append(score) }
        }
        return scores
    }

    private func processChunk(_ chunk: [Float]) throws -> Float? {
        let frames = try runner.melSpectrogram(context + chunk)
        context = Array(chunk.suffix(OpenWakeWord.melContextSamples))
        melFrames.append(contentsOf: frames.map { $0.map(OpenWakeWord.transformMel) })
        melFrames.removeFirst(max(0, melFrames.count - OpenWakeWord.melWindow))

        embeddings.append(try runner.embedding(melFrames))
        let window = runner.classifierWindow
        embeddings.removeFirst(max(0, embeddings.count - window))
        guard embeddings.count == window else { return nil }
        return try runner.wakeScore(embeddings)
    }
}

/// Turns per-chunk scores into detections: the mean of the last `smoothing` scores must reach
/// `threshold`, and after a detection nothing fires for `cooldownChunks` (80 ms each), with the
/// score history cleared so the same utterance cannot fire twice.
public struct WakeWordDetector: Sendable, Equatable {
    public var threshold: Float
    public var smoothing: Int
    public var cooldownChunks: Int
    private var recent: [Float] = []
    private var cooldownLeft = 0

    /// Defaults: openWakeWord's recommended 0.5 threshold, two chunks (160 ms) of smoothing and
    /// a 2 s cooldown. Tune on the iPad (docs/wake-word.md).
    public init(threshold: Float = 0.5, smoothing: Int = 2, cooldownChunks: Int = 25) {
        self.threshold = threshold
        self.smoothing = max(1, smoothing)
        self.cooldownChunks = max(0, cooldownChunks)
    }

    public mutating func observe(_ score: Float) -> Bool {
        if cooldownLeft > 0 {
            cooldownLeft -= 1
            return false
        }
        recent.append(score)
        recent.removeFirst(max(0, recent.count - smoothing))
        let mean = recent.reduce(0, +) / Float(recent.count)
        guard mean >= threshold else { return false }
        recent = []
        cooldownLeft = cooldownChunks
        return true
    }

    public mutating func reset() {
        recent = []
        cooldownLeft = 0
    }
}

/// Pipeline + detector behind a lock: fed from the microphone's queue, reset from the main
/// actor when the engine restarts.
public final class OpenWakeWordRecognizer: @unchecked Sendable {
    private let lock = NSLock()
    private let pipeline: OpenWakeWordPipeline
    private var detector: WakeWordDetector

    public init(runner: any WakeWordModelRunner, detector: WakeWordDetector = WakeWordDetector()) {
        self.pipeline = OpenWakeWordPipeline(runner: runner)
        self.detector = detector
    }

    /// True if "hey coco" was detected in any chunk this audio completed. A model error counts
    /// as no detection (the next chunk tries again).
    public func process(_ samples: [Int16]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let scores: [Float]
        do {
            scores = try pipeline.process(samples)
        } catch {
            return false
        }
        var detected = false
        for score in scores where detector.observe(score) { detected = true }
        return detected
    }

    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        pipeline.reset()
        detector.reset()
    }
}
