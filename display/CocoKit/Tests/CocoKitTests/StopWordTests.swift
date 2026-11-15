import Foundation
import Testing
@testable import CocoKit

@Suite struct StopWordMatcherTests {
    @Test(arguments: [
        "stop", "Stop.", "STOP!", "stop it", "Stop it.", "ok", "OK", "okay", "Okay.", "enough",
        "dismiss", "shut up", "off", "  stop  ", "hey coco, stop", "Hey Coco stop it", "coco stop",
        "hey, coco. Stop!", "stop stop", "stop, stop it",
    ])
    func accepts(_ phrase: String) {
        #expect(StopWordMatcher.matches(phrase))
    }

    @Test(arguments: [
        "", "hey coco", "don't stop", "stop the music", "bus stop", "okay google", "turn it off",
        "stopwatch", "what's the weather", "enough already", "set a timer",
    ])
    func rejects(_ phrase: String) {
        #expect(!StopWordMatcher.matches(phrase))
    }
}

@MainActor
@Suite struct RecognizerStopWordListenerTests {
    @Test func firesOnAStopPartialAndStopsListening() async {
        let recognizer = FakeRecognizer()
        let listener = RecognizerStopWordListener(recognizer: recognizer)
        var heard = 0
        try? listener.start { heard += 1 }
        #expect(listener.isRunning)
        #expect(await waitUntil { recognizer.continuation != nil })

        recognizer.hear(.partial("Stop"))
        #expect(await waitUntil { heard == 1 })
        #expect(listener.isRunning == false)
        #expect(recognizer.cancelCount >= 1)
    }

    @Test func ignoresOtherSpeechAndListensAgain() async {
        let recognizer = FakeRecognizer()
        let listener = RecognizerStopWordListener(recognizer: recognizer)
        var heard = 0
        try? listener.start { heard += 1 }
        #expect(await waitUntil { recognizer.startCount == 1 })

        recognizer.hear(.partial("don't"))
        recognizer.hear(.final("don't stop"))
        // A new short window opens after each utterance.
        #expect(await waitUntil { recognizer.startCount == 2 })
        #expect(heard == 0)

        recognizer.hear(.final("okay"))
        #expect(await waitUntil { heard == 1 })
        listener.stop()
    }

    @Test func stopEndsTheLoop() async {
        let recognizer = FakeRecognizer()
        let listener = RecognizerStopWordListener(recognizer: recognizer)
        var heard = 0
        try? listener.start { heard += 1 }
        #expect(await waitUntil { recognizer.startCount == 1 })
        listener.stop()
        #expect(listener.isRunning == false)
        recognizer.hear(.final("stop"))
        try? await Task.sleep(for: .milliseconds(50))
        #expect(heard == 0)
        #expect(recognizer.startCount == 1)
    }

    @Test func withoutSpeechPermissionItDoesNotListen() async {
        let recognizer = FakeRecognizer()
        recognizer.permission = .speechRecognitionDenied
        let listener = RecognizerStopWordListener(recognizer: recognizer)
        try? listener.start {}
        #expect(await waitUntil { listener.isRunning == false })
        #expect(recognizer.startCount == 0)
    }
}
