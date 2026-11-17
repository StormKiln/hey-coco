import Foundation

/// The Weather screen's place-beside-home layout (#111): which rows line up, and the cues
/// between them. "There" is the named place, "here" is home (where the iPad is).
public enum WeatherCompare {
    /// Day rows in the comparison layout, by orientation (fewer than the single-place list:
    /// each row is wider and taller).
    public static let landscapeDayCount = 5
    public static let portraitDayCount = 6
    /// A chance of precipitation this likely makes a day wet (the Hub's "likely").
    public static let wetPercent = 50

    public struct DayPair: Sendable, Equatable {
        public let place: WeatherDay
        public let home: WeatherDay?
    }

    /// The place's days, each with home's day of the same date (nil past home's forecast). The
    /// Hub already lined the dates up on the place's calendar; this only matches them.
    public static func pairs(place: [WeatherDay], home: [WeatherDay]) -> [DayPair] {
        let byDate = Dictionary(home.map { ($0.date, $0) }, uniquingKeysWith: { first, _ in first })
        return place.map { DayPair(place: $0, home: byDate[$0.date]) }
    }

    /// The place minus home, on the rounded temperatures the screen shows: "+6°", "−5°", "Same".
    public static func difference(place: Double, home: Double) -> String {
        let diff = Int(place.rounded()) - Int(home.rounded())
        if diff == 0 { return "Same" }
        return diff > 0 ? "+\(diff)°" : "\u{2212}\(-diff)°"
    }

    /// "Rain there, dry here" / "Dry there, snow here" for a day, or nil unless exactly one side
    /// is wet.
    public static func precipCue(place: WeatherDay, home: WeatherDay) -> String? {
        cue(place: dayKind(place), home: dayKind(home))
    }

    /// The same for current conditions, which carry only a weather code.
    public static func precipCue(placeCode: Int, homeCode: Int) -> String? {
        cue(place: precipKind(code: placeCode), home: precipKind(code: homeCode))
    }

    /// "Portland" from "Portland, Oregon": short enough for a column header.
    public static func shortName(_ place: String) -> String {
        place.split(separator: ",").first.map { $0.trimmingCharacters(in: .whitespaces) } ?? place
    }

    /// "rain" or "snow" for a WMO precipitation code; nil for dry codes.
    static func precipKind(code: Int) -> String? {
        switch code {
        case 71, 73, 75, 77, 85, 86: "snow"
        case 51...67, 80...82, 95...99: "rain"
        default: nil
        }
    }

    private static func dayKind(_ day: WeatherDay) -> String? {
        guard day.precipProbability >= wetPercent else { return nil }
        return precipKind(code: day.code) ?? "rain"
    }

    private static func cue(place: String?, home: String?) -> String? {
        switch (place, home) {
        case (let kind?, nil): "\(kind.capitalized) there, dry here"
        case (nil, let kind?): "Dry there, \(kind) here"
        default: nil
        }
    }
}
