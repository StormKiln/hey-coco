import Foundation

// `/v1/settings/location`: the home weather location, set from Settings (#96).

/// Where home is, for weather. `label` is how to show it ("Portland, Oregon").
public struct HomeLocation: Codable, Sendable, Equatable, Hashable {
    public var name: String
    public var label: String
    public var lat: Double
    public var lon: Double
    public var timezone: String
    public var country: String?
    public var countryCode: String?
    /// State or province.
    public var admin1: String?

    public init(
        name: String, label: String, lat: Double, lon: Double, timezone: String,
        country: String?, countryCode: String?, admin1: String?
    ) {
        self.name = name
        self.label = label
        self.lat = lat
        self.lon = lon
        self.timezone = timezone
        self.country = country
        self.countryCode = countryCode
        self.admin1 = admin1
    }

    enum CodingKeys: String, CodingKey {
        case name, label, lat, lon, timezone, country, admin1
        case countryCode = "country_code"
    }

    // Every key is always present, so `nil` is sent as an explicit `null`.
    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(label, forKey: .label)
        try container.encode(lat, forKey: .lat)
        try container.encode(lon, forKey: .lon)
        try container.encode(timezone, forKey: .timezone)
        try container.encode(country, forKey: .country)
        try container.encode(countryCode, forKey: .countryCode)
        try container.encode(admin1, forKey: .admin1)
    }
}

/// The home location the Hub uses, and where it came from.
public struct LocationSettings: Codable, Sendable, Equatable {
    public enum Source: String, Codable, Sendable, Equatable {
        /// The Hub's config file (`[location]`): "the Hub's default".
        case config
        /// Set from the Display.
        case override
    }

    public var source: Source
    /// `nil` when the config file has no location and none was set.
    public var location: HomeLocation?
    /// After a `PUT` by name: the name fitted more than one sizeable place.
    public var ambiguous: Bool
    /// After a `PUT` by name: other matches, most populous first. Otherwise empty.
    public var alternatives: [HomeLocation]

    public init(
        source: Source, location: HomeLocation?, ambiguous: Bool, alternatives: [HomeLocation]
    ) {
        self.source = source
        self.location = location
        self.ambiguous = ambiguous
        self.alternatives = alternatives
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(source, forKey: .source)
        try container.encode(location, forKey: .location)
        try container.encode(ambiguous, forKey: .ambiguous)
        try container.encode(alternatives, forKey: .alternatives)
    }

    enum CodingKeys: String, CodingKey {
        case source, location, ambiguous, alternatives
    }
}

/// `PUT /v1/settings/location` by name: the Hub looks it up and saves the best match.
public struct LocationQueryRequest: Codable, Sendable, Equatable {
    public var query: String

    public init(query: String) {
        self.query = query
    }
}

/// `PUT /v1/settings/location` with a place the person picked from the alternatives.
public struct LocationPlaceRequest: Codable, Sendable, Equatable {
    public var name: String
    public var lat: Double
    public var lon: Double
    public var timezone: String
    public var country: String?
    public var countryCode: String?
    public var admin1: String?

    public init(_ place: HomeLocation) {
        name = place.name
        lat = place.lat
        lon = place.lon
        timezone = place.timezone
        country = place.country
        countryCode = place.countryCode
        admin1 = place.admin1
    }

    enum CodingKeys: String, CodingKey {
        case name, lat, lon, timezone, country, admin1
        case countryCode = "country_code"
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(lat, forKey: .lat)
        try container.encode(lon, forKey: .lon)
        try container.encode(timezone, forKey: .timezone)
        try container.encode(country, forKey: .country)
        try container.encode(countryCode, forKey: .countryCode)
        try container.encode(admin1, forKey: .admin1)
    }
}
