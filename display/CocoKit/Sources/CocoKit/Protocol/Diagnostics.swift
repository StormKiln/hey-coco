import Foundation

// `GET /v1/diagnostics` (docs/protocol/README.md#diagnostics): what the Hub heard and did,
// and whether its services are up. Read-only; the Display never sends these.

/// Health of one Hub service. Unknown values decode as `.unknown` so an older Display
/// survives a newer Hub.
public enum CheckStatus: String, Codable, Sendable, Equatable {
    case ok, warn, error, off, unknown

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = CheckStatus(rawValue: raw) ?? .unknown
    }
}

public struct UtteranceTrace: Codable, Sendable, Equatable {
    public var ts: Date
    public var text: String
    public var source: UtteranceRequest.Source
    /// The request's `context.view`.
    public var view: ViewType
    /// `nil` when handling failed (see `error`).
    public var intent: String?
    public var matchedBy: Action.MatchedBy?
    public var say: String?
    public var latencyMs: Int
    public var error: String?

    enum CodingKeys: String, CodingKey {
        case ts, text, source, view, intent, say, error
        case matchedBy = "matched_by"
        case latencyMs = "latency_ms"
    }
}

public struct DiagnosticsSettings: Codable, Sendable, Equatable {
    public struct Qwen: Codable, Sendable, Equatable {
        public var enabled: Bool
        public var model: String
    }

    public struct Spotify: Codable, Sendable, Equatable {
        public var configured: Bool
        public var loggedIn: Bool

        enum CodingKeys: String, CodingKey {
            case configured
            case loggedIn = "logged_in"
        }
    }

    public var timezone: String
    public var units: String
    public var locationSet: Bool
    public var qwen: Qwen
    public var youtubeConfigured: Bool
    public var spotify: Spotify
    public var night: Night
    public var logTranscripts: Bool

    enum CodingKeys: String, CodingKey {
        case timezone, units, qwen, spotify, night
        case locationSet = "location_set"
        case youtubeConfigured = "youtube_configured"
        case logTranscripts = "log_transcripts"
    }
}

public struct OllamaHealth: Codable, Sendable, Equatable {
    public var status: CheckStatus
    public var reachable: Bool
    public var modelPresent: Bool
    public var modelLoaded: Bool
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case status, reachable, detail
        case modelPresent = "model_present"
        case modelLoaded = "model_loaded"
    }
}

public struct WeatherHealth: Codable, Sendable, Equatable {
    public var status: CheckStatus
    public var fetchedAt: Date?
    public var stale: Bool
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case status, stale, detail
        case fetchedAt = "fetched_at"
    }
}

public struct YoutubeHealth: Codable, Sendable, Equatable {
    public var status: CheckStatus
    public var quotaUsed: Int
    public var quotaLimit: Int
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case status, detail
        case quotaUsed = "quota_used"
        case quotaLimit = "quota_limit"
    }
}

public struct SpotifyHealth: Codable, Sendable, Equatable {
    public var status: CheckStatus
    public var deviceVisible: Bool
    public var deviceName: String?
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case status, detail
        case deviceVisible = "device_visible"
        case deviceName = "device_name"
    }
}

/// One configured calendar, by position only: never its name or URL (#139).
public struct CalendarHealth: Codable, Sendable, Equatable {
    public var index: Int
    public var status: CheckStatus
    public var fetchedAt: Date?
    public var stale: Bool
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case index, status, stale, detail
        case fetchedAt = "fetched_at"
    }
}

public struct DiagnosticsHealth: Codable, Sendable, Equatable {
    public var ollama: OllamaHealth
    public var weather: WeatherHealth
    public var youtube: YoutubeHealth
    public var spotify: SpotifyHealth
    /// Empty when no calendars are configured (and from Hubs older than #139).
    public var calendars: [CalendarHealth]

    enum CodingKeys: String, CodingKey {
        case ollama, weather, youtube, spotify, calendars
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ollama = try c.decode(OllamaHealth.self, forKey: .ollama)
        weather = try c.decode(WeatherHealth.self, forKey: .weather)
        youtube = try c.decode(YoutubeHealth.self, forKey: .youtube)
        spotify = try c.decode(SpotifyHealth.self, forKey: .spotify)
        calendars = try c.decodeIfPresent([CalendarHealth].self, forKey: .calendars) ?? []
    }
}

public struct HubDiagnostics: Codable, Sendable, Equatable {
    public var serverTime: Date
    public var version: String
    public var gitSha: String?
    public var startedAt: Date
    public var uptimeS: Int
    public var settings: DiagnosticsSettings
    public var health: DiagnosticsHealth
    /// Most recent first, up to 20.
    public var utterances: [UtteranceTrace]

    enum CodingKeys: String, CodingKey {
        case version, settings, health, utterances
        case serverTime = "server_time"
        case gitSha = "git_sha"
        case startedAt = "started_at"
        case uptimeS = "uptime_s"
    }
}
