import Foundation

/// The alarm sound, synthesised in code (no third-party audio): bursts of four loud beeps with a
/// pause, as 16-bit mono PCM WAV.
public enum AlarmTone {
    /// Notification sounds are looked up by file name in `Library/Sounds`.
    public static let fileName = "coco-alarm.wav"
    public static let sampleRate = 22_050
    /// One beep-beep-beep-beep-pause cycle.
    public static let patternDuration: TimeInterval = 1.6
    /// Notification sounds longer than 30 s are replaced by the default sound.
    public static let notificationDuration: TimeInterval = 29

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
        let patternLength = Int(patternDuration * Double(sampleRate))
        let beep = Int(0.15 * Double(sampleRate))
        let gap = Int(0.1 * Double(sampleRate))
        let fade = Int(0.005 * Double(sampleRate))
        var out = [Int16](repeating: 0, count: count)
        for i in 0..<count {
            let p = i % patternLength
            let slot = p / (beep + gap)
            let inSlot = p % (beep + gap)
            guard slot < 4, inSlot < beep else { continue }
            let t = Double(inSlot) / Double(sampleRate)
            // Two partials around 2–4 kHz cut through kitchen noise on a small speaker.
            let wave = 0.7 * sin(2 * .pi * 2_000 * t) + 0.3 * sin(2 * .pi * 4_000 * t)
            let envelope = min(1, Double(min(inSlot, beep - inSlot)) / Double(fade))
            out[i] = Int16(wave * envelope * 0.95 * Double(Int16.max))
        }
        return out
    }

    /// Writes the tone to `Library/Sounds/coco-alarm.wav` (if missing) and returns its URL. The
    /// notification backup needs it there; `AudioAlarmPlayer` loops the same file.
    @discardableResult
    public static func installNotificationSound(fileManager: FileManager = .default) throws -> URL {
        let library = try fileManager.url(
            for: .libraryDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let sounds = library.appendingPathComponent("Sounds", isDirectory: true)
        let url = sounds.appendingPathComponent(fileName)
        if !fileManager.fileExists(atPath: url.path) {
            try fileManager.createDirectory(at: sounds, withIntermediateDirectories: true)
            try wav(duration: notificationDuration).write(to: url, options: .atomic)
        }
        return url
    }
}
