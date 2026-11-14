import Foundation
import Testing
@testable import CocoKit

/// "One moment.": spoken once when the Hub is slow, without cancelling the request.
@MainActor
@Suite struct OneMomentTests {
    let log = CallLog()
    let recognizer = FakeRecognizer()
    let sleeper = ManualSleeper()
    let synthesizer: FakeSynthesizer
    let host: FakeConversationHost
    let controller: ConversationController

    init() {
        synthesizer = FakeSynthesizer(log: log)
        host = FakeConversationHost(log: log)
        host.holds = true
        controller = ConversationController(
            host: host, recognizer: recognizer, synthesizer: synthesizer,
            audio: FakeConversationAudio(log: log), messageHold: .seconds(4),
            sleep: sleeper.sleep)
    }

    private func askAndWaitForHub(_ text: String = "how tall is everest") async {
        controller.micTapped()
        #expect(await waitUntil { recognizer.continuation != nil })
        recognizer.hear(.final(text))
        #expect(await waitUntil { host.isWaiting })
    }

    private func reply(_ say: String) -> Action {
        Action(say: say, view: .answer(text: say), commands: [], intent: "test", matchedBy: .qwen)
    }

    @Test func slowHubGetsOneMomentThenTheAnswer() async {
        host.result = .success(reply("8,849 metres."))
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending == [ConversationController.oneMomentDelay] })
        #expect(ConversationController.oneMomentDelay == .milliseconds(1500))

        sleeper.fire()
        #expect(await waitUntil { synthesizer.spoken == [ConversationController.oneMomentMessage] })
        #expect(ConversationController.oneMomentMessage == "One moment.")
        // Still thinking: the request was not cancelled.
        #expect(controller.state == .thinking(heard: "how tall is everest"))
        #expect(host.isWaiting)

        host.respond()
        await controller.waitUntilFinished()
        #expect(synthesizer.spoken == ["One moment.", "8,849 metres."])
        #expect(host.requests.count == 1)
        #expect(controller.state == .idle)
    }

    @Test func saidOnlyOnce() async {
        host.result = .success(reply("Done."))
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        sleeper.fire()
        #expect(await waitUntil { synthesizer.spoken.count == 1 })
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.isEmpty)
        host.respond()
        await controller.waitUntilFinished()
        #expect(synthesizer.spoken.filter { $0 == "One moment." }.count == 1)
    }

    @Test func fastHubSaysNothingExtra() async {
        host.result = .success(reply("Paris."))
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        host.respond()
        await controller.waitUntilFinished()
        #expect(synthesizer.spoken == ["Paris."])
        // The pending nudge was cancelled with the reply.
        #expect(sleeper.pending.isEmpty)
    }

    @Test func replyWaitsForOneMomentToFinish() async {
        host.result = .success(reply("Paris."))
        synthesizer.holds = true
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        sleeper.fire()
        #expect(await waitUntil { synthesizer.isSpeaking })
        let stopsBefore = synthesizer.stopCount

        host.respond()
        try? await Task.sleep(for: .milliseconds(30))
        // "One moment." is not cut off by the reply.
        #expect(synthesizer.spoken == ["One moment."])
        #expect(synthesizer.stopCount == stopsBefore)
        #expect(synthesizer.isSpeaking)

        synthesizer.finishSpeaking()
        #expect(await waitUntil { controller.state == .speaking(reply: "Paris.") })
        synthesizer.finishSpeaking()
        await controller.waitUntilFinished()
        #expect(controller.state == .idle)
    }

    @Test func hubTimeoutAfterOneMomentStillReportsTheError() async {
        host.result = .failure(.unreachable("timed out"))
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        sleeper.fire()
        #expect(await waitUntil { synthesizer.spoken.count == 1 })

        host.respond()
        let message = ConversationController.hubUnreachableMessage
        #expect(await waitUntil { controller.state == .error(message: message) })
        #expect(synthesizer.spoken == ["One moment.", message])
    }

    @Test func cancellingWhileThinkingCancelsTheNudge() async {
        host.result = .success(reply("Paris."))
        await askAndWaitForHub()
        #expect(await waitUntil { sleeper.pending.count == 1 })
        controller.cancel()
        #expect(sleeper.pending.isEmpty)
        sleeper.fire()
        try? await Task.sleep(for: .milliseconds(30))
        #expect(synthesizer.spoken.isEmpty)
    }
}
