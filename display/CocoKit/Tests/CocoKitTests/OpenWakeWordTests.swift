import Foundation
import Testing
@testable import CocoKit

/// Stands in for the three ONNX models. The mel "model" returns as many frames as openWakeWord's
/// does for the input length (`ceil(n / 160 - 3)`), each filled with the input's first sample, so
/// tests can see what audio reached it. The embedding "model" returns its window's last row's
/// first value, so tests can see which mel frames were windowed.
final class FakeModelRunner: WakeWordModelRunner, @unchecked Sendable {
    let classifierWindow: Int
    var melInputs: [[Float]] = []
    var embeddingInputs: [[[Float]]] = []
    var classifierInputs: [[[Float]]] = []
    /// Returned by successive classifier calls (then `defaultScore`).
    var scores: [Float] = []
    var defaultScore: Float = 0
    var error: (any Error)?

    init(classifierWindow: Int = 16) { self.classifierWindow = classifierWindow }

    func melSpectrogram(_ samples: [Float]) throws -> [[Float]] {
        if let error { throw error }
        melInputs.append(samples)
        let frames = Int((Double(samples.count) / 160 - 3).rounded(.up))
        return Array(repeating: Array(repeating: samples.first ?? 0, count: OpenWakeWord.melBins), count: frames)
    }

    func embedding(_ melWindow: [[Float]]) throws -> [Float] {
        embeddingInputs.append(melWindow)
        return Array(repeating: melWindow.last?.first ?? 0, count: OpenWakeWord.embeddingSize)
    }

    func wakeScore(_ embeddings: [[Float]]) throws -> Float {
        classifierInputs.append(embeddings)
        return scores.isEmpty ? defaultScore : scores.removeFirst()
    }
}

private func chunk(_ value: Int16, count: Int = OpenWakeWord.chunkSamples) -> [Int16] {
    Array(repeating: value, count: count)
}

@Suite struct OpenWakeWordPipelineTests {
    @Test func buffersUntilA1280SampleChunk() throws {
        let runner = FakeModelRunner()
        let pipeline = OpenWakeWordPipeline(runner: runner)
        _ = try pipeline.process(chunk(1, count: 512))
        _ = try pipeline.process(chunk(1, count: 512))
        #expect(runner.melInputs.isEmpty)
        _ = try pipeline.process(chunk(1, count: 512))  // 1536: one chunk, 256 left over
        #expect(runner.melInputs.map(\.count) == [1280])
        _ = try pipeline.process(chunk(1, count: 1024))  // 1280 again
        #expect(runner.melInputs.count == 2)
    }

    @Test func feedsTheMelModelRawSamplesWith480SamplesOfContext() throws {
        let runner = FakeModelRunner()
        let pipeline = OpenWakeWordPipeline(runner: runner)
        _ = try pipeline.process((0..<1280).map { Int16($0) })
        _ = try pipeline.process(chunk(-7))
        #expect(runner.melInputs[0] == (0..<1280).map { Float($0) })
        // openWakeWord: melspectrogram of the last n + 160 * 3 samples.
        #expect(runner.melInputs[1].count == 1760)
        #expect(Array(runner.melInputs[1].prefix(480)) == (800..<1280).map { Float($0) })
        #expect(Array(runner.melInputs[1].suffix(1280)) == Array(repeating: -7, count: 1280))
    }

    @Test func windowsTheLast76TransformedMelFramesStartingFromOnes() throws {
        let runner = FakeModelRunner()
        let pipeline = OpenWakeWordPipeline(runner: runner)
        _ = try pipeline.process(chunk(30))
        // 76 rows of padding ones, then 5 new frames (1280 samples), transformed x / 10 + 2 = 5.
        let first = try #require(runner.embeddingInputs.first)
        #expect(first.count == 76)
        #expect(first.allSatisfy { $0.count == OpenWakeWord.melBins })
        #expect(first.prefix(71).allSatisfy { $0.allSatisfy { $0 == 1 } })
        #expect(first.suffix(5).allSatisfy { $0.allSatisfy { $0 == 5 } })

        _ = try pipeline.process(chunk(50))
        // The next chunk adds 8 frames; its mel input starts with chunk one's samples (30).
        let second = try #require(runner.embeddingInputs.last)
        #expect(second.count == 76)
        #expect(second.suffix(8).allSatisfy { $0.allSatisfy { $0 == 5 } })
        #expect(second[second.count - 9].allSatisfy { $0 == 5 })
        #expect(second.prefix(63).allSatisfy { $0.allSatisfy { $0 == 1 } })
    }

