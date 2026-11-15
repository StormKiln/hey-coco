import CocoKit
import Foundation
import OnnxRuntimeBindings

/// openWakeWord's three models on ONNX Runtime (CPU, one thread each, as openWakeWord runs them).
/// Shapes follow `openwakeword/utils.py` and `model.py`: mel [1, n] → [1, 1, frames, 32];
/// embedding [1, 76, 32, 1] → [1, 1, 1, 96]; classifier [1, 16, 96] → [1, 1].
///
/// Not thread-safe: `OpenWakeWordRecognizer` calls it under its lock.
final class OnnxWakeWordModelRunner: WakeWordModelRunner {
    /// openWakeWord's training pipeline makes 16-embedding (1.28 s) classifiers for short
    /// phrases; `init` checks the model really takes that.
    let classifierWindow = 16

    private let env: ORTEnv
    private let mel: Model
    private let embedder: Model
    private let classifier: Model

    init(resources: WakeWordResources) throws {
        env = try ORTEnv(loggingLevel: .warning)
        mel = try Model(env: env, url: resources.melSpectrogramURL)
        embedder = try Model(env: env, url: resources.embeddingURL)
        classifier = try Model(env: env, url: resources.classifierURL)
        // Fails now (wake word unavailable) rather than on every chunk if the shape is wrong.
        _ = try wakeScore(Array(repeating: Array(repeating: 0, count: OpenWakeWord.embeddingSize), count: classifierWindow))
    }

    func melSpectrogram(_ samples: [Float]) throws -> [[Float]] {
        let out = try mel.run(samples, shape: [1, samples.count])
        return stride(from: 0, to: out.count - OpenWakeWord.melBins + 1, by: OpenWakeWord.melBins).map {
            Array(out[$0..<$0 + OpenWakeWord.melBins])
        }
    }

    func embedding(_ melWindow: [[Float]]) throws -> [Float] {
        let out = try embedder.run(
            melWindow.flatMap { $0 }, shape: [1, melWindow.count, OpenWakeWord.melBins, 1])
        guard out.count == OpenWakeWord.embeddingSize else { throw RunnerError.shape("embedding", out.count) }
        return out
    }

    func wakeScore(_ embeddings: [[Float]]) throws -> Float {
        let out = try classifier.run(
            embeddings.flatMap { $0 }, shape: [1, embeddings.count, OpenWakeWord.embeddingSize])
        guard let score = out.first else { throw RunnerError.shape("classifier", 0) }
        return score
    }

    enum RunnerError: Error {
        case shape(String, Int)
    }

    /// One session with a single float input and a single float output.
    private final class Model {
        let session: ORTSession
        let input: String
        let output: String

        init(env: ORTEnv, url: URL) throws {
            let options = try ORTSessionOptions()
            try options.setIntraOpNumThreads(1)
            try options.setGraphOptimizationLevel(.all)
            session = try ORTSession(env: env, modelPath: url.path(percentEncoded: false), sessionOptions: options)
            guard let input = try session.inputNames().first, let output = try session.outputNames().first
            else { throw RunnerError.shape(url.lastPathComponent, 0) }
            self.input = input
            self.output = output
        }

        func run(_ values: [Float], shape: [Int]) throws -> [Float] {
            let data = values.withUnsafeBufferPointer {
                NSMutableData(bytes: $0.baseAddress, length: $0.count * MemoryLayout<Float>.stride)
            }
            let tensor = try ORTValue(
                tensorData: data, elementType: .float, shape: shape.map { NSNumber(value: $0) })
            let outputs = try session.run(withInputs: [input: tensor], outputNames: [output], runOptions: nil)
            guard let result = outputs[output] else { throw RunnerError.shape(output, 0) }
            let bytes = try result.tensorData() as Data
            return bytes.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        }
    }
}
