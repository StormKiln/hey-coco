import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// Device readings the test sets before each sample.
@MainActor
final class FakeDeviceConditions: DeviceConditionsSource {
    var next = DeviceConditions(
        thermal: .nominal, batteryLevel: 1.0, batteryState: .charging, cpuSeconds: 0)
    private(set) var sampled = 0

    func sample() -> DeviceConditions {
        sampled += 1
        return next
    }
}

@MainActor
private func makeRecorder(
    clock: TestClock = TestClock(Date(timeIntervalSince1970: 1_800_000_000)),
    conditions: FakeDeviceConditions = FakeDeviceConditions(),
    sleeper: ManualSleeper = ManualSleeper()
) -> VoiceTestRecorder {
    VoiceTestRecorder(
        conditions: conditions, now: clock.function, sleep: sleeper.sleep,
        timeZone: TimeZone(identifier: "UTC")!)
}

@MainActor
@Suite struct VoiceTestRecorderTests {
    @Test func offByDefaultAndIgnoresEventsWhileOff() {
        let recorder = makeRecorder()
        #expect(!recorder.isRecording)
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.finalTranscript("set a timer"))
        recorder.record(.ended)
        #expect(recorder.summary.wakeDetections == 0)
        #expect(recorder.turns.isEmpty)
    }

    @Test func wakeFollowedByARequestIsCountedWithItsLatencies() {
        let clock = TestClock(Date(timeIntervalSince1970: 1_800_000_000))
        let recorder = makeRecorder(clock: clock)
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        clock.advance(by: 10)
        recorder.wakeWordDetected()
        recorder.record(.started)
        clock.advance(by: 1)
        recorder.record(.heardSpeech)
        clock.advance(by: 0.5)
        recorder.record(.heardSpeech)  // speech ends here
        clock.advance(by: 1.6)
        recorder.record(.finalTranscript("set a pasta timer"))
        clock.advance(by: 0.3)
        recorder.record(.hubReplied(succeeded: true))
        recorder.record(.ended)

        let s = recorder.summary
        #expect(s.wakeDetections == 1)
        #expect(s.wakesWithRequest == 1)
        #expect(s.probableFalseWakes == 0)
        #expect(recorder.turns.count == 1)
        let turn = recorder.turns[0]
        #expect(turn.trigger == .wakeWord)
        #expect(turn.words == 4)
        #expect(turn.speechEndToFinal.map { abs($0 - 1.6) < 0.001 } == true)
        #expect(turn.finalToReply.map { abs($0 - 0.3) < 0.001 } == true)
        #expect(turn.replySucceeded == true)
    }

    @Test func wakeFollowedByNothingHeardIsAProbableFalseWake() {
        let clock = TestClock(Date(timeIntervalSince1970: 1_800_000_000))
        let recorder = makeRecorder(clock: clock)
        recorder.start(onDeviceRecognition: nil, wakeWord: .armed)
        recorder.wakeWordDetected()
        recorder.record(.started)
        clock.advance(by: 5)
        recorder.record(.finalTranscript(""))
        recorder.record(.ended)
        // A recognizer failure counts the same.
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.recognitionFailed)
        recorder.record(.ended)
        // So does a turn cancelled before anything was heard.
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.ended)
        #expect(recorder.summary.wakeDetections == 3)
        #expect(recorder.summary.probableFalseWakes == 3)
        #expect(recorder.wakeDetections.allSatisfy { $0.outcome == .probableFalseWake })
        #expect(recorder.turns.allSatisfy { $0.speechEndToFinal == nil && $0.finalToReply == nil })
    }

    @Test func wakeWhileAPreviousWakeIsUnresolvedResolvesItAsFalse() {
        let recorder = makeRecorder()
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        recorder.wakeWordDetected()
        recorder.record(.started)
        // Barge-in: no end event for the abandoned turn.
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.heardSpeech)
        recorder.record(.finalTranscript("stop"))
        recorder.record(.ended)
        #expect(recorder.wakeDetections.map(\.outcome) == [.probableFalseWake, .request])
        #expect(recorder.turns.count == 2)
    }

    @Test func tapsAreTurnsButNotWakeDetections() {
        let recorder = makeRecorder()
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        recorder.record(.started)
        recorder.record(.heardSpeech)
        recorder.record(.finalTranscript("what's the weather"))
        recorder.record(.hubReplied(succeeded: false))
        recorder.record(.ended)
        #expect(recorder.summary.wakeDetections == 0)
        #expect(recorder.summary.taps == 1)
        #expect(recorder.turns.first?.trigger == .tap)
        #expect(recorder.turns.first?.replySucceeded == false)
    }

    @Test func listeningTimeIsSplitByWakeWordStatus() {
        let clock = TestClock(Date(timeIntervalSince1970: 1_800_000_000))
        let recorder = makeRecorder(clock: clock)
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        clock.advance(by: 50)
        recorder.wakeWordStatusChanged(.paused)
        clock.advance(by: 5)
        recorder.wakeWordStatusChanged(.retrying(after: .seconds(2)))
        clock.advance(by: 2)
        recorder.wakeWordStatusChanged(.armed)
        clock.advance(by: 3)
        recorder.stop()
        let s = recorder.summary
        #expect(s.duration == 60)
        #expect(s.armed == 53)
        #expect(s.paused == 5)
        #expect(s.restarting == 2)
        #expect(s.off == 0)
        #expect(s.wakeWordFailures == 1)
        // After stop nothing more is counted.
        clock.advance(by: 100)
        recorder.wakeWordStatusChanged(.paused)
        #expect(recorder.summary.duration == 60)
        #expect(recorder.summary.armed == 53)
    }

    @Test func samplesDeviceConditionsAtStartEveryMinuteAndAtStop() async {
        let clock = TestClock(Date(timeIntervalSince1970: 1_800_000_000))
        let conditions = FakeDeviceConditions()
        let sleeper = ManualSleeper()
        let recorder = makeRecorder(clock: clock, conditions: conditions, sleeper: sleeper)
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        #expect(recorder.samples.count == 1)
        #expect(await waitUntil { sleeper.pending == [.seconds(60)] })

        clock.advance(by: 60)
        conditions.next = DeviceConditions(
            thermal: .fair, batteryLevel: 0.97, batteryState: .unplugged, cpuSeconds: 6)
        sleeper.fire()
        #expect(await waitUntil { recorder.samples.count == 2 })
        #expect(recorder.samples[1].cpuPercent.map { abs($0 - 10) < 0.001 } == true)

        clock.advance(by: 30)
        recorder.stop()
        #expect(recorder.samples.count == 3)
        #expect(await waitUntil { sleeper.pending.isEmpty })
        #expect(recorder.summary.maxThermal == .fair)
    }

    @Test func startingAgainClearsTheLastRun() {
        let recorder = makeRecorder()
        recorder.start(onDeviceRecognition: true, wakeWord: .armed)
        recorder.wakeWordDetected()
        recorder.stop()
        recorder.start(onDeviceRecognition: false, wakeWord: .armed)
        #expect(recorder.summary.wakeDetections == 0)
        #expect(recorder.onDeviceRecognition == false)
        recorder.stop()
    }

    @Test func reportIsPlainTextWithTheMeasurementsAndNoTranscripts() {
        let clock = TestClock(Date(timeIntervalSince1970: 1_800_000_000))
        let recorder = makeRecorder(clock: clock)
        recorder.start(onDeviceRecognition: true, wakeWord: .armed, device: "iPad (iPad7,11), iPadOS 18.6")
        clock.advance(by: 10)
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.heardSpeech)
        clock.advance(by: 1.5)
        recorder.record(.finalTranscript("set a secret pasta timer"))
        clock.advance(by: 0.25)
        recorder.record(.hubReplied(succeeded: true))
        recorder.record(.ended)
        clock.advance(by: 20)
        recorder.wakeWordDetected()
        recorder.record(.started)
        recorder.record(.finalTranscript(""))
        recorder.record(.ended)
        clock.advance(by: 30)
        recorder.stop()

        let report = recorder.report()
        #expect(report.contains("Coco voice test"))
        #expect(report.contains("iPad (iPad7,11), iPadOS 18.6"))
        #expect(report.contains("On-device recognition supported: yes"))
        #expect(report.contains("Wake word detections: 2"))
        #expect(report.contains("Probable false wakes: 1"))
        #expect(report.contains("08:00:31"))  // the false wake's time (UTC)
        #expect(report.contains("Speech end → final transcript: median 1.5 s"))
        #expect(report.contains("Final transcript → Hub reply: median 250 ms"))
        #expect(report.contains("Duration: 1 m"))
        #expect(!report.contains("pasta"))
    }
}

