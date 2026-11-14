import Foundation
import Synchronization

/// Where `TimerStore` keeps its last known state between launches.
public protocol TimerPersistence: Sendable {
    /// `nil` when nothing was saved or the file is unreadable.
    func load() -> TimerSnapshot?
    func save(_ snapshot: TimerSnapshot) throws
}

/// JSON in Application Support. Written atomically so a crash mid-write keeps the old file.
public struct FileTimerPersistence: TimerPersistence {
    public let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// `Application Support/CocoDisplay/timers.json`.
    public static func standard() -> FileTimerPersistence {
        let base =
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return FileTimerPersistence(
            url: base.appendingPathComponent("CocoDisplay", isDirectory: true)
                .appendingPathComponent("timers.json"))
    }

    public func load() -> TimerSnapshot? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? Self.decoder.decode(TimerSnapshot.self, from: data)
    }

    public func save(_ snapshot: TimerSnapshot) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        // Sub-second precision: local end times are not whole seconds.
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(snapshot).write(to: url, options: .atomic)
    }
}

extension FileTimerPersistence {
    // `load` must match `save`'s date strategy.
    fileprivate static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return decoder
    }
}

/// For tests and previews.
public final class InMemoryTimerPersistence: TimerPersistence {
    private let stored = Mutex<TimerSnapshot?>(nil)

    public init() {}

    public func load() -> TimerSnapshot? { stored.withLock { $0 } }
    public func save(_ snapshot: TimerSnapshot) throws { stored.withLock { $0 = snapshot } }
}