    @Test func scoresOnlyOnceTheClassifierWindowIsFullOfRealEmbeddings() throws {
        let runner = FakeModelRunner(classifierWindow: 16)
        let pipeline = OpenWakeWordPipeline(runner: runner)
        for i in 0..<15 {
            #expect(try pipeline.process(chunk(Int16(i))).isEmpty)
        }
        runner.defaultScore = 0.25
        #expect(try pipeline.process(chunk(15)) == [0.25])
        let input = try #require(runner.classifierInputs.last)
        #expect(input.count == 16)
        #expect(input.allSatisfy { $0.count == OpenWakeWord.embeddingSize })
        #expect(try pipeline.process(chunk(16)) == [0.25])
        // Oldest first, sliding by one embedding per chunk.
        let next = try #require(runner.classifierInputs.last)
        #expect(Array(next.dropLast()) == Array(input.dropFirst()))
    }

    @Test func returnsAScorePerChunkInOneLongBuffer() throws {
        let runner = FakeModelRunner(classifierWindow: 2)
        runner.scores = [0.1, 0.2, 0.3]
        let pipeline = OpenWakeWordPipeline(runner: runner)
        #expect(try pipeline.process(chunk(1, count: 1280 * 4 + 100)) == [0.1, 0.2, 0.3])
        #expect(runner.melInputs.count == 4)
    }

    @Test func resetForgetsAudioAndWarmUp() throws {
        let runner = FakeModelRunner(classifierWindow: 2)
        runner.defaultScore = 0.9
        let pipeline = OpenWakeWordPipeline(runner: runner)
        _ = try pipeline.process(chunk(3, count: 1280 * 3 + 600))
        pipeline.reset()
        runner.melInputs = []
        #expect(try pipeline.process(chunk(4, count: 1000)).isEmpty)
        #expect(runner.melInputs.isEmpty)  // the 600 left over were dropped
        #expect(try pipeline.process(chunk(4, count: 280)).isEmpty)  // warm-up again
        #expect(runner.melInputs.map(\.count) == [1280])  // no stale context
        #expect(try pipeline.process(chunk(4)) == [0.9])
    }
}

@Suite struct WakeWordDetectorTests {
    /// Which of `scores` fire, in order (`#expect` cannot call a mutating method itself).
    private func fired(_ detector: inout WakeWordDetector, _ scores: [Float]) -> [Bool] {
        scores.map { detector.observe($0) }
    }

    @Test func firesAtOrAboveTheThreshold() {
        var detector = WakeWordDetector(threshold: 0.5, smoothing: 1, cooldownChunks: 0)
        let result = fired(&detector, [0.49, 0.5])
        #expect(result == [false, true])
    }

    @Test func averagesTheLastScoresSoASingleSpikeIsNotEnough() {
        var detector = WakeWordDetector(threshold: 0.5, smoothing: 2, cooldownChunks: 0)
        // (0.0 + 0.9) / 2 = 0.45, then (0.9 + 0.8) / 2 = 0.85.
        let result = fired(&detector, [0.0, 0.9, 0.8])
        #expect(result == [false, false, true])
    }

    @Test func ignoresEverythingDuringTheCooldown() {
        var detector = WakeWordDetector(threshold: 0.5, smoothing: 1, cooldownChunks: 3)
        let result = fired(&detector, [0.9, 0.9, 0.9, 0.9, 0.9])
        #expect(result == [true, false, false, false, true])
    }

    @Test func historyStartsAfreshAfterTheCooldown() {
        var detector = WakeWordDetector(threshold: 0.5, smoothing: 2, cooldownChunks: 1)
        // Fires on (0 + 1) / 2; cools down for one; then 0.2 is averaged alone (not with the
        // pre-detection 1.0), and (0.2 + 0.9) / 2 = 0.55 fires.
        let result = fired(&detector, [0.0, 1.0, 1.0, 0.2, 0.9])
        #expect(result == [false, true, false, false, true])
    }

