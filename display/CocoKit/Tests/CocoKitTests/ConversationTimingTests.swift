import Foundation
import Testing
@testable import CocoKit

@MainActor
@Suite struct AutoReturnTimerTests {
    private final class Counter { var count = 0 }

    private func makeTimer() -> (AutoReturnTimer, ManualSleeper, Counter) {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let timer = AutoReturnTimer(timeout: .seconds(20), sleep: sleeper.sleep) { counter.count += 1 }
        return (timer, sleeper, counter)
    }

    @Test func answerReturnsToIdleAfterTimeout() async {
        let (timer, sleeper, counter) = makeTimer()
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(timer.isArmed)
        #expect(await waitUntil { sleeper.pending == [.seconds(20)] })
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
        #expect(!timer.isArmed)
    }

    @Test func otherViewsDoNotArm() {
        let (timer, _, _) = makeTimer()
        timer.update(view: .idle, busy: false)
        #expect(!timer.isArmed)
        timer.update(view: .timers, busy: false)
        #expect(!timer.isArmed)
    }

    @Test func doesNotRunWhileBusy() async {
        let (timer, sleeper, counter) = makeTimer()
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(await waitUntil { sleeper.pending.count == 1 })
        timer.update(view: .answer(text: "42"), busy: true)
        #expect(!timer.isArmed)
        #expect(await waitUntil { sleeper.pending.isEmpty })
        #expect(counter.count == 0)
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(timer.isArmed)
    }

    @Test func interactionRestartsTheCountdown() async {
        let (timer, sleeper, counter) = makeTimer()
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(await waitUntil { sleeper.pending.count == 1 })
        timer.noteInteraction()
        // The old countdown is cancelled and a fresh one is waiting.
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending == [.seconds(20)])
        #expect(counter.count == 0)
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func interactionDoesNothingWhenDisarmed() async {
        let (timer, sleeper, _) = makeTimer()
        timer.update(view: .idle, busy: false)
        timer.noteInteraction()
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.isEmpty)
    }

    @Test func repeatedUpdatesDoNotExtendTheCountdown() async {
        let (timer, sleeper, _) = makeTimer()
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(await waitUntil { sleeper.pending.count == 1 })
        timer.update(view: .answer(text: "42"), busy: false)
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.count == 1)
    }

    @Test func aNewAnswerRestartsTheCountdown() async {
        let (timer, sleeper, counter) = makeTimer()
        timer.update(view: .answer(text: "1"), busy: false)
        #expect(await waitUntil { sleeper.pending.count == 1 })
        timer.update(view: .answer(text: "2"), busy: false)
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.count == 1)
        sleeper.fire()
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func leavingTheAnswerDisarms() async {
        let (timer, sleeper, counter) = makeTimer()
        timer.update(view: .answer(text: "42"), busy: false)
        #expect(await waitUntil { sleeper.pending.count == 1 })
        timer.update(view: .idle, busy: false)
        #expect(await waitUntil { sleeper.pending.isEmpty })
        #expect(counter.count == 0)
    }
}

@MainActor
@Suite struct UtteranceEndpointerTests {
    private final class Counter { var count = 0 }
    private let policy = UtteranceEndpointer.Policy(
        silenceTimeout: .milliseconds(1200), noSpeechTimeout: .seconds(5), maxDuration: .seconds(8))

    private func make() -> (UtteranceEndpointer, ManualSleeper, Counter) {
        let sleeper = ManualSleeper()
        let counter = Counter()
        let endpointer = UtteranceEndpointer(policy: policy, sleep: sleeper.sleep) { counter.count += 1 }
        return (endpointer, sleeper, counter)
    }

    @Test func defaultsMatchTheDesign() {
        let policy = UtteranceEndpointer.Policy()
        #expect(policy.silenceTimeout == .milliseconds(1200))
        #expect(policy.maxDuration == .seconds(8))
    }

    @Test func endsWhenNobodySpeaks() async {
        let (endpointer, sleeper, counter) = make()
        endpointer.start()
        #expect(await waitUntil { Set(sleeper.pending) == [.seconds(5), .seconds(8)] })
        sleeper.fire(.seconds(5))
        #expect(await waitUntil { counter.count == 1 })
        #expect(endpointer.hasEnded)
        #expect(await waitUntil { sleeper.pending.isEmpty })
    }

    @Test func endsAfterSilenceFollowingSpeech() async {
        let (endpointer, sleeper, counter) = make()
        endpointer.start()
        #expect(await waitUntil { sleeper.pending.count == 2 })
        endpointer.heardSpeech()
        #expect(await waitUntil { Set(sleeper.pending) == [.milliseconds(1200), .seconds(8)] })
        endpointer.heardSpeech()
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.filter { $0 == .milliseconds(1200) }.count == 1)
        sleeper.fire(.milliseconds(1200))
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func hardCapEndsLongUtterances() async {
        let (endpointer, sleeper, counter) = make()
        endpointer.start()
        endpointer.heardSpeech()
        #expect(await waitUntil { sleeper.pending.contains(.seconds(8)) })
        sleeper.fire(.seconds(8))
        #expect(await waitUntil { counter.count == 1 })
    }

