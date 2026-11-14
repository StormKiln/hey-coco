import Foundation
@testable import CocoKit

/// `SpeechRecognizer` that logs `recognizer:start` to a shared `CallLog`
/// and otherwise behaves like `FakeRecognizer`.
@MainActor
final class LoggingRecognizer: SpeechRecognizer {
    let fake = FakeRecognizer()
    let log: CallLog
    let name: String

    init(log: CallLog, name: String = "recognizer") {
        self.log = log
        self.name = name
    }

    func requestPermission() async -> SpeechPermission { await fake.requestPermission() }

    func start() throws -> AsyncThrowingStream<SpeechTranscript, any Error> {
        log.record("\(name):start")
        return try fake.start()
    }

    func stop() { fake.stop() }

    func cancel() { fake.cancel() }
}

@MainActor
final class FakeVoiceAudioSession: VoiceAudioSession {
    let log: CallLog
    var standbyError: (any Error)?
    var onEvent: (@MainActor (AudioSessionEvent) -> Void)?
    private(set) var mode = "inactive"

    init(log: CallLog) { self.log = log }

    func activateStandby() throws {
        if let standbyError { throw standbyError }
        guard mode != "standby" else { return }
        mode = "standby"
        log.record("session:standby")
    }

    func beginConversation() throws {
        mode = "conversation"
        log.record("session:duck")
    }

    func playListeningChime() async { log.record("session:chime") }

    func deactivate() {
        guard mode != "inactive" else { return }
        mode = "inactive"
        log.record("session:off")
    }

    func send(_ event: AudioSessionEvent) {
        if event == .interruptionBegan || event == .mediaServicesReset { mode = "inactive" }
        onEvent?(event)
    }
}

/// `StopWordListener` the test triggers by hand.
@MainActor
final class FakeStopWordListener: StopWordListener {
    let log: CallLog
    private(set) var isRunning = false
    private(set) var startCount = 0
    private var onStop: (@MainActor @Sendable () -> Void)?

    init(log: CallLog) { self.log = log }

    func start(onStop: @escaping @MainActor @Sendable () -> Void) throws {
        startCount += 1
        isRunning = true
        self.onStop = onStop
        log.record("stopword:start")
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        onStop = nil
        log.record("stopword:stop")
    }

    /// Simulates hearing "stop".
    func hearStop() { onStop?() }
}

struct TestError: Error, Equatable {}