/// The recorder wired into the real loop: what the loop reports while a test runs.
@MainActor
@Suite struct VoiceLoopRecordingTests {
    @MainActor
    private struct Harness {
        let log = CallLog()
        let recognizer: LoggingRecognizer
        let synthesizer: FakeSynthesizer
        let host: FakeConversationHost
        let wake = FakeWakeWordEngine()
        let loop: VoiceLoop

        init() {
            recognizer = LoggingRecognizer(log: log)
            synthesizer = FakeSynthesizer(log: log)
            host = FakeConversationHost(log: log)
            loop = VoiceLoop(
                host: host, wakeWord: .available(wake),
                stopWord: FakeStopWordListener(log: log), recognizer: recognizer,
                synthesizer: synthesizer, session: FakeVoiceAudioSession(log: log),
                requestMicPermission: { true }, sleep: ManualSleeper().sleep,
                voiceTest: VoiceTestRecorder(conditions: FakeDeviceConditions()))
        }
    }

    @Test func wakeRequestAndReplyAreRecorded() async {
        let h = Harness()
        await h.loop.start()
        h.loop.voiceTest.start(onDeviceRecognition: true, wakeWord: h.loop.wakeWord)
        h.host.result = .success(Action(say: nil, view: nil, commands: [], intent: "t", matchedBy: .rules))
        h.wake.detect()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        h.recognizer.fake.hear(.partial("set a"))
        h.recognizer.fake.hear(.final("set a timer"))
        #expect(await waitUntil { h.loop.voiceTest.turns.count == 1 })
        let turn = h.loop.voiceTest.turns[0]
        #expect(turn.trigger == .wakeWord)
        #expect(turn.words == 3)
        #expect(turn.speechEndToFinal != nil)
        #expect(turn.finalToReply != nil)
        #expect(h.loop.voiceTest.summary.wakesWithRequest == 1)
        h.loop.voiceTest.stop()
    }

