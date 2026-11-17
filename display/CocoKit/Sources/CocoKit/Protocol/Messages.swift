import Foundation

// Plain protocol messages. Tagged unions (views, commands, events) live in their own files.

public enum MediaTarget: String, Codable, Sendable, Equatable {
    case youtube, music, sound
}

public enum ViewType: String, Codable, Sendable, Equatable {
    case idle, answer, timers, weather, picture, sound, youtube, calendar
    case nowPlaying = "now_playing"
}

// MARK: - Utterance

public struct UtteranceContext: Codable, Sendable, Equatable {
    public var view: ViewType
    public var alarmRinging: Bool
    public var mediaPlaying: MediaTarget?

    public init(view: ViewType, alarmRinging: Bool, mediaPlaying: MediaTarget?) {
        self.view = view
        self.alarmRinging = alarmRinging
        self.mediaPlaying = mediaPlaying
    }

    enum CodingKeys: String, CodingKey {
        case view
        case alarmRinging = "alarm_ringing"
        case mediaPlaying = "media_playing"
    }

    // The Hub requires every key, so `nil` is sent as an explicit `null`.
    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(view, forKey: .view)
        try container.encode(alarmRinging, forKey: .alarmRinging)
        try container.encode(mediaPlaying, forKey: .mediaPlaying)
    }
}

public struct UtteranceRequest: Codable, Sendable, Equatable {
    public enum Source: String, Codable, Sendable, Equatable {
        case voice, touch
    }

    public var text: String
    public var source: Source
    public var context: UtteranceContext

    public init(text: String, source: Source, context: UtteranceContext) {
        self.text = text
        self.source = source
        self.context = context
    }
}

// MARK: - Weather

public enum TemperatureUnit: String, Codable, Sendable, Equatable {
    case celsius = "C"
    case fahrenheit = "F"
}

public struct WeatherNow: Codable, Sendable, Equatable {
    public var time: Date
    public var temp: Double
    public var feelsLike: Double
    public var unit: TemperatureUnit
    /// WMO weather code.
    public var code: Int
    public var description: String
    public var isDay: Bool

    enum CodingKeys: String, CodingKey {
        case time, temp, unit, code, description
        case feelsLike = "feels_like"
        case isDay = "is_day"
    }
}

public struct WeatherHour: Codable, Sendable, Equatable {
    public var time: Date
    public var temp: Double
    public var unit: TemperatureUnit
    public var code: Int
    /// 0–100.
    public var precipProbability: Int

    enum CodingKeys: String, CodingKey {
        case time, temp, unit, code
        case precipProbability = "precip_probability"
    }
}

public struct WeatherDay: Codable, Sendable, Equatable {
    /// `YYYY-MM-DD` in the Hub's timezone.
    public var date: String
    public var high: Double
    public var low: Double
    public var unit: TemperatureUnit
    public var code: Int
    public var precipProbability: Int

    enum CodingKeys: String, CodingKey {
        case date, high, low, unit, code
        case precipProbability = "precip_probability"
    }
}

/// Home's weather beside a named place's (#111).
public struct WeatherComparison: Codable, Sendable, Equatable {
    /// "Home".
    public var name: String
    /// Home's conditions now.
    public var current: WeatherNow
    /// Home's days for the place's dates (the place's local calendar), as far as home's
    /// forecast reaches; match them by `date`.
    public var daily: [WeatherDay]
}

// MARK: - Idle

public struct Night: Codable, Sendable, Equatable {
    /// `HH:MM`, Hub-local.
    public var start: String
    /// `HH:MM`, Hub-local.
    public var end: String
    public var darkAfterIdleS: Int

    public init(start: String, end: String, darkAfterIdleS: Int) {
        self.start = start
        self.end = end
        self.darkAfterIdleS = darkAfterIdleS
    }

    enum CodingKeys: String, CodingKey {
        case start, end
        case darkAfterIdleS = "dark_after_idle_s"
    }
}

public struct IdleWeather: Codable, Sendable, Equatable {
    public var current: WeatherNow
    public var hourly: [WeatherHour]
    public var stale: Bool
}

public struct IdleInfo: Codable, Sendable, Equatable {
    public var serverTime: Date
    public var timezone: String
    /// `nil` until the Hub has fetched weather at least once.
    public var weather: IdleWeather?
    /// `nil` when no calendars are configured (or from a Hub older than #140).
    public var calendar: IdleCalendar? = nil
    public var night: Night

    enum CodingKeys: String, CodingKey {
        case timezone, weather, calendar, night
        case serverTime = "server_time"
    }
}

// MARK: - Timers

/// A Hub timer. Named `TimerInfo` to avoid clashing with `Foundation.Timer`.
public struct TimerInfo: Codable, Sendable, Equatable, Identifiable {
    public enum State: String, Codable, Sendable, Equatable {
        case running, ringing, dismissed, cancelled
    }

    public var id: String
    public var name: String?
    public var durationS: Int
    public var createdAt: Date
    public var endsAt: Date
    public var state: State

    enum CodingKeys: String, CodingKey {
        case id, name, state
        case durationS = "duration_s"
        case createdAt = "created_at"
        case endsAt = "ends_at"
    }
}

public struct TimersList: Codable, Sendable, Equatable {
    public var timers: [TimerInfo]
    public var serverTime: Date

    enum CodingKeys: String, CodingKey {
        case timers
        case serverTime = "server_time"
    }
}

/// `POST /v1/pair` (no bearer): the one-time code `make hub-pair` showed (#89).
public struct PairRequest: Codable, Sendable, Equatable {
    public var code: String

    public init(code: String) {
        self.code = code
    }
}

/// The shared LAN token, for the right pairing code.
public struct PairResponse: Codable, Sendable, Equatable {
    public var token: String

    public init(token: String) {
        self.token = token
    }
}

/// `POST /v1/timers/{id}/add`: time to add to a running (or ringing) Hub timer.
public struct TimerAddRequest: Codable, Sendable, Equatable {
    public var seconds: Int

    public init(seconds: Int) {
        self.seconds = seconds
    }
}

public struct TimerCreateRequest: Codable, Sendable, Equatable {
    public var name: String?
    public var durationS: Int

    public init(name: String?, durationS: Int) {
        self.name = name
        self.durationS = durationS
    }

    enum CodingKeys: String, CodingKey {
        case name
        case durationS = "duration_s"
    }

    // The Hub requires every key, so `nil` is sent as an explicit `null`.
    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(durationS, forKey: .durationS)
    }
}

// MARK: - Now playing

public struct NowPlaying: Codable, Sendable, Equatable {
    public enum Source: String, Codable, Sendable, Equatable {
        case spotify
        case appleMusic = "apple_music"
    }

    public var source: Source
    public var title: String
    public var artist: String
    public var album: String?
    public var artworkURL: String?
    public var isPlaying: Bool

    public init(
        source: Source, title: String, artist: String, album: String? = nil, artworkURL: String? = nil,
        isPlaying: Bool
    ) {
        self.source = source
        self.title = title
        self.artist = artist
        self.album = album
        self.artworkURL = artworkURL
        self.isPlaying = isPlaying
    }

    enum CodingKeys: String, CodingKey {
        case source, title, artist, album
        case artworkURL = "artwork_url"
        case isPlaying = "is_playing"
    }
}

// MARK: - Errors

public struct ErrorResponse: Codable, Sendable, Equatable {
    public struct Body: Codable, Sendable, Equatable {
        public var code: String
        public var message: String
    }

    public var error: Body
}
