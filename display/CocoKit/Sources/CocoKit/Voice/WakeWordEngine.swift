import Foundation

/// Listens for the wake phrase ("hey coco") and calls back on every detection. Porcupine is the
/// first implementation (in the app target, since Picovoice's package is iOS-only); the protocol
/// keeps it swappable if its free tier or accuracy disappoints.
///
/// `start` opens the microphone; it must not be running while the speech recognizer is
/// (`VoiceLoop` hands the mic back and forth).
@MainActor
public protocol WakeWordEngine: AnyObject {
    var isRunning: Bool { get }
    /// Starts listening. `onDetected` is called on the main actor for each detection.
    func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws
    func stop()
}

/// Whether the wake word can run on this build, and if not, why (shown subtly in the UI).
public enum WakeWordAvailability {
    case available(any WakeWordEngine)
    case unavailable(reason: String)
}

/// A wake-word engine the test (or a preview) triggers by hand.
@MainActor
public final class FakeWakeWordEngine: WakeWordEngine {
    public private(set) var isRunning = false
    public private(set) var startCount = 0
    /// Thrown by the next `start()` while set.
    public var startError: (any Error)?
    /// Records "start" / "stop" (tests use it for ordering).
    public var log: ((String) -> Void)?
    private var onDetected: (@MainActor @Sendable () -> Void)?

    public init() {}

    public func start(onDetected: @escaping @MainActor @Sendable () -> Void) throws {
        startCount += 1
        if let startError { throw startError }
        isRunning = true
        self.onDetected = onDetected
        log?("start")
    }

    public func stop() {
        guard isRunning else { return }
        isRunning = false
        onDetected = nil
        log?("stop")
    }

    /// Simulates hearing "hey coco". Ignored while stopped, like a real engine.
    public func detect() {
        guard isRunning else { return }
        onDetected?()
    }
}

/// The non-committed pieces Porcupine needs: the Picovoice AccessKey and the trained keyword
/// file. Both are bundled from the gitignored `display/WakeWord/` folder (see
/// `docs/wake-word.md`).
public struct WakeWordResources: Sendable, Equatable {
    public static let accessKeyFile = "PicovoiceAccessKey.txt"
    public static let iOSKeywordFile = "hey-coco_ios.ppn"
    public static let macKeywordFile = "hey-coco_mac.ppn"
    public static let noKeyReason = "Wake word off: no Picovoice AccessKey in this build."
    public static let noKeywordReason = "Wake word off: no \"hey coco\" keyword file in this build."

    public enum Lookup: Sendable, Equatable {
        case available(WakeWordResources)
        case unavailable(String)
    }

    public var accessKey: String
    public var keywordURL: URL

    public init(accessKey: String, keywordURL: URL) {
        self.accessKey = accessKey
        self.keywordURL = keywordURL
    }

    /// Looks in `directory` (the app bundle's resources). On a Mac ("Designed for iPad") the
    /// macOS keyword is preferred, falling back to the iOS one.
    public static func locate(in directory: URL?, runningOnMac: Bool) -> Lookup {
        guard let directory,
            let raw = try? String(contentsOf: directory.appending(path: accessKeyFile), encoding: .utf8)
        else { return .unavailable(noKeyReason) }
        let key = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return .unavailable(noKeyReason) }

        let names = runningOnMac ? [macKeywordFile, iOSKeywordFile] : [iOSKeywordFile]
        let keyword = names.map { directory.appending(path: $0) }
            .first { FileManager.default.fileExists(atPath: $0.path(percentEncoded: false)) }
        guard let keyword else { return .unavailable(noKeywordReason) }
        return .available(WakeWordResources(accessKey: key, keywordURL: keyword))
    }
}
