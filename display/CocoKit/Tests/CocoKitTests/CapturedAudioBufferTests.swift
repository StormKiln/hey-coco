import AVFoundation
import Testing
@testable import CocoKit

@Suite struct CapturedAudioBufferTests {
    @Test func framesBecomeFloatMonoBuffersAtTheirRate() throws {
        let buffer = try #require(CapturedAudioBuffer.make(from: [0, 16_384, -32_768, 32_767], sampleRate: 16_000))
        #expect(buffer.format.sampleRate == 16_000)
        #expect(buffer.format.channelCount == 1)
        #expect(buffer.format.commonFormat == .pcmFormatFloat32)
        #expect(buffer.frameLength == 4)
        let samples = Array(UnsafeBufferPointer(start: try #require(buffer.floatChannelData)[0], count: 4))
        #expect(samples[0] == 0)
        #expect(samples[1] == 0.5)
        #expect(samples[2] == -1)
        #expect(abs(samples[3] - 1) < 0.0001)
    }

    @Test func anEmptyFrameMakesNoBuffer() {
        #expect(CapturedAudioBuffer.make(from: [], sampleRate: 16_000) == nil)
    }
}
