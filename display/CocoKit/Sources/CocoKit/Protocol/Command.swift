import Foundation

/// Something only the Display can do (`Action.commands[]`).
/// Unknown `type` values decode to `.unknown` so an older Display survives a newer Hub.
public enum Command: Codable, Sendable, Equatable {
    case alarmStop
    case mediaPause(target: MediaTarget)
    case mediaResume(target: MediaTarget)
    case mediaNext(target: MediaTarget)
    case mediaStop(target: MediaTarget)
    /// 0–100.
    case volumeSet(level: Int)
    /// −100–100.
    case volumeChange(delta: Int)
    case screenWake
    case screenSleep
    /// Apple Music (#42): search the catalog and play the best match; `query` "" resumes.
    case musicPlay(query: String, kind: MusicSearchKind)
    case unknown(String)

    enum CodingKeys: String, CodingKey {
        case type, target, level, delta, query, kind
    }

    public var type: String {
        switch self {
        case .alarmStop: "alarm.stop"
        case .mediaPause: "media.pause"
        case .mediaResume: "media.resume"
        case .mediaNext: "media.next"
        case .mediaStop: "media.stop"
        case .volumeSet: "volume.set"
        case .volumeChange: "volume.change"
        case .screenWake: "screen.wake"
        case .screenSleep: "screen.sleep"
        case .musicPlay: "music.play"
        case .unknown(let type): type
        }
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        func target() throws -> MediaTarget {
            try container.decode(MediaTarget.self, forKey: .target)
        }
        switch type {
        case "alarm.stop": self = .alarmStop
        case "media.pause": self = .mediaPause(target: try target())
        case "media.resume": self = .mediaResume(target: try target())
        case "media.next": self = .mediaNext(target: try target())
        case "media.stop": self = .mediaStop(target: try target())
        case "volume.set": self = .volumeSet(level: try container.decode(Int.self, forKey: .level))
        case "volume.change":
            self = .volumeChange(delta: try container.decode(Int.self, forKey: .delta))
        case "screen.wake": self = .screenWake
        case "screen.sleep": self = .screenSleep
        case "music.play":
            self = .musicPlay(
                query: try container.decode(String.self, forKey: .query),
                kind: try container.decode(MusicSearchKind.self, forKey: .kind))
        default: self = .unknown(type)
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        switch self {
        case .mediaPause(let target), .mediaResume(let target), .mediaNext(let target),
            .mediaStop(let target):
            try container.encode(target, forKey: .target)
        case .volumeSet(let level): try container.encode(level, forKey: .level)
        case .volumeChange(let delta): try container.encode(delta, forKey: .delta)
        case .musicPlay(let query, let kind):
            try container.encode(query, forKey: .query)
            try container.encode(kind, forKey: .kind)
        case .alarmStop, .screenWake, .screenSleep, .unknown: break
        }
    }
}

/// What `music.play` searches for. An unknown kind from a newer Hub searches everything.
public enum MusicSearchKind: String, Codable, Sendable, Equatable, CaseIterable {
    case artist, album, playlist, song, any

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = MusicSearchKind(rawValue: raw) ?? .any
    }
}
