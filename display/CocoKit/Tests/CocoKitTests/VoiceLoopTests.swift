import Foundation
import Testing
@testable import CocoKit

@MainActor
private struct Harness {
    let log = CallLog()
    let recognizer: LoggingRecognizer
    let synthesizer: FakeSynthesizer
    let host: FakeConversationHost
    let session: FakeVoiceAudioSession
    let wake: FakeWakeWordEngine?
    let stopWord: FakeStopWordListener
    let sleeper = ManualSleeper()
    let loop: VoiceLoop

    init(wakeWord: Bool = true, micGranted: Bool = true) {
        recognizer = LoggingRecognizer(log: log)
        synthesizer = FakeSynthesizer(log: log)
        host = FakeConversationHost(log: log)
        session = FakeVoiceAudioSession(log: log)
        stopWord = FakeStopWordListener(log: log)
        if wakeWord {
            let wake = FakeWakeWordEngine()
            wake.log = { [log] in log.record("wake:\($0)") }
            self.wake = wake
        } else {
            wake = nil
        }
        loop = VoiceLoop(
            host: host,
            wakeWord: wake.map { .available($0) } ?? .unavailable(reason: "No AccessKey"),
            stopWord: stopWord,
            recognizer: recognizer,
            synthesizer: synthesizer,
            session: session,
            requestMicPermission: { micGranted },
            retryBackoff: .seconds(2),
            sleep: sleeper.sleep)
    }

    func entries(after marker: String) -> [String] {
        guard let index = log.entries.lastIndex(of: marker) else { return [] }
        return Array(log.entries[(index + 1)...])
    }

    /// Wakes Coco and waits until the recognizer is listening.
    func wakeAndListen() async {
        wake?.detect()
        #expect(await waitUntil { recognizer.fake.continuation != nil && loop.state == .listening })
    }
}

private func action(say: String?, view: ViewSpec? = nil, commands: [Command] = []) -> Action {
    Action(say: say, view: view, commands: commands, intent: "test", matchedBy: .rules)
}

@MainActor
@Suite struct VoiceLoopTests {
    // MARK: - Arming

    @Test func startArmsTheWakeWordInStandby() async {
        let h = Harness()
        #expect(h.loop.state == .idle(armed: false))
        await h.loop.start()
        #expect(h.wake?.isRunning == true)
        #expect(h.loop.wakeWord == .armed)
        #expect(h.loop.state == .idle(armed: true))
        #expect(h.loop.ducking == false)
        #expect(h.log.entries == ["session:standby", "wake:start"])
    }

