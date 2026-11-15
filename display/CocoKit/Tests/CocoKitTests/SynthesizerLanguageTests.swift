import Testing

@testable import CocoKit

/// The speaking language comes from the device's language list, not the text-to-speech service:
/// asking that service on the main thread at launch froze the app on a blank screen after a
/// simulator reboot (it waits on a semaphore until the speech daemon answers).
@Suite struct SynthesizerLanguageTests {
    @Test func englishDeviceLanguageIsUsed() {
        #expect(AppleSpeechSynthesizer.preferredLanguage(from: ["en-CA", "fr-CA"]) == "en-CA")
    }

    @Test func nonEnglishDeviceFallsBackToUSEnglish() {
        #expect(AppleSpeechSynthesizer.preferredLanguage(from: ["fr-CA", "en-CA"]) == "en-US")
    }

    @Test func bareEnglishBecomesUSEnglish() {
        #expect(AppleSpeechSynthesizer.preferredLanguage(from: ["en"]) == "en-US")
    }

    @Test func noLanguagesFallsBackToUSEnglish() {
        #expect(AppleSpeechSynthesizer.preferredLanguage(from: []) == "en-US")
    }

    @Test func scriptAndRegionKeepTheRegion() {
        #expect(AppleSpeechSynthesizer.preferredLanguage(from: ["en-Latn-GB"]) == "en-GB")
    }
}