    @Test func resetClearsCooldownAndHistory() {
        var detector = WakeWordDetector(threshold: 0.5, smoothing: 2, cooldownChunks: 10)
        let before = fired(&detector, [0.0, 1.0])
        detector.reset()
        let after = fired(&detector, [0.6])
        #expect(before == [false, true])
        #expect(after == [true])
    }

    @Test func defaultsMatchTheDocumentedTuning() {
        let detector = WakeWordDetector()
        #expect(detector.threshold == 0.5)
        #expect(detector.smoothing == 2)
        #expect(detector.cooldownChunks == 25)  // 2 s of 80 ms chunks
    }
}

@Suite struct OpenWakeWordRecognizerTests {
    @Test func detectsOnceThenCoolsDown() {
        let runner = FakeModelRunner(classifierWindow: 1)
        runner.defaultScore = 0.95
        let recognizer = OpenWakeWordRecognizer(
            runner: runner, detector: WakeWordDetector(threshold: 0.5, smoothing: 1, cooldownChunks: 5))
        #expect(recognizer.process(chunk(1)))
        #expect(!recognizer.process(chunk(1)))
    }

    @Test func aModelErrorIsNoDetection() {
        let runner = FakeModelRunner(classifierWindow: 1)
        runner.error = CocoTestError.boom
        let recognizer = OpenWakeWordRecognizer(runner: runner, detector: WakeWordDetector(smoothing: 1))
        #expect(!recognizer.process(chunk(1)))
        runner.error = nil
        runner.defaultScore = 1
        #expect(recognizer.process(chunk(1)))
    }

    @Test func resetNeedsAFullWarmUpBeforeTheNextDetection() {
        let runner = FakeModelRunner(classifierWindow: 3)
        runner.defaultScore = 1
        let recognizer = OpenWakeWordRecognizer(
            runner: runner, detector: WakeWordDetector(threshold: 0.5, smoothing: 1, cooldownChunks: 0))
        #expect(recognizer.process(chunk(1, count: 1280 * 3)))
        recognizer.reset()
        #expect(!recognizer.process(chunk(1, count: 1280 * 2)))
        #expect(recognizer.process(chunk(1)))
    }
}

private enum CocoTestError: Error { case boom }

@Suite struct WakeWordResourcesTests {
    private func directory(_ files: [String]) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appending(path: "wake-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        for name in files {
            try Data("x".utf8).write(to: url.appending(path: name))
        }
        return url
    }

    @Test func findsAllThreeModels() throws {
        let dir = try directory(["melspectrogram.onnx", "embedding_model.onnx", "hey_coco.onnx"])
        #expect(
            WakeWordResources.locate(in: dir)
                == .available(
                    WakeWordResources(
                        melSpectrogramURL: dir.appending(path: "melspectrogram.onnx"),
                        embeddingURL: dir.appending(path: "embedding_model.onnx"),
                        classifierURL: dir.appending(path: "hey_coco.onnx"))))
    }

    @Test func missingHeyCocoModelIsUnavailable() throws {
        let dir = try directory(["melspectrogram.onnx", "embedding_model.onnx"])
        #expect(WakeWordResources.locate(in: dir) == .unavailable(WakeWordResources.noWakeModelReason))
    }

    @Test func missingFeatureModelsAreUnavailable() throws {
        let dir = try directory(["embedding_model.onnx", "hey_coco.onnx"])
        #expect(WakeWordResources.locate(in: dir) == .unavailable(WakeWordResources.noFeatureModelsReason))
        let noEmbedding = try directory(["melspectrogram.onnx", "hey_coco.onnx"])
        #expect(WakeWordResources.locate(in: noEmbedding) == .unavailable(WakeWordResources.noFeatureModelsReason))
        #expect(WakeWordResources.locate(in: nil) == .unavailable(WakeWordResources.noFeatureModelsReason))
    }

    @Test func anEmptyModelFileDoesNotCount() throws {
        let dir = try directory(["melspectrogram.onnx", "embedding_model.onnx"])
        try Data().write(to: dir.appending(path: "hey_coco.onnx"))
        #expect(WakeWordResources.locate(in: dir) == .unavailable(WakeWordResources.noWakeModelReason))
    }
}
