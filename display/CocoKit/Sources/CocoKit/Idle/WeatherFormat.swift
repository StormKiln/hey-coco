import Foundation

/// Formatting for weather on the idle screen.
public enum WeatherFormat {
    /// Rounded, with a degree sign and no unit: "5°". Never "-0°".
    public static func temperature(_ value: Double) -> String {
        let rounded = Int(value.rounded())
        return "\(rounded == 0 ? 0 : rounded)°"
    }

    /// "3 PM" in en_US; follows `locale` otherwise.
    public static func hourLabel(
        _ date: Date, timeZone: TimeZone = .current, locale: Locale = .current
    ) -> String {
        var style = Date.FormatStyle(locale: locale, timeZone: timeZone)
        style = style.hour(.defaultDigits(amPM: .abbreviated))
        // ICU puts a narrow no-break space before AM/PM; plain spaces render the same and test simply.
        return date.formatted(style)
            .replacingOccurrences(of: "\u{202F}", with: " ")
            .replacingOccurrences(of: "\u{00A0}", with: " ")
    }

    /// Hourly forecasts carry no `is_day`; 7:00–18:59 local counts as day. Good enough for an
    /// icon (sun vs moon); the current conditions use the Hub's real `is_day`.
    public static func isDaytime(_ date: Date, timeZone: TimeZone = .current) -> Bool {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return (7..<19).contains(calendar.component(.hour, from: date))
    }

    /// SF Symbol for a WMO weather code (Open-Meteo's `weather_code`).
    public static func symbolName(code: Int, isDay: Bool) -> String {
        switch code {
        case 0: isDay ? "sun.max.fill" : "moon.stars.fill"
        case 1: isDay ? "sun.max.fill" : "moon.fill"
        case 2: isDay ? "cloud.sun.fill" : "cloud.moon.fill"
        case 3: "cloud.fill"
        case 45, 48: "cloud.fog.fill"
        case 51, 53, 55: "cloud.drizzle.fill"
        case 56, 57, 66, 67: "cloud.sleet.fill"
        case 61, 63: "cloud.rain.fill"
        case 65: "cloud.heavyrain.fill"
        case 71, 73, 75, 77, 85, 86: "cloud.snow.fill"
        case 80, 81: isDay ? "cloud.sun.rain.fill" : "cloud.moon.rain.fill"
        case 82: "cloud.heavyrain.fill"
        case 95, 96, 99: "cloud.bolt.rain.fill"
        default: "thermometer.medium"
        }
    }
}
