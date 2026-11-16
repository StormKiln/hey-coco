import Foundation
import Synchronization
import Testing
@testable import CocoKit

/// `AlarmSound` that records its calls and can block the audio-session queue like a wedged
/// `AVAudioSession` call.
private final class FakeAlarmSound: AlarmSound {
    let calls = Mutex<[String]>([])
    let playing = Mutex(true)
    let volumes = Mutex<[Float]>([])
    private let gate = DispatchSemaphore(value: 0)
    private let blocks: Bool

    init(blocks: Bool = false) { self.blocks = blocks }

    func start(voiceOwnsSession: Bool, volume: Float) -> Bool {
        calls.withLock { $0.append("start(voice: \(voiceOwnsSession))") }
        volumes.withLock { $0.append(volume) }
        if blocks { gate.wait() }
        return playing.withLock { $0 }
    }

    func ensurePlaying(voiceOwnsSession: Bool, volume: Float) -> Bool {
        calls.withLock { $0.append("ensure") }
        volumes.withLock { $0.append(volume) }
        return playing.withLock { $0 }
    }

    func stop(voiceOwnsSession: Bool) {
        calls.withLock { $0.append("stop(voice: \(voiceOwnsSession))") }
    }

    func unblock() { gate.signal() }
    var recorded: [String] { calls.withLock { $0 } }
}

@MainActor
private final class Alerts {
    var count = 0
}

@MainActor
@Suite struct AudioAlarmPlayerTests {
    private func makePlayer(
        sound: FakeAlarmSound, queue: AudioSessionQueue, sleeper: ManualSleeper, alerts: Alerts,
        voiceOwns: Bool = false, ramp: AlarmVolumeRamp = AlarmVolumeRamp()
    ) -> AudioAlarmPlayer {
        AudioAlarmPlayer(
            sound: sound, queue: queue, voiceOwnsSession: { voiceOwns },
            alertSound: { alerts.count += 1 }, sleep: sleeper.sleep, ramp: ramp)
    }

    /// #97: starts moderate and gets louder on every watchdog tick until full volume.
    @Test func volumeRampsUpWhileRinging() async {
        let sound = FakeAlarmSound()
        let sleeper = ManualSleeper()
        let ramp = AlarmVolumeRamp(initial: 0.25, duration: .seconds(3))
        let player = makePlayer(
            sound: sound, queue: AudioSessionQueue(label: "test"), sleeper: sleeper, alerts: Alerts(),
            ramp: ramp)
        player.start()
        for tick in 1...4 {
            #expect(await waitUntil { sound.volumes.withLock { $0.count } == tick })
            try? await Task.sleep(for: .milliseconds(20))  // the report hops back to the main actor
            #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
            sleeper.fire()
        }
        #expect(await waitUntil { sound.volumes.withLock { $0.count } == 5 })
        let volumes = sound.volumes.withLock { $0 }
        #expect(volumes == (0...4).map { ramp.volume(after: .seconds($0)) })
        #expect(volumes.first == 0.25)
        #expect(volumes.last == 1)
        player.stop()
    }

    @Test func startPlaysOnTheQueueAndNeedsNoFallback() async {
        let sound = FakeAlarmSound()
        let sleeper = ManualSleeper()
        let alerts = Alerts()
        let player = makePlayer(
            sound: sound, queue: AudioSessionQueue(label: "test"), sleeper: sleeper, alerts: alerts)
        player.start()
        #expect(player.isRinging)
        #expect(await waitUntil { sound.recorded == ["start(voice: false)"] })
        try? await Task.sleep(for: .milliseconds(20))  // the report hops back to the main actor
        #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
        sleeper.fire()
        #expect(await waitUntil { sound.recorded.count == 2 })
        #expect(sound.recorded.last == "ensure")
        #expect(alerts.count == 0)
        player.stop()
        #expect(await waitUntil { sound.recorded.last == "stop(voice: false)" })
    }

