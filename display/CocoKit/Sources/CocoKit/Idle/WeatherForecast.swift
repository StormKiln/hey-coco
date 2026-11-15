import Foundation

/// What the Weather screen (`ViewSpec.weather`) shows: which hours and days, their labels, and
/// when a chance of precipitation is worth printing. Dates follow the Hub's timezone.
public enum WeatherForecast {
    /// The Weather screen returns to idle after this long without a touch.
    public static let idleTimeout: Duration = .seconds(30)
    /// Hours in the strip ("next 12 hours").
    public static let hourCount = 12
    /// Day rows.
    public static let dayCount = 7
    /// A chance of rain or snow below this is noise, so no percentage is shown.
    public static let precipThreshold = 20

    /// "40%" when `probability` is at least `precipThreshold`, else `nil`.
    public static func precipText(_ probability: Int) -> String? {
        probability >= precipThreshold ? "\(min(probability, 100))%" : nil
    }

    /// The next `count` forecast hours, starting with the hour in progress, soonest first.
    public static func upcomingHours(
        _ hourly: [WeatherHour], count: Int = hourCount, at now: Date
    ) -> [WeatherHour] {
        let cutoff = now.addingTimeInterval(-3600)
        return Array(hourly.filter { $0.time > cutoff }.sorted { $0.time < $1.time }.prefix(count))
    }

    /// Today and the following days (Hub-local dates), at most `count`. Past days are dropped
    /// (a view kept on screen past midnight); undated or malformed entries are skipped.
    public static func upcomingDays(
        _ daily: [WeatherDay], count: Int = dayCount, at now: Date, timeZone: TimeZone
    ) -> [WeatherDay] {
        let today = dayString(now, timeZone: timeZone)
        return Array(
            daily.filter { date(of: $0, timeZone: timeZone) != nil && $0.date >= today }
                .sorted { $0.date < $1.date }
                .prefix(count))
    }

    /// "Today", "Tomorrow", then the weekday ("Wednesday"), by the Hub's calendar. A day that
    /// can't be parsed shows its raw `YYYY-MM-DD`.
    public static func dayLabel(
        _ day: WeatherDay, at now: Date, timeZone: TimeZone, locale: Locale = .current
    ) -> String {
        guard let date = date(of: day, timeZone: timeZone) else { return day.date }
        let calendar = calendar(timeZone)
        let offset = calendar.dateComponents(
            [.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: date)
        ).day
        switch offset {
        case 0: return "Today"
        case 1: return "Tomorrow"
        default:
            var style = Date.FormatStyle(locale: locale, timeZone: timeZone)
            style = style.weekday(.wide)
            return date.formatted(style)
        }
    }

    /// Noon on `day` in `timeZone` (safe from DST edges), or `nil` for a malformed date.
    static func date(of day: WeatherDay, timeZone: TimeZone) -> Date? {
        let parts = day.date.split(separator: "-")
        guard parts.count == 3, let year = Int(parts[0]), let month = Int(parts[1]),
            let dayOfMonth = Int(parts[2])
        else { return nil }
        let components = DateComponents(year: year, month: month, day: dayOfMonth, hour: 12)
        let calendar = calendar(timeZone)
        guard components.isValidDate(in: calendar) else { return nil }
        return calendar.date(from: components)
    }

    static func dayString(_ date: Date, timeZone: TimeZone) -> String {
        let parts = calendar(timeZone).dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    private static func calendar(_ timeZone: TimeZone) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }
}

/// The colour role of one layer of a weather symbol, drawn with palette rendering. Multicolor
/// draws clouds and moons white, which vanish on the light (day) theme; the app maps these roles
/// to colours that read on both themes.
public enum WeatherTint: Sendable, Equatable {
    case cloud, sun, moon, rain, snow, fog
}

extension WeatherFormat {
    /// Palette roles for `symbolName(code:isDay:)`'s symbols, in SF Symbols layer order
    /// (primary, secondary, tertiary).
    public static func tints(forSymbol name: String) -> [WeatherTint] {
        switch name {
        case "sun.max.fill": [.sun]
        case "moon.fill", "moon.stars.fill": [.moon, .moon]
        case "cloud.sun.fill": [.cloud, .sun]
        case "cloud.moon.fill": [.cloud, .moon]
        case "cloud.fog.fill": [.cloud, .fog]
        case "cloud.snow.fill": [.cloud, .snow]
        case "cloud.drizzle.fill", "cloud.rain.fill", "cloud.heavyrain.fill", "cloud.sleet.fill",
            "cloud.bolt.rain.fill":
            [.cloud, .rain]
        case "cloud.sun.rain.fill": [.cloud, .sun, .rain]
        case "cloud.moon.rain.fill": [.cloud, .moon, .rain]
        default: [.cloud]
        }
    }
}
