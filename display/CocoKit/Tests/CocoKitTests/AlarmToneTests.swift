import Foundation
import Testing
@testable import CocoKit

/// The alarm chime (#97): soft, bell-like, with headroom, and click-free.
@Suite struct AlarmToneTests {
    private let fullScale = Double(Int16.max)

    private func level(_ pcm: [Int16]) -> [Double] { pcm.map { Double($0) / fullScale } }

    /// Power of `samples` at `frequency` (Goertzel).
    private func power(_ samples: [Double], at frequency: Double) -> Double {
        let coefficient = 2 * cos(2 * .pi * frequency / Double(AlarmTone.sampleRate))
        var s1 = 0.0
        var s2 = 0.0
        for x in samples {
            let s0 = x + coefficient * s1 - s2
            s2 = s1
            s1 = s0
        }
        return s1 * s1 + s2 * s2 - coefficient * s1 * s2
    }

    @Test func wavHeaderIs44kMono16Bit() {
        let data = AlarmTone.wav(duration: 2)
        #expect(AlarmTone.sampleRate == 44_100)
        #expect(data.prefix(4) == Data("RIFF".utf8))
        #expect(data[8..<12] == Data("WAVE".utf8))
        let rate = data[24..<28].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
        #expect(UInt32(littleEndian: rate) == 44_100)
        #expect(data.count == 44 + Int(2 * Double(AlarmTone.sampleRate)) * 2)
    }

    @Test func peaksAroundMinus6dBFSWithoutClipping() {
        let pcm = level(AlarmTone.pcm(duration: AlarmTone.patternDuration))
        let peak = pcm.map(abs).max() ?? 0
        #expect(peak <= 0.5)
        #expect(peak >= 0.45)  // still a real level, not accidentally quiet
    }

    @Test func notesAreWarmBellPitches() {
        #expect(AlarmTone.notes.count >= 2 && AlarmTone.notes.count <= 3)
        for note in AlarmTone.notes {
            #expect((600...1_300).contains(note.frequency))
        }
        // The energy sits in the fundamentals, not in the old harsh 2 / 4 kHz band.
        let pcm = level(AlarmTone.pcm(duration: AlarmTone.patternDuration))
        let harsh = max(power(pcm, at: 2_000), power(pcm, at: 4_000))
        for note in AlarmTone.notes {
            #expect(power(pcm, at: note.frequency) > 20 * harsh)
        }
    }

    @Test func everyNoteStartsAndEndsAtZero() {
        let rate = Double(AlarmTone.sampleRate)
        for note in AlarmTone.notes {
            let alone = AlarmTone.render([note], duration: note.start + note.length + 0.01)
            let peak = alone.map(abs).max() ?? 0
            let first = Int((note.start * rate).rounded())
            let last = Int((note.start + note.length) * rate) - 1
            #expect(abs(alone[first]) < 1e-3 * peak)
            #expect(abs(alone[last]) < 1e-3 * peak)
            #expect(alone[(last + 1)...].allSatisfy { $0 == 0 })
        }
    }

    /// A click is a step: it shows up as a spike in the second difference, which for the chime's
    /// smooth partials stays near (2πf / rate)² × level ≈ 0.02. Any step of 0.04 full scale or
    /// more, anywhere (a note cut off, a bad loop seam), fails.
    @Test func noJumpsAnywhereIncludingTheLoopSeam() {
        let pcm = level(AlarmTone.pcm(duration: AlarmTone.patternDuration * 2))
        let jumps = zip(pcm, pcm.dropFirst()).map { abs($1 - $0) }
        #expect((jumps.max() ?? 1) < 0.15)
        let curvature = (1..<(pcm.count - 1)).map { abs(pcm[$0 + 1] - 2 * pcm[$0] + pcm[$0 - 1]) }
        #expect((curvature.max() ?? 1) < 0.04)
        // Looping: the pattern ends and begins in silence.
        let pattern = Int(AlarmTone.patternDuration * Double(AlarmTone.sampleRate))
        #expect(pcm[pattern - 1] == 0)
        #expect(pcm[0] == 0)
        #expect(Array(pcm[..<pattern]) == Array(pcm[pattern...]))
    }

    @Test func pausesBetweenRepeats() {
        let pcm = AlarmTone.pcm(duration: AlarmTone.patternDuration)
        // At least half a second of silence before it repeats.
        #expect(pcm.suffix(AlarmTone.sampleRate / 2).allSatisfy { $0 == 0 })
    }

    @Test func notificationCopyFitsTheLimitAndLoopsCleanly() {
        #expect(AlarmTone.notificationDuration < 30)
        #expect(AlarmTone.notificationDuration > 20)
        let repeats = AlarmTone.notificationDuration / AlarmTone.patternDuration
        #expect(abs(repeats - repeats.rounded()) < 1e-9)
    }

    @Test func installsUnderTheVersionedNameAndRemovesTheOldTone() throws {
        let sounds = FileManager.default.temporaryDirectory
            .appendingPathComponent("coco-sounds-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: sounds) }
        try FileManager.default.createDirectory(at: sounds, withIntermediateDirectories: true)
        let old = sounds.appendingPathComponent("coco-alarm.wav")
        try Data("old".utf8).write(to: old)

        let url = try AlarmTone.install(in: sounds)
        #expect(AlarmTone.fileName != "coco-alarm.wav")
        #expect(url.lastPathComponent == AlarmTone.fileName)
        #expect(try Data(contentsOf: url) == AlarmTone.wav(duration: AlarmTone.notificationDuration))
        #expect(!FileManager.default.fileExists(atPath: old.path))
        #expect(UserNotificationAlarms().soundName == AlarmTone.fileName)
    }
}

@Suite struct AlarmVolumeRampTests {
    @Test func startsModerateAndReachesFullOverTwentySeconds() {
        let ramp = AlarmVolumeRamp()
        #expect(ramp.duration == .seconds(20))
        #expect(ramp.volume(after: .zero) == ramp.initial)
        #expect(ramp.initial > 0.2 && ramp.initial < 0.5)
        #expect(ramp.volume(after: .seconds(20)) == 1)
        #expect(ramp.volume(after: .seconds(90)) == 1)
    }

    @Test func risesSteadilyInDecibels() {
        let ramp = AlarmVolumeRamp(initial: 0.1, duration: .seconds(10))
        let volumes = (0...10).map { ramp.volume(after: .seconds($0)) }
        #expect(zip(volumes, volumes.dropFirst()).allSatisfy { $0 < $1 })
        // Halfway in time is halfway in dB: -20 dB → -10 dB.
        #expect(abs(ramp.volume(after: .seconds(5)) - 0.316) < 0.01)
    }
}

@Suite struct AlarmAudioPolicyTests {
    @Test func alarmTakesPlaybackOnlyWhenTheVoiceLoopIsOff() {
        #expect(AlarmAudioPolicy.session(voiceOwnsSession: false) == .playback)
        #expect(AlarmAudioPolicy.session(voiceOwnsSession: true) == .shareVoiceSession)
    }

    /// While the alarm rings the mic belongs to the bare-"stop" listener; voice processing there
    /// would put the alarm through the voice-processing I/O (and rebuild it every few seconds).
    @Test func stopWordListenerSkipsVoiceProcessing() {
        #expect(AlarmAudioPolicy.voiceProcessing(for: .stopWord) == false)
        #expect(AlarmAudioPolicy.voiceProcessing(for: .conversation) == true)
        #expect(AlarmAudioPolicy.voiceProcessing(for: .wakeWord) == true)
    }
}