    /// The freeze: the voice loop's `setActive` never returned. The alarm must not wait behind it.
    @Test func startIsNotHeldUpByAHungVoiceActivation() async {
        let queue = AudioSessionQueue(label: "test")
        let wedged = DispatchSemaphore(value: 0)
        queue.enqueue { wedged.wait() }  // the voice loop's activation, stuck in CoreAudio
        let sound = FakeAlarmSound()
        let sleeper = ManualSleeper()
        let alerts = Alerts()
        let player = makePlayer(
            sound: sound, queue: queue, sleeper: sleeper, alerts: alerts, voiceOwns: true)

        let began = ContinuousClock.now
        player.start()
        #expect(ContinuousClock.now - began < .milliseconds(100))
        #expect(player.isRinging)  // the overlay shows

        // The tone can't start, so the watchdog sounds the system alert instead, every tick,
        // without queueing more work behind the wedge.
        #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
        sleeper.fire()
        #expect(await waitUntil { alerts.count == 1 })
        #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
        sleeper.fire()
        #expect(await waitUntil { alerts.count == 2 })
        #expect(sound.recorded.isEmpty)

        // Unstuck: the queued start runs and the fallback stops.
        wedged.signal()
        #expect(await waitUntil { sound.recorded == ["start(voice: true)"] })
        try? await Task.sleep(for: .milliseconds(20))
        #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
        sleeper.fire()
        #expect(await waitUntil { sound.recorded.last == "ensure" })
        #expect(alerts.count == 2)
        player.stop()
    }

    @Test func aToneThatNeverPlaysFallsBackToTheAlertSound() async {
        let sound = FakeAlarmSound()
        sound.playing.withLock { $0 = false }
        let sleeper = ManualSleeper()
        let alerts = Alerts()
        let player = makePlayer(
            sound: sound, queue: AudioSessionQueue(label: "test"), sleeper: sleeper, alerts: alerts)
        player.start()
        #expect(await waitUntil { sound.recorded.count == 1 })
        try? await Task.sleep(for: .milliseconds(20))
        #expect(await waitUntil { sleeper.pending == [.seconds(1)] })
        sleeper.fire()
        #expect(await waitUntil { alerts.count == 1 })
        player.stop()
    }

    @Test func stopAfterAWedgedStartStillStopsOnceUnstuck() async {
        let sound = FakeAlarmSound(blocks: true)
        let sleeper = ManualSleeper()
        let alerts = Alerts()
        let player = makePlayer(
            sound: sound, queue: AudioSessionQueue(label: "test"), sleeper: sleeper, alerts: alerts)
        player.start()
        #expect(await waitUntil { sound.recorded.count == 1 })
        player.stop()
        #expect(player.isRinging == false)
        sound.unblock()
        #expect(await waitUntil { sound.recorded == ["start(voice: false)", "stop(voice: false)"] })
    }
}

@MainActor
@Suite struct AudioSessionQueueTests {
    /// A blocking session call on the queue doesn't block the main actor, and the timeout returns
    /// at its deadline even though the call never does.
    @Test func timeoutReturnsWhileTheSessionCallIsStillBlocked() async {
        let queue = AudioSessionQueue(label: "test")
        let wedged = DispatchSemaphore(value: 0)
        let sleeper = ManualSleeper()
        let outcome = Mutex<String?>(nil)

        Task { @MainActor in
            do {
                try await withAudioSessionTimeout(.seconds(3), sleep: sleeper.sleep) {
                    try await queue.run { wedged.wait() }
                }
                outcome.withLock { $0 = "returned" }
            } catch is AudioSessionTimeout {
                outcome.withLock { $0 = "timeout" }
            } catch {
                outcome.withLock { $0 = "error" }
            }
        }
        #expect(await waitUntil { sleeper.pending == [.seconds(3)] })
        // The main actor is free while the call is blocked.
        #expect(outcome.withLock { $0 } == nil)
        sleeper.fire()
        #expect(await waitUntil { outcome.withLock { $0 } == "timeout" })
        wedged.signal()
    }

    @Test func aFastCallCancelsItsTimeout() async throws {
        let sleeper = ManualSleeper()
        try await withAudioSessionTimeout(.seconds(3), sleep: sleeper.sleep) {
            try await AudioSessionQueue(label: "test").run {}
        }
        #expect(await waitUntil { sleeper.pending.isEmpty })
    }
}