    @Test func noConfigurationMeansTapToTalkOnly() async {
        let h = Harness(wakeWord: false)
        await h.loop.start()
        #expect(h.loop.wakeWord == .unavailable(reason: "No AccessKey"))
        #expect(h.loop.state == .idle(armed: false))
        // Nothing holds the mic while idle.
        #expect(h.log.entries.isEmpty)

        h.host.result = .success(action(say: nil))
        h.loop.conversation.micTapped()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        #expect(h.loop.state == .listening)
        #expect(h.loop.ducking)
        h.recognizer.fake.hear(.final("hello"))
        await h.loop.conversation.waitUntilFinished()
        #expect(h.loop.state == .idle(armed: false))
        #expect(h.loop.ducking == false)
        #expect(
            h.log.entries == [
                "session:duck", "session:chime", "recognizer:start", "hub:hello", "view:nil",
                "session:off",
            ])
    }

    @Test func micPermissionDeniedLeavesWakeWordOff() async {
        let h = Harness(micGranted: false)
        await h.loop.start()
        #expect(h.wake?.isRunning == false)
        #expect(h.loop.wakeWord == .unavailable(reason: VoiceLoop.micDeniedReason))
    }

    // MARK: - The listening loop

    @Test func wakeWordRunsTheFullLoopAndReArms() async {
        let h = Harness()
        h.host.result = .success(action(say: "Paris."))
        h.host.holds = true
        h.synthesizer.holds = true
        await h.loop.start()

        h.wake?.detect()
        #expect(await waitUntil { h.loop.state == .woken || h.loop.state == .listening })
        #expect(h.loop.ducking)
        #expect(await waitUntil { h.loop.state == .listening })

        h.recognizer.fake.hear(.final("capital of france"))
        #expect(await waitUntil { h.loop.state == .thinking })
        // Mic handed back to the wake word as soon as the recognizer is done (barge-in).
        #expect(h.wake?.isRunning == true)

        h.host.respond()
        #expect(await waitUntil { h.loop.state == .speaking })
        #expect(h.loop.ducking)

        h.synthesizer.finishSpeaking()
        await h.loop.conversation.waitUntilFinished()
        #expect(h.loop.state == .idle(armed: true))
        #expect(h.loop.ducking == false)
        #expect(h.wake?.isRunning == true)
        #expect(
            h.log.entries == [
                    "session:standby", "wake:start",
                    // wake → hand the mic over before the recognizer opens it
                    "wake:stop", "session:duck", "session:chime", "recognizer:start",
                    // recognizer done → wake word back for barge-in
                    "wake:start", "hub:capital of france", "view:nil", "say:Paris.",
                    // turn over → unduck, re-arm
                    "wake:stop", "session:off", "session:standby", "wake:start",
                ])
    }

    @Test func wakeWordIsStoppedBeforeTheRecognizerStartsOnTapToo() async {
        let h = Harness()
        h.host.result = .success(action(say: nil))
        await h.loop.start()
        h.loop.conversation.micTapped()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        #expect(h.wake?.isRunning == false)
        let stop = h.log.entries.lastIndex(of: "wake:stop")
        let start = h.log.entries.firstIndex(of: "recognizer:start")
        #expect(stop != nil && start != nil && stop! < start!)
    }

    @Test func wakeWordWhileSpeakingBargesIn() async {
        let h = Harness()
        h.host.result = .success(action(say: "A very long answer."))
        h.synthesizer.holds = true
        await h.loop.start()
        await h.wakeAndListen()
        h.recognizer.fake.hear(.final("tell me a story"))
        #expect(await waitUntil { h.loop.state == .speaking })
        #expect(h.wake?.isRunning == true)

        h.wake?.detect()
        #expect(await waitUntil { h.synthesizer.stopCount > 0 && h.loop.state == .listening })
        #expect(h.recognizer.fake.startCount == 2)
        #expect(h.wake?.isRunning == false)
    }

    @Test func cancellingDuringTheChimeReArms() async {
        let h = Harness()
        await h.loop.start()
        await h.wakeAndListen()
        h.loop.conversation.cancel()
        #expect(await waitUntil { h.loop.state == .idle(armed: true) })
        #expect(h.wake?.isRunning == true)
        #expect(h.loop.ducking == false)
    }

    // MARK: - Recovery

    @Test func interruptionStopsAndThenReArmsTheWakeWord() async {
        let h = Harness()
        await h.loop.start()
        h.session.send(.interruptionBegan)
        #expect(h.wake?.isRunning == false)
        #expect(h.loop.wakeWord == .paused)
        #expect(h.loop.state == .idle(armed: false))

        h.session.send(.interruptionEnded)
        #expect(h.wake?.isRunning == true)
        #expect(h.loop.wakeWord == .armed)
        #expect(h.entries(after: "wake:stop") == ["session:standby", "wake:start"])
    }

    @Test func interruptionCancelsAConversation() async {
        let h = Harness()
        await h.loop.start()
        await h.wakeAndListen()
        h.session.send(.interruptionBegan)
        #expect(await waitUntil { h.loop.conversation.state == .idle })
        #expect(h.wake?.isRunning == false)
        h.session.send(.interruptionEnded)
        #expect(h.wake?.isRunning == true)
    }

    @Test func routeChangeRestartsTheWakeWord() async {
        let h = Harness()
        await h.loop.start()
        h.session.send(.routeChanged)
        #expect(h.entries(after: "session:standby") == ["wake:start", "wake:stop", "wake:start"])
        #expect(h.loop.wakeWord == .armed)
    }

    @Test func startFailureRetriesAfterBackoff() async {
        let h = Harness()
        h.wake?.startError = TestError()
        await h.loop.start()
        #expect(h.loop.wakeWord == .retrying(after: .seconds(2)))
        #expect(await waitUntil { h.sleeper.pending == [.seconds(2)] })

        // Still failing: the backoff doubles.
        h.sleeper.fire()
        #expect(await waitUntil { h.sleeper.pending == [.seconds(4)] })
        #expect(h.loop.wakeWord == .retrying(after: .seconds(4)))

        h.wake?.startError = nil
        h.sleeper.fire()
        #expect(await waitUntil { h.loop.wakeWord == .armed })
        #expect(h.wake?.isRunning == true)
    }

    @Test func resumeReArmsAfterTheEngineWasStoppedBehindOurBack() async {
        let h = Harness()
        await h.loop.start()
        h.session.send(.interruptionBegan)
        // App came back to the foreground without an interruption-ended.
        await h.loop.resume()
        #expect(h.wake?.isRunning == true)
        #expect(h.loop.wakeWord == .armed)
    }

    // MARK: - Bare "stop" while an alarm rings (#23)

    @Test func stopWordIsNeverActiveWithoutARingingAlarm() async {
        let h = Harness()
        h.host.result = .success(action(say: nil))
        await h.loop.start()
        await h.wakeAndListen()
        h.recognizer.fake.hear(.final("hi"))
        await h.loop.conversation.waitUntilFinished()
        h.loop.alarmRingingChanged(false)
        #expect(h.stopWord.startCount == 0)
        #expect(h.loop.stopWordActive == false)
    }

    @Test func ringingAlarmSwapsTheWakeWordForTheStopWord() async {
        let h = Harness()
        await h.loop.start()
        h.loop.alarmRingingChanged(true)
        #expect(h.stopWord.isRunning)
        #expect(h.loop.stopWordActive)
        #expect(h.wake?.isRunning == false)
        #expect(h.entries(after: "wake:start") == ["wake:stop", "stopword:start"])

        h.loop.alarmRingingChanged(false)
        #expect(h.stopWord.isRunning == false)
        #expect(h.loop.stopWordActive == false)
        #expect(h.wake?.isRunning == true)
    }

    @Test func stopWordWorksWithoutAWakeWordConfigured() async {
        let h = Harness(wakeWord: false)
        await h.loop.start()
        h.loop.alarmRingingChanged(true)
        #expect(h.stopWord.isRunning)
        h.loop.alarmRingingChanged(false)
        #expect(h.stopWord.isRunning == false)
        #expect(h.log.entries == ["session:standby", "stopword:start", "stopword:stop", "session:off"])
    }

    @Test func hearingStopSilencesLocallyAndTellsTheHub() async {
        let h = Harness()
        h.host.alarmRinging = true
        h.host.result = .success(action(say: nil, commands: [.alarmStop]))
        await h.loop.start()
        h.loop.alarmRingingChanged(true)

        h.stopWord.hearStop()
        // Local silence first, before any network round trip.
        #expect(h.log.entries.contains("command:alarm.stop"))
        #expect(await waitUntil { h.host.requests.count == 1 })
        let request = h.host.requests[0]
        #expect(request.text == "stop")
        #expect(request.source == .voice)
        #expect(request.context.alarmRinging == true)
        let local = h.log.entries.firstIndex(of: "command:alarm.stop")!
        let hub = h.log.entries.firstIndex(of: "hub:stop")!
        #expect(local < hub)
        // Nothing is spoken for a bare stop.
        #expect(h.synthesizer.spoken.isEmpty)
    }

    @Test func stopWordIsIgnoredOnceTheAlarmStopped() async {
        let h = Harness()
        await h.loop.start()
        h.loop.alarmRingingChanged(true)
        let stopWord = h.stopWord
        h.loop.alarmRingingChanged(false)
        stopWord.hearStop()
        try? await Task.sleep(for: .milliseconds(50))
        #expect(!h.log.entries.contains("command:alarm.stop"))
        #expect(h.host.requests.isEmpty)
    }

    @Test func conversationTakesTheMicFromTheStopWordAndGivesItBack() async {
        let h = Harness()
        h.host.result = .success(action(say: nil))
        await h.loop.start()
        h.loop.alarmRingingChanged(true)
        h.loop.conversation.micTapped()
        #expect(await waitUntil { h.recognizer.fake.continuation != nil })
        #expect(h.stopWord.isRunning == false)
        h.recognizer.fake.hear(.final("add a minute"))
        await h.loop.conversation.waitUntilFinished()
        #expect(h.stopWord.isRunning)
        #expect(h.wake?.isRunning == false)
    }
}
