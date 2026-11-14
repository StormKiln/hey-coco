import Foundation

/// A message on the `WS /v1/events` stream. Every event carries `server_time`; the rest
/// depends on `type`. Unknown types decode to `.unknown` so an older Display survives a newer Hub.
public struct Event: Codable, Sendable, Equatable {
    public enum Kind: Sendable, Equatable {
        /// Sent on connect.
        case hello(timers: [TimerInfo], nowPlaying: NowPlaying?)
        case timerCreated(TimerInfo)
        case timerUpdated(TimerInfo)
        case timerFired(TimerInfo)
        case timerDismissed(TimerInfo)
        case timerCancelled(TimerInfo)
        case nowPlaying(NowPlaying?)
        case settingsChanged(night: Night)
        case unknown(String)

        public var type: String {
            switch self {
            case .hello: "hello"
            case .timerCreated: "timer.created"
            case .timerUpdated: "timer.updated"
            case .timerFired: "timer.fired"
            case .timerDismissed: "timer.dismissed"
            case .timerCancelled: "timer.cancelled"
            case .nowPlaying: "now_playing"
            case .settingsChanged: "settings.changed"
            case .unknown(let type): type
            }
        }
    }

    public var serverTime: Date
    public var kind: Kind

    public init(serverTime: Date, kind: Kind) {
        self.serverTime = serverTime
        self.kind = kind
    }

    enum CodingKeys: String, CodingKey {
        case type, timers, timer, night
        case serverTime = "server_time"
        case nowPlaying = "now_playing"
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        serverTime = try container.decode(Date.self, forKey: .serverTime)
        let type = try container.decode(String.self, forKey: .type)
        func timer() throws -> TimerInfo { try container.decode(TimerInfo.self, forKey: .timer) }
        func nowPlaying() throws -> NowPlaying? {
            try container.decodeIfPresent(NowPlaying.self, forKey: .nowPlaying)
        }
        switch type {
        case "hello":
            kind = .hello(
                timers: try container.decode([TimerInfo].self, forKey: .timers),
                nowPlaying: try nowPlaying())
        case "timer.created": kind = .timerCreated(try timer())
        case "timer.updated": kind = .timerUpdated(try timer())
        case "timer.fired": kind = .timerFired(try timer())
        case "timer.dismissed": kind = .timerDismissed(try timer())
        case "timer.cancelled": kind = .timerCancelled(try timer())
        case "now_playing": kind = .nowPlaying(try nowPlaying())
        case "settings.changed":
            kind = .settingsChanged(night: try container.decode(Night.self, forKey: .night))
        default: kind = .unknown(type)
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(kind.type, forKey: .type)
        try container.encode(serverTime, forKey: .serverTime)
        switch kind {
        case .hello(let timers, let nowPlaying):
            try container.encode(timers, forKey: .timers)
            try container.encode(nowPlaying, forKey: .nowPlaying)
        case .timerCreated(let timer), .timerUpdated(let timer), .timerFired(let timer),
            .timerDismissed(let timer), .timerCancelled(let timer):
            try container.encode(timer, forKey: .timer)
        case .nowPlaying(let nowPlaying): try container.encode(nowPlaying, forKey: .nowPlaying)
        case .settingsChanged(let night): try container.encode(night, forKey: .night)
        case .unknown: break
        }
    }
}
