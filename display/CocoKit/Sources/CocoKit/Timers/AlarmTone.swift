import Foundation

/// The alarm sound (#97), synthesised in code (no third-party audio): a soft, rising three-note
/// bell chime, then a pause, as 16-bit mono PCM WAV at 44.1 kHz.
///
/// Each note is a sine fundamental (600–1,300 Hz) with two quieter harmonics that die away
/// faster, a 6 ms raised-cosine attack and an exponential decay, faded to exactly zero at its end.
/// The pattern starts and ends in silence, so it loops without a click, and is normalised to
/// `peakLevel` (-6 dBFS) so a small speaker isn't overdriven. Its loudness grows while it rings
/// (`AlarmVolumeRamp`), not in the file.
public enum AlarmTone {
    /// Notification sounds are looked up by file name in `Library/Sounds`. Versioned: an install
    /// keeps an existing file, so a changed tone needs a new name to reach existing devices.
    public static let fileName = "coco-chime-v2.wav"
    /// Earlier tones, removed on install.
    static let legacyFileNames = ["coco-alarm.wav"]
    public static let sampleRate = 44_100
    /// Peak amplitude as a fraction of full scale (-6 dBFS).
    public static let peakLevel = 0.5

    struct Note: Sendable {
        /// Seconds from the start of the pattern.
        let start: TimeInterval
        let frequency: Double
        /// Until the note has faded to silence.
        let length: TimeInterval
    }

    /// G5, B5, D6: a gently rising arpeggio, each note ringing on under the next.
    static let notes: [Note] = [
        Note(start: 0.00, frequency: 783.99, length: 1.1),
        Note(start: 0.22, frequency: 987.77, length: 1.1),
        Note(start: 0.44, frequency: 1_174.66, length: 1.1),
    ]
    /// One chime and its pause (the last note is silent by 1.54 s).
    public static let patternDuration: TimeInterval = 2.4
    /// Notification sounds longer than 30 s are replaced by the default sound; whole patterns
    /// only, so the copy loops cleanly too.
    public static let notificationDuration: TimeInterval = (29 / patternDuration).rounded(.down) * patternDuration

    /// (harmonic, relative amplitude, decay time constant in seconds): the overtones fade first,
    /// which is what makes it sound like a struck bell rather than a beep.
    private static let partials: [(harmonic: Double, amplitude: Double, decay: Double)] = [
        (1, 1.0, 0.35),
        (2, 0.28, 0.16),
        (3, 0.07, 0.08),
    ]
    private static let attack: TimeInterval = 0.006
    private static let release: TimeInterval = 0.15

    /// `duration` seconds of the repeating pattern as a WAV file.
    public static func wav(duration: TimeInterval) -> Data {
        let samples = pcm(duration: duration)
        var data = Data()
        func append<T: FixedWidthInteger>(_ value: T) {
            withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
        }
        let dataSize = UInt32(samples.count * 2)
        data.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36) + dataSize)
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        append(UInt32(16))  // fmt chunk size
        append(UInt16(1))  // PCM
        append(UInt16(1))  // mono
        append(UInt32(sampleRate))
        append(UInt32(sampleRate * 2))  // byte rate
        append(UInt16(2))  // block align
        append(UInt16(16))  // bits per sample
        data.append(contentsOf: Array("data".utf8))
        append(dataSize)
        for sample in samples { append(sample) }
        return data
    }

    static func pcm(duration: TimeInterval) -> [Int16] {
        let count = Int(duration * Double(sampleRate))
        let cycle = pattern
        return (0..<count).map { cycle[$0 % cycle.count] }
    }

    /// One pattern, normalised to `peakLevel`.
    private static let pattern: [Int16] = {
        let raw = render(notes, duration: patternDuration)
        let peak = raw.map(abs).max() ?? 1
        let scale = peakLevel * Double(Int16.max) / peak
        // Truncation toward zero keeps |sample| ≤ the peak and silence exactly 0.
        return raw.map { Int16($0 * scale) }
    }()

    /// `notes` mixed, unnormalised.
    static func render(_ notes: [Note], duration: TimeInterval) -> [Double] {
        let rate = Double(sampleRate)
        var out = [Double](repeating: 0, count: Int(duration * rate))
        for note in notes {
            let first = Int((note.start * rate).rounded())
            let count = Int(note.length * rate)
            for n in 0..<count where first + n < out.count {
                let t = Double(n) / rate
                out[first + n] += sample(t, frequency: note.frequency, length: note.length)
            }
        }
        return out
    }

    /// One note at `t` seconds into it: zero at 0 and at its last sample.
    private static func sample(_ t: TimeInterval, frequency: Double, length: TimeInterval) -> Double {
        var wave = 0.0
        for partial in partials {
            wave += partial.amplitude * exp(-t / partial.decay) * sin(2 * .pi * frequency * partial.harmonic * t)
        }
        let rise = t < attack ? (1 - cos(.pi * t / attack)) / 2 : 1
        let lastT = length - 1 / Double(sampleRate)
        let tail = lastT - t
        let fall = tail < release ? (1 - cos(.pi * max(0, tail) / release)) / 2 : 1
        return wave * rise * fall
    }

    /// Writes the tone to `Library/Sounds/<fileName>` (if missing) and returns its URL. The
    /// notification backup needs it there; `AudioAlarmPlayer` loops the same file.
    @discardableResult
    public static func installNotificationSound(fileManager: FileManager = .default) throws -> URL {
        let library = try fileManager.url(
            for: .libraryDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        return try install(in: library.appendingPathComponent("Sounds", isDirectory: true), fileManager: fileManager)
    }

    static func install(in sounds: URL, fileManager: FileManager = .default) throws -> URL {
        for legacy in legacyFileNames {
            try? fileManager.removeItem(at: sounds.appendingPathComponent(legacy))
        }
        let url = sounds.appendingPathComponent(fileName)
        if !fileManager.fileExists(atPath: url.path) {
            try fileManager.createDirectory(at: sounds, withIntermediateDirectories: true)
            try wav(duration: notificationDuration).write(to: url, options: .atomic)
        }
        return url
    }
}

/// How loud the alarm plays while it rings (#97): moderate at first, then rising steadily (linear
/// in decibels) to full volume after `duration`.
public struct AlarmVolumeRamp: Sendable, Equatable {
    /// Player volume at the start (0.32 ≈ -10 dB).
    public var initial: Float
    public var duration: Duration

    public init(initial: Float = 0.32, duration: Duration = .seconds(20)) {
        self.initial = initial
        self.duration = duration
    }

    public func volume(after elapsed: Duration) -> Float {
        if elapsed <= .zero { return initial }
        guard elapsed < duration, initial > 0 else { return 1 }
        let progress = Float(elapsed / duration)
        return powf(initial, 1 - progress)  // initial in dB, scaled toward 0 dB
    }
}
