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

@Suite struct WakeWordResourcesTests {
    private func directory(_ files: [String: String]) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appending(path: "wake-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        for (name, contents) in files {
            try contents.write(to: url.appending(path: name), atomically: true, encoding: .utf8)
        }
        return url
    }

    @Test func findsKeyAndIOSKeyword() throws {
        let dir = try directory(["PicovoiceAccessKey.txt": "  abc/def==\n", "hey-coco_ios.ppn": "x"])
        let found = WakeWordResources.locate(in: dir, runningOnMac: false)
        #expect(
            found
                == .available(
                    WakeWordResources(
                        accessKey: "abc/def==", keywordURL: dir.appending(path: "hey-coco_ios.ppn"))))
    }

    @Test func prefersTheMacKeywordOnMacAndFallsBack() throws {
        let both = try directory([
            "PicovoiceAccessKey.txt": "k", "hey-coco_ios.ppn": "x", "hey-coco_mac.ppn": "y",
        ])
        #expect(
            WakeWordResources.locate(in: both, runningOnMac: true)
                == .available(.init(accessKey: "k", keywordURL: both.appending(path: "hey-coco_mac.ppn"))))
        let iosOnly = try directory(["PicovoiceAccessKey.txt": "k", "hey-coco_ios.ppn": "x"])
        #expect(
            WakeWordResources.locate(in: iosOnly, runningOnMac: true)
                == .available(.init(accessKey: "k", keywordURL: iosOnly.appending(path: "hey-coco_ios.ppn"))))
    }

    @Test func missingOrEmptyKeyIsUnavailable() throws {
        let noKey = try directory(["hey-coco_ios.ppn": "x"])
        #expect(WakeWordResources.locate(in: noKey, runningOnMac: false) == .unavailable(WakeWordResources.noKeyReason))
        let blank = try directory(["PicovoiceAccessKey.txt": " \n", "hey-coco_ios.ppn": "x"])
        #expect(WakeWordResources.locate(in: blank, runningOnMac: false) == .unavailable(WakeWordResources.noKeyReason))
    }

    @Test func missingKeywordIsUnavailable() throws {
        let dir = try directory(["PicovoiceAccessKey.txt": "k"])
        #expect(WakeWordResources.locate(in: dir, runningOnMac: false) == .unavailable(WakeWordResources.noKeywordReason))
        #expect(WakeWordResources.locate(in: nil, runningOnMac: false) == .unavailable(WakeWordResources.noKeyReason))
    }
}
