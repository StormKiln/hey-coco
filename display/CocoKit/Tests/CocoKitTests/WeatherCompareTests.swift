import Foundation
import Testing
@testable import CocoKit

private func day(_ date: String, high: Double = 5, code: Int = 3, precip: Int = 0) -> WeatherDay {
    WeatherDay(date: date, high: high, low: -1, unit: .celsius, code: code, precipProbability: precip)
}

/// #111: a named place's weather beside home's.
@Suite struct WeatherCompareTests {
    @Test func daysPairByDateAndKeepThePlacesOrder() {
        let place = [day("2026-11-16"), day("2026-11-17"), day("2026-11-18")]
        let home = [day("2026-11-17", high: 1), day("2026-11-16", high: 2)]
        let pairs = WeatherCompare.pairs(place: place, home: home)
        #expect(pairs.map(\.place.date) == ["2026-11-16", "2026-11-17", "2026-11-18"])
        #expect(pairs.map { $0.home?.high } == [2, 1, nil])
    }

    @Test func differenceIsThePlaceMinusHomeAsShown() {
        #expect(WeatherCompare.difference(place: 10.4, home: 4.1) == "+6°")
        #expect(WeatherCompare.difference(place: -3.2, home: 2.0) == "\u{2212}5°")
        // 10.7 and 11.3 both show as 11°.
        #expect(WeatherCompare.difference(place: 10.7, home: 11.3) == "Same")
    }

    @Test func precipCueOnlyWhenOneSideIsWet() {
        let rain = day("d", code: 61, precip: 70)
        let snow = day("d", code: 73, precip: 60)
        let dry = day("d", code: 3, precip: 10)
        #expect(WeatherCompare.precipCue(place: rain, home: dry) == "Rain there, dry here")
        #expect(WeatherCompare.precipCue(place: dry, home: snow) == "Dry there, snow here")
        #expect(WeatherCompare.precipCue(place: rain, home: snow) == nil)
        #expect(WeatherCompare.precipCue(place: dry, home: dry) == nil)
        // A likely chance with a dry-looking code still counts as wet.
        #expect(WeatherCompare.precipCue(place: day("d", code: 3, precip: 55), home: dry)
            == "Rain there, dry here")
    }

    @Test func precipCueForNowUsesTheCodes() {
        #expect(WeatherCompare.precipCue(placeCode: 63, homeCode: 0) == "Rain there, dry here")
        #expect(WeatherCompare.precipCue(placeCode: 2, homeCode: 71) == "Dry there, snow here")
        #expect(WeatherCompare.precipCue(placeCode: 2, homeCode: 3) == nil)
    }

    @Test func shortNameDropsTheRegion() {
        #expect(WeatherCompare.shortName("Portland, Oregon") == "Portland")
        #expect(WeatherCompare.shortName("Paris") == "Paris")
    }
}
