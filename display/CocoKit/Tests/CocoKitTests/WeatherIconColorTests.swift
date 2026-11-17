import Foundation
import Testing
@testable import CocoKit

/// Weather icons drawn in Coco's theme (#113 follow-up): every layer reads on the screen's
/// surfaces by day and by night, rain is the same blue as the chance of rain, and nothing is the
/// old system indigo (a purple moon on a terracotta theme).
@Suite struct WeatherIconColorTests {
    static let tints: [WeatherTint] = [.cloud, .sun, .moon, .rain, .snow, .fog]
    static let schemes = [MaterialScheme(dark: false), MaterialScheme(dark: true)]

    @Test(arguments: tints)
    func readsOnTheScreensSurfaces(_ tint: WeatherTint) {
        for scheme in Self.schemes {
            let color = scheme.weather(tint)
            for surface in [scheme.surface, scheme.surfaceContainerLow, scheme.surfaceContainer] {
                let ratio = RGBColor.contrastRatio(color, surface)
                // WCAG's 3:1 for meaningful graphics.
                #expect(ratio >= 3, "\(tint) on \(scheme.isDark ? "dark" : "light"): \(ratio)")
            }
        }
    }

    @Test func rainIsTheChanceOfRainBlue() {
        for scheme in Self.schemes {
            #expect(scheme.weather(.rain) == scheme.info)
        }
    }

    @Test func sunAndMoonAreWarmNotPurple() {
        for scheme in Self.schemes {
            for tint in [WeatherTint.sun, .moon] {
                let hue = scheme.weather(tint).lch.h
                // Orange to yellow, nowhere near indigo/violet (~270–320°).
                #expect((50...105).contains(hue), "\(tint) hue \(hue)")
            }
            #expect(scheme.weather(.sun).lch.c > scheme.weather(.moon).lch.c)
        }
    }

    @Test func cloudsAndFogAreQuietNeutrals() {
        for scheme in Self.schemes {
            #expect(scheme.weather(.cloud).lch.c < 15)
            #expect(scheme.weather(.fog).lch.c < 15)
            #expect(scheme.weather(.cloud) != scheme.weather(.fog))
        }
    }

    @Test func snowIsAPaleBlueApartFromRain() {
        for scheme in Self.schemes {
            let snow = scheme.weather(.snow).lch, rain = scheme.weather(.rain).lch
            #expect(abs(snow.h - rain.h) < 30)
            #expect(snow.c < rain.c)
        }
    }

    @Test func eachTintDiffersBetweenDayAndNight() {
        for tint in Self.tints {
            #expect(Self.schemes[0].weather(tint) != Self.schemes[1].weather(tint), "\(tint)")
        }
    }
}
