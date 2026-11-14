@preconcurrency import AVFoundation
import Foundation

/// The mic as fixed-length frames of 16-bit mono PCM at a fixed rate: what wake-word engines
/// (Porcupine: 512 samples at 16 kHz) consume. Voice processing (echo cancellation) is on, so
/// Coco's own speech, music and video are largely removed from what the engine hears.
///
/// Frames are delivered in order on a private serial queue, never on the main actor. Restarts
/// itself when the engine's configuration changes (route change, sample-rate change).
@MainActor
public final class PCMFrameMicrophone {
    public let sampleRate: Double
    public let frameLength: Int
    public private(set) var isRunning = false

    private var engine: AVAudioEngine?
    private var configObserver: (any NSObjectProtocol)?
    private var onFrame: (@Sendable ([Int16]) -> Void)?
    private let queue = DispatchQueue(label: "coco.mic-frames", qos: .userInteractive)

    public init(sampleRate: Double = 16_000, frameLength: Int = 512) {
        self.sampleRate = sampleRate
        self.frameLength = frameLength
    }

    public func start(onFrame: @escaping @Sendable ([Int16]) -> Void) throws {
        stop()
        self.onFrame = onFrame
        try startEngine()
        isRunning = true
    }

    public func stop() {
        isRunning = false
        onFrame = nil
        stopEngine()
    }

    private func startEngine() throws {
        guard let onFrame else { return }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        try VoiceProcessing.enable(on: input)
        let inputFormat = input.outputFormat(forBus: 0)
        guard
            let outputFormat = AVAudioFormat(
                commonFormat: .pcmFormatInt16, sampleRate: sampleRate, channels: 1, interleaved: true),
            let converter = AVAudioConverter(from: inputFormat, to: outputFormat)
        else { throw SpeechRecognitionError.failed("Unsupported microphone format \(inputFormat)") }

        let framer = Framer(
            converter: converter, outputFormat: outputFormat, frameLength: frameLength, queue: queue,
            onFrame: onFrame)
        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat, block: Self.tapBlock(framer))
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw error
        }
        self.engine = engine
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.restartAfterConfigurationChange() }
        }
    }

    private func stopEngine() {
        if let configObserver { NotificationCenter.default.removeObserver(configObserver) }
        configObserver = nil
        guard let engine else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        self.engine = nil
    }

    private func restartAfterConfigurationChange() {
        guard isRunning else { return }
        stopEngine()
        do {
            try startEngine()
        } catch {
            // VoiceLoop re-arms on the route-change event that usually accompanies this.
            isRunning = false
        }
    }

    private nonisolated static func tapBlock(_ framer: Framer) -> AVAudioNodeTapBlock {
        { buffer, _ in framer.consume(buffer) }
    }
}

/// Converts tap buffers and cuts them into frames. Only touched from the tap thread (`consume`)
/// and the serial queue (`pending`), hence `@unchecked Sendable`.
private final class Framer: @unchecked Sendable {
    let converter: AVAudioConverter
    let outputFormat: AVAudioFormat
    let frameLength: Int
    let queue: DispatchQueue
    let onFrame: @Sendable ([Int16]) -> Void
    private var pending: [Int16] = []

    init(
        converter: AVAudioConverter, outputFormat: AVAudioFormat, frameLength: Int,
        queue: DispatchQueue, onFrame: @escaping @Sendable ([Int16]) -> Void
    ) {
        self.converter = converter
        self.outputFormat = outputFormat
        self.frameLength = frameLength
        self.queue = queue
        self.onFrame = onFrame
    }

    func consume(_ buffer: AVAudioPCMBuffer) {
        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }
        nonisolated(unsafe) var supplied = false
        nonisolated(unsafe) let input = buffer
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return input
        }
        guard error == nil, let channel = output.int16ChannelData?[0], output.frameLength > 0 else { return }
        let samples = Array(UnsafeBufferPointer(start: channel, count: Int(output.frameLength)))
        queue.async { [self] in
            pending.append(contentsOf: samples)
            while pending.count >= frameLength {
                let frame = Array(pending.prefix(frameLength))
                pending.removeFirst(frameLength)
                onFrame(frame)
            }
        }
    }
}

/// Echo cancellation on a mic engine, without voice processing's default heavy ducking of
/// everything else the device plays.
enum VoiceProcessing {
    static func enable(on input: AVAudioInputNode) throws {
        try input.setVoiceProcessingEnabled(true)
        input.voiceProcessingOtherAudioDuckingConfiguration = AVAudioVoiceProcessingOtherAudioDuckingConfiguration(
            enableAdvancedDucking: false, duckingLevel: .min)
    }
}
