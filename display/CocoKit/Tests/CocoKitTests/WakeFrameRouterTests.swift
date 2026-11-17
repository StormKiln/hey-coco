import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Frames handed to a sink, from whichever thread delivers them.
final class FrameLog: Sendable {
    private let frames = Mutex<[[Int16]]>([])
    var received: [[Int16]] { frames.withLock { $0 } }
    var sink: @Sendable ([Int16]) -> Void { { [self] frame in frames.withLock { $0.append(frame) } } }
}

/// A detector that fires on frames whose first sample is `trigger`, and counts its calls.
private final class ScriptedDetector: Sendable {
    static let trigger: Int16 = 999
    private let state = Mutex((processed: 0, resets: 0))
    var processed: Int { state.withLock { $0.processed } }
    var resets: Int { state.withLock { $0.resets } }

    func process(_ frame: [Int16]) -> Bool {
        state.withLock { $0.processed += 1 }
        return frame.first == Self.trigger
    }

    func reset() { state.withLock { $0.resets += 1 } }
}

private let wake: [Int16] = [ScriptedDetector.trigger]

private func router(_ detector: ScriptedDetector, maxBufferedFrames: Int = 100) -> WakeFrameRouter {
    WakeFrameRouter(
        sampleRate: 16_000, maxBufferedFrames: maxBufferedFrames,
        detect: { detector.process($0) }, resetDetector: { detector.reset() })
}

@Suite struct WakeFrameRouterTests {
    @Test func reportsADetectionAndCapturesEverythingAfterItInOrder() {
        let detector = ScriptedDetector()
        let router = router(detector)
        #expect(router.process([1]) == false)
        #expect(router.process(wake) == true)
        // Speech straight after "hey coco", before anyone is listening for it.
        #expect(router.process([2]) == false)
        #expect(router.process([3]) == false)

        let capture = router.claimCapture()
        #expect(capture?.sampleRate == 16_000)
        let log = FrameLog()
        capture?.attach(log.sink)
        #expect(log.received == [[2], [3]])
        router.process([4])
        router.process([5])
        #expect(log.received == [[2], [3], [4], [5]])
    }

    @Test func doesNotRunTheDetectorWhileCapturing() {
        let detector = ScriptedDetector()
        let router = router(detector)
        router.process(wake)
        router.process(wake)
        router.process([2])
        #expect(detector.processed == 1)
    }

    @Test func nothingToClaimWithoutADetection() {
        let router = router(ScriptedDetector())
        router.process([1])
        #expect(router.claimCapture() == nil)
    }

    @Test func detachStopsDeliveryAndResumeListensForTheWakeWordAgain() {
        let detector = ScriptedDetector()
        let router = router(detector)
        router.process(wake)
        let capture = router.claimCapture()
        let log = FrameLog()
        capture?.attach(log.sink)
        router.process([2])
        capture?.detach()
        router.process([3])
        #expect(log.received == [[2]])
        #expect(detector.processed == 1)

        router.resumeDetection()
        #expect(detector.resets == 1)
        #expect(router.process([4]) == false)
        #expect(router.process(wake) == true)
        #expect(detector.processed == 3)
    }

    @Test func aStaleCaptureCannotAttachToALaterOne() {
        let router = router(ScriptedDetector())
        router.process(wake)
        let old = router.claimCapture()
        router.resumeDetection()
        router.process(wake)
        router.process([7])
        let log = FrameLog()
        old?.attach(log.sink)
        router.process([8])
        #expect(log.received.isEmpty)
        // The current one still gets everything.
        let current = FrameLog()
        router.claimCapture()?.attach(current.sink)
        #expect(current.received == [[7], [8]])
        old?.detach()
        router.process([9])
        #expect(current.received == [[7], [8], [9]])
    }

    @Test func anUnclaimedCaptureIsDroppedAfterTheLimitAndDetectionResumes() {
        let detector = ScriptedDetector()
        let router = router(detector, maxBufferedFrames: 3)
        router.process(wake)
        router.process([1])
        router.process([2])
        router.process([3])
        // Nobody came for it: back to the wake word, fresh.
        router.process([4])
        #expect(router.claimCapture() == nil)
        #expect(detector.resets == 1)
        #expect(router.process(wake) == true)
    }

    @Test func resetDropsTheCapture() {
        let router = router(ScriptedDetector())
        router.process(wake)
        router.process([2])
        router.resumeDetection()
        #expect(router.claimCapture() == nil)
    }
}