    @Test func endsOnlyOnce() async {
        let (endpointer, sleeper, counter) = make()
        endpointer.start()
        #expect(await waitUntil { sleeper.pending.count == 2 })
        sleeper.fire()
        try? await Task.sleep(for: .milliseconds(30))
        #expect(counter.count == 1)
        endpointer.heardSpeech()
        try? await Task.sleep(for: .milliseconds(30))
        #expect(sleeper.pending.isEmpty)
    }

    @Test func cancelStopsEverything() async {
        let (endpointer, sleeper, counter) = make()
        endpointer.start()
        #expect(await waitUntil { sleeper.pending.count == 2 })
        endpointer.cancel()
        #expect(await waitUntil { sleeper.pending.isEmpty })
        #expect(counter.count == 0)
    }
}

@Suite struct ListeningChimeTests {
    @Test func isAValidMonoPCMWave() throws {
        let data = ListeningChime.wavData(sampleRate: 44_100)
        let bytes = [UInt8](data)
        #expect(String(decoding: bytes[0..<4], as: UTF8.self) == "RIFF")
        #expect(String(decoding: bytes[8..<12], as: UTF8.self) == "WAVE")
        #expect(String(decoding: bytes[36..<40], as: UTF8.self) == "data")
        func u32(_ at: Int) -> UInt32 {
            bytes[at..<at + 4].enumerated().reduce(0) { $0 | UInt32($1.element) << (8 * $1.offset) }
        }
        func u16(_ at: Int) -> UInt16 { UInt16(bytes[at]) | UInt16(bytes[at + 1]) << 8 }
        #expect(u32(4) == UInt32(bytes.count - 8))
        #expect(u16(20) == 1)  // PCM
        #expect(u16(22) == 1)  // mono
        #expect(u32(24) == 44_100)
        #expect(u16(34) == 16)
        #expect(u32(40) == UInt32(bytes.count - 44))
        let seconds = Double(bytes.count - 44) / 2 / 44_100
        #expect(seconds > 0.1 && seconds < 0.5)
    }

    @Test func startsAndEndsSilentToAvoidClicks() {
        let data = [UInt8](ListeningChime.wavData(sampleRate: 44_100))
        func sample(_ index: Int) -> Int16 {
            Int16(bitPattern: UInt16(data[44 + index * 2]) | UInt16(data[45 + index * 2]) << 8)
        }
        let count = (data.count - 44) / 2
        #expect(abs(Int(sample(0))) < 50)
        #expect(abs(Int(sample(count - 1))) < 50)
        #expect((0..<count).map { abs(Int(sample($0))) }.max()! > 5_000)
    }
}

@Suite struct VoiceSelectionTests {
    private typealias Voice = AppleSpeechSynthesizer.VoiceInfo

    @Test func prefersPremiumThenEnhancedInTheRequestedLanguage() {
        let voices = [
            Voice(identifier: "gb.premium", language: "en-GB", quality: .premium, isNovelty: false),
            Voice(identifier: "us.default", language: "en-US", quality: .default, isNovelty: false),
            Voice(identifier: "us.enhanced", language: "en-US", quality: .enhanced, isNovelty: false),
            Voice(identifier: "us.premium", language: "en-US", quality: .premium, isNovelty: false),
        ]
        #expect(AppleSpeechSynthesizer.bestVoice(in: voices, language: "en-US") == "us.premium")
    }

    @Test func fallsBackToAnyEnglishHighQualityVoice() {
        let voices = [
            Voice(identifier: "gb.enhanced", language: "en-GB", quality: .enhanced, isNovelty: false),
            Voice(identifier: "fr.premium", language: "fr-FR", quality: .premium, isNovelty: false),
        ]
        #expect(AppleSpeechSynthesizer.bestVoice(in: voices, language: "en-US") == "gb.enhanced")
    }

    @Test func noHighQualityVoiceMeansSystemDefault() {
        let voices = [
            Voice(identifier: "us.default", language: "en-US", quality: .default, isNovelty: false),
            Voice(identifier: "us.bubbles", language: "en-US", quality: .premium, isNovelty: true),
        ]
        #expect(AppleSpeechSynthesizer.bestVoice(in: voices, language: "en-US") == nil)
    }

    @Test func choiceIsStableRegardlessOfOrder() {
        let a = Voice(identifier: "a", language: "en-US", quality: .enhanced, isNovelty: false)
        let b = Voice(identifier: "b", language: "en-US", quality: .enhanced, isNovelty: false)
        #expect(AppleSpeechSynthesizer.bestVoice(in: [a, b], language: "en-US") == "a")
        #expect(AppleSpeechSynthesizer.bestVoice(in: [b, a], language: "en-US") == "a")
    }
}
