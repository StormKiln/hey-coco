import Foundation

/// What the Hub wants on screen (`Action.view`). Named `ViewSpec` to avoid clashing with SwiftUI.
/// Unknown `type` values decode to `.unknown` so an older Display survives a newer Hub.
public enum ViewSpec: Codable, Sendable, Equatable {
    case idle
    case answer(text: String)
    case timers
    case weather(WeatherPayload)
    case picture(PicturePayload)
    case sound(SoundPayload)
    case youtube(videoID: String, title: String)
    case nowPlaying
    case unknown(String)

    public struct WeatherPayload: Codable, Sendable, Equatable {
        public var current: WeatherNow
        public var hourly: [WeatherHour]
        public var daily: [WeatherDay]
        /// A place the person named ("Portland, Oregon"); nil for home (#93).
        public var place: String?
    }

    public struct PicturePayload: Codable, Sendable, Equatable {
        public var query: String
        public var images: [PictureImage]
    }

    public struct SoundPayload: Codable, Sendable, Equatable {
        public var name: String
        public var clipURL: String
        public var imageURL: String
        public var attribution: String

        enum CodingKeys: String, CodingKey {
            case name, attribution
            case clipURL = "clip_url"
            case imageURL = "image_url"
        }
    }

    enum CodingKeys: String, CodingKey {
        case type, text, title
        case videoID = "video_id"
    }

    public var type: String {
        switch self {
        case .idle: "idle"
        case .answer: "answer"
        case .timers: "timers"
        case .weather: "weather"
        case .picture: "picture"
        case .sound: "sound"
        case .youtube: "youtube"
        case .nowPlaying: "now_playing"
        case .unknown(let type): type
        }
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "idle": self = .idle
        case "answer": self = .answer(text: try container.decode(String.self, forKey: .text))
        case "timers": self = .timers
        case "weather": self = .weather(try WeatherPayload(from: decoder))
        case "picture": self = .picture(try PicturePayload(from: decoder))
        case "sound": self = .sound(try SoundPayload(from: decoder))
        case "youtube":
            self = .youtube(
                videoID: try container.decode(String.self, forKey: .videoID),
                title: try container.decode(String.self, forKey: .title))
        case "now_playing": self = .nowPlaying
        default: self = .unknown(type)
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        switch self {
        case .idle, .timers, .nowPlaying, .unknown: break
        case .answer(let text): try container.encode(text, forKey: .text)
        case .weather(let payload): try payload.encode(to: encoder)
        case .picture(let payload): try payload.encode(to: encoder)
        case .sound(let payload): try payload.encode(to: encoder)
        case .youtube(let videoID, let title):
            try container.encode(videoID, forKey: .videoID)
            try container.encode(title, forKey: .title)
        }
    }
}

/// A picture result (`Image` in the protocol). Named to avoid clashing with SwiftUI's `Image`.
public struct PictureImage: Codable, Sendable, Equatable {
    public var url: String
    public var thumbURL: String
    public var title: String
    public var width: Int
    public var height: Int
    public var license: String
    public var author: String?
    public var sourceURL: String

    enum CodingKeys: String, CodingKey {
        case url, title, width, height, license, author
        case thumbURL = "thumb_url"
        case sourceURL = "source_url"
    }
}