    @Test func wakeWithNothingSaidIsAProbableFalseWake() async {
        let h = Harness()
        await h.loop.start()
        h.loop.voiceTest.start(onDeviceRecognition: true, wakeWord: h.loop.wakeWord)
        h.wake.detect()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        h.recognizer.fake.finish()
        #expect(await waitUntil { h.loop.voiceTest.turns.count == 1 })
        #expect(h.loop.voiceTest.summary.probableFalseWakes == 1)
        h.loop.voiceTest.stop()
    }

    @Test func statusChangesReachTheRecorder() async {
        let h = Harness()
        await h.loop.start()
        h.loop.voiceTest.start(onDeviceRecognition: true, wakeWord: h.loop.wakeWord)
        h.wake.detect()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        #expect(h.loop.voiceTest.wakeWordStatus == .paused)
        h.recognizer.fake.finish()
        #expect(await waitUntil { h.loop.voiceTest.wakeWordStatus == .armed })
        h.loop.voiceTest.stop()
    }

    @Test func nothingIsRecordedUnlessStarted() async {
        let h = Harness()
        await h.loop.start()
        h.wake.detect()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        h.recognizer.fake.finish()
        #expect(await waitUntil { h.loop.state == .idle(armed: true) })
        #expect(h.loop.voiceTest.turns.isEmpty)
        #expect(h.loop.voiceTest.summary.wakeDetections == 0)
    }
}
