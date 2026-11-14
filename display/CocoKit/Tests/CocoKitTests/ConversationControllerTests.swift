import Foundation
import Testing
@testable import CocoKit

@MainActor
private struct Harness {
    let log = CallLog()
    let recognizer = FakeRecognizer()
    let sleeper = ManualSleeper()
    let synthesizer: FakeSynthesizer
    let host: FakeConversationHost
    let controller: ConversationController

    init() {
        synthesizer = FakeSynthesizer(log: log)
        host = FakeConversationHost(log: log)
        controller = ConversationController(
            host: host, recognizer: recognizer, synthesizer: synthesizer,
            audio: FakeConversationAudio(log: log), messageHold: .seconds(4),
            sleep: sleeper.sleep)
    }

    /// Taps the mic and waits until the recognizer is running.
    func startListening() async {
        controller.micTapped()
        #expect(await waitUntil { recognizer.continuation != nil })
    }
}

private func action(
    say: String?, view: ViewSpec?, commands: [Command] = []
) -> Action {
    Action(say: say, view: view, commands: commands, intent: "test", matchedBy: .rules)
}

@MainActor
@Suite struct ConversationControllerTests {
    @Test func startsIdle() {
        #expect(Harness().controller.state == .idle)
    }

    @Test func fullLoopListensThinksSpeaksAndReturnsToIdle() async {
        let h = Harness()
        h.host.currentView = .timers
        h.host.result = .success(action(say: "Paris.", view: .answer(text: "Paris.")))
        h.host.holds = true
        h.synthesizer.holds = true

        await h.startListening()
        #expect(h.controller.state == .listening(partial: ""))

        h.recognizer.hear(.partial("capital of"))
        #expect(await waitUntil { h.controller.state == .listening(partial: "capital of") })

        h.recognizer.hear(.final("  capital of france "))
        #expect(await waitUntil { h.controller.state == .thinking(heard: "capital of france") })

        h.host.respond()
        #expect(await waitUntil { h.controller.state == .speaking(reply: "Paris.") })

        h.synthesizer.finishSpeaking()
        await h.controller.waitUntilFinished()
        #expect(h.controller.state == .idle)

        let request = h.host.requests.first
        #expect(request?.text == "capital of france")
        #expect(request?.source == .voice)
        #expect(request?.context == UtteranceContext(view: .timers, alarmRinging: false, mediaPlaying: nil))
        #expect(h.synthesizer.spoken == ["Paris."])
        #expect(
            h.log.entries == [
                "audio:begin", "audio:chime", "hub:capital of france", "view:answer", "say:Paris.",
                "audio:end",
            ])
    }

    @Test func requestReportsRingingAlarmFromHost() async {
        let h = Harness()
        h.host.alarmRinging = true
        h.host.result = .success(action(say: nil, view: nil, commands: [.alarmStop]))
        await h.startListening()
        h.recognizer.hear(.final("stop"))
        await h.controller.waitUntilFinished()
        #expect(h.host.requests.first?.context.alarmRinging == true)
    }

    @Test func requestReportsPlayingMediaFromHost() async {
        let h = Harness()
        h.host.mediaPlaying = .youtube
        h.host.result = .success(action(say: nil, view: nil, commands: [.mediaPause(target: .youtube)]))
        await h.startListening()
        h.recognizer.hear(.final("pause"))
        await h.controller.waitUntilFinished()
        #expect(h.host.requests.first?.context.mediaPlaying == .youtube)
    }

    @Test func commandsRunBeforeTheViewAndTheViewBeforeSpeech() async {
        let h = Harness()
        h.host.result = .success(
            action(say: "Stopped.", view: .idle, commands: [.alarmStop, .mediaPause(target: .music)]))

        await h.startListening()
        h.recognizer.hear(.final("stop"))
        await h.controller.waitUntilFinished()

        let relevant = h.log.entries.filter { !$0.hasPrefix("audio:") && !$0.hasPrefix("hub:") }
        #expect(relevant == ["command:alarm.stop", "command:media.pause", "view:idle", "say:Stopped."])
    }

    @Test func silentActionAppliesViewWithoutSpeaking() async {
        let h = Harness()
        h.host.result = .success(action(say: nil, view: .timers))
        await h.startListening()
        h.recognizer.hear(.final("show timers"))
        await h.controller.waitUntilFinished()
        #expect(h.host.currentView == .timers)
        #expect(h.synthesizer.spoken.isEmpty)
        #expect(h.controller.state == .idle)
    }

    @Test func emptyTranscriptReturnsToIdleQuietly() async {
        let h = Harness()
        await h.startListening()
        h.recognizer.hear(.final("   "))
        await h.controller.waitUntilFinished()
        #expect(h.controller.state == .idle)
        #expect(h.host.requests.isEmpty)
        #expect(h.synthesizer.spoken.isEmpty)
        #expect(h.log.entries.last == "audio:end")
    }

    @Test func streamEndingWithoutFinalUsesLastPartial() async {
        let h = Harness()
        h.host.result = .success(action(say: "Ok.", view: nil))
        await h.startListening()
        h.recognizer.hear(.partial("what time is it"))
        #expect(await waitUntil { h.controller.state == .listening(partial: "what time is it") })
        h.recognizer.finish()
        await h.controller.waitUntilFinished()
        #expect(h.host.requests.first?.text == "what time is it")
    }

    @Test func secondTapCancelsListening() async {
        let h = Harness()
        await h.startListening()
        h.recognizer.hear(.partial("set a"))
        #expect(await waitUntil { h.controller.state == .listening(partial: "set a") })

        h.controller.micTapped()
        #expect(h.controller.state == .idle)
        #expect(h.recognizer.cancelCount >= 1)

        // A late final from the cancelled run is ignored.
        h.recognizer.hear(.final("set a timer"))
        await h.controller.waitUntilFinished()
        #expect(h.host.requests.isEmpty)
        #expect(h.controller.state == .idle)
    }

    @Test func cancellingWhileThinkingDropsTheReply() async {
        let h = Harness()
        h.host.result = .success(action(say: "Done.", view: .timers))
        h.host.holds = true
        await h.startListening()
        h.recognizer.hear(.final("set a timer"))
        #expect(await waitUntil { h.host.isWaiting })

        h.controller.micTapped()
        #expect(h.controller.state == .idle)
        h.host.respond()
        try? await Task.sleep(for: .milliseconds(50))
        #expect(h.host.applied.isEmpty)
        #expect(h.synthesizer.spoken.isEmpty)
        #expect(h.controller.state == .idle)
    }

    @Test func tapWhileSpeakingStopsSpeech() async {
        let h = Harness()
        h.host.result = .success(action(say: "A long answer.", view: .answer(text: "A long answer.")))
        h.synthesizer.holds = true
        await h.startListening()
        h.recognizer.hear(.final("tell me something"))
        #expect(await waitUntil { h.controller.state == .speaking(reply: "A long answer.") })

        h.controller.micTapped()
        #expect(h.controller.state == .idle)
        #expect(h.synthesizer.stopCount >= 1)
        // The answer stays on screen.
        #expect(h.host.currentView == .answer(text: "A long answer."))
    }

    @Test func wakeWordBargesInWhileSpeaking() async {
        let h = Harness()
        h.host.result = .success(action(say: "Hello.", view: nil))
        h.synthesizer.holds = true
        await h.startListening()
        h.recognizer.hear(.final("hi"))
        #expect(await waitUntil { h.controller.state == .speaking(reply: "Hello.") })

        h.controller.wakeWordDetected()
        #expect(await waitUntil { h.recognizer.startCount == 2 })
        #expect(h.controller.state == .listening(partial: ""))
        #expect(h.synthesizer.stopCount >= 1)
    }

    @Test func unreachableHubIsShownAndSpokenThenIdles() async {
        let h = Harness()
        h.host.result = .failure(.unreachable("connection refused"))
        await h.startListening()
        h.recognizer.hear(.final("what's the weather"))

        let message = ConversationController.hubUnreachableMessage
        #expect(message == "I can't reach the Coco Hub right now.")
        #expect(await waitUntil { h.controller.state == .error(message: message) })
        #expect(await waitUntil { h.sleeper.pending == [.seconds(4)] })
        #expect(h.synthesizer.spoken == [message])

        h.sleeper.fire()
        await h.controller.waitUntilFinished()
        #expect(h.controller.state == .idle)
    }

    @Test func otherHubErrorsGetTheirOwnMessage() async {
        let h = Harness()
        h.host.result = .failure(.unauthorized)
        await h.startListening()
        h.recognizer.hear(.final("hello"))
        #expect(await waitUntil { if case .error = h.controller.state { true } else { false } })
        #expect(h.controller.state != .error(message: ConversationController.hubUnreachableMessage))
        #expect(h.synthesizer.spoken.count == 1)
    }

    @Test func deniedPermissionShowsWhyAndDoesNotListen() async {
        let h = Harness()
        h.recognizer.permission = .microphoneDenied
        h.controller.micTapped()
        await h.controller.waitUntilFinished()
        #expect(h.controller.state == .permissionNeeded(.microphoneDenied))
        #expect(h.recognizer.startCount == 0)

        // Tapping again tries again (the user may have fixed it in Settings).
        h.recognizer.permission = .granted
        h.controller.micTapped()
        #expect(await waitUntil { h.recognizer.startCount == 1 })
        #expect(h.controller.state == .listening(partial: ""))
    }

    @Test func recognizerFailureShowsErrorWithoutCallingHub() async {
        struct Boom: Error {}
        let h = Harness()
        h.recognizer.startError = Boom()
        h.controller.micTapped()
        #expect(await waitUntil { if case .error = h.controller.state { true } else { false } })
        #expect(h.host.requests.isEmpty)
        #expect(h.synthesizer.spoken.isEmpty)
        #expect(await waitUntil { h.sleeper.pending == [.seconds(4)] })
        h.sleeper.fire()
        await h.controller.waitUntilFinished()
        #expect(h.controller.state == .idle)
    }

    @Test func tappingDuringErrorStartsListeningAgain() async {
        let h = Harness()
        h.host.result = .failure(.unreachable("down"))
        await h.startListening()
        h.recognizer.hear(.final("hello"))
        #expect(await waitUntil { h.sleeper.pending.count == 1 })

        h.controller.micTapped()
        #expect(await waitUntil { h.recognizer.startCount == 2 })
        #expect(h.controller.state == .listening(partial: ""))
        #expect(h.sleeper.pending.isEmpty)
    }

    @Test func isActiveCoversListeningThinkingSpeaking() {
        #expect(ConversationState.listening(partial: "").isActive)
        #expect(ConversationState.thinking(heard: "x").isActive)
        #expect(ConversationState.speaking(reply: "x").isActive)
        #expect(!ConversationState.idle.isActive)
        #expect(!ConversationState.error(message: "x").isActive)
        #expect(!ConversationState.permissionNeeded(.speechRecognitionDenied).isActive)
    }
}

@MainActor
@Suite struct AppModelConversationHostTests {
    @Test func postUtteranceWithoutAHubIsUnreachable() async {
        let store = UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-tests-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore())
        let model = AppModel(
            config: store, discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
            makeClient: { _ in FakeHubAPI() }, makeEvents: { _ in AsyncStream { _ in } })
        let request = UtteranceRequest(
            text: "hi", source: .voice,
            context: UtteranceContext(view: .idle, alarmRinging: false, mediaPlaying: nil))
        do throws(HubError) {
            _ = try await model.postUtterance(request)
            Issue.record("expected an error")
        } catch {
            guard case .unreachable = error else {
                Issue.record("expected unreachable, got \(error)")
                return
            }
        }
    }
}
