import Foundation
import Testing
@testable import CocoKit

/// #113: a Material 3 style theme from one seed colour. Tone is CIE L* (as in M3's HCT), so a
/// tone pairing such as 40 on 100 has a known minimum contrast whatever the hue.
@Suite struct MaterialThemeTests {
    // MARK: - Colour maths

    @Test func hexRoundTrips() {
        for hex: UInt32 in [0x000000, 0xFFFFFF, 0xC0643A, 0x1E88E5, 0x7F7F7F] {
            #expect(RGBColor(hex: hex).hex == hex)
        }
    }

    @Test func contrastRatioMatchesWCAG() {
        let black = RGBColor(hex: 0x000000), white = RGBColor(hex: 0xFFFFFF)
        #expect(abs(RGBColor.contrastRatio(black, white) - 21) < 0.01)
        #expect(abs(RGBColor.contrastRatio(white, black) - 21) < 0.01)
        #expect(abs(RGBColor.contrastRatio(white, white) - 1) < 0.0001)
        // #777777 on white is the classic "just under AA" grey (4.48:1).
        #expect(abs(RGBColor.contrastRatio(RGBColor(hex: 0x777777), white) - 4.48) < 0.01)
    }

    @Test func lchRoundTripsThroughSRGB() {
        for hex: UInt32 in [0xC0643A, 0x1E88E5, 0x43A047, 0xB3261E, 0x7F7F7F] {
            let color = RGBColor(hex: hex)
            let back = RGBColor(lch: color.lch)
            #expect(back.hex == hex, "\(String(hex, radix: 16)) came back \(String(back.hex, radix: 16))")
        }
    }

    @Test func greysHaveNoChroma() {
        #expect(RGBColor(hex: 0x7F7F7F).lch.c < 0.01)
        #expect(abs(RGBColor(hex: 0xFFFFFF).lch.l - 100) < 0.01)
        #expect(RGBColor(hex: 0x000000).lch.l < 0.01)
    }

    // MARK: - Tonal palette

    @Test func toneIsLightness() {
        let palette = TonalPalette(hue: 45, chroma: 60)
        #expect(palette.tone(0).hex == 0x000000)
        #expect(palette.tone(100).hex == 0xFFFFFF)
        for tone in stride(from: 10.0, through: 90, by: 10) {
            #expect(abs(palette.tone(tone).lch.l - tone) < 0.6, "tone \(tone)")
        }
    }

    @Test func tonesGetLighterAndKeepTheirHue() {
        let palette = TonalPalette(hue: 45, chroma: 60)
        var previous = -1.0
        for tone in stride(from: 0.0, through: 100, by: 5) {
            let luminance = palette.tone(tone).relativeLuminance
            #expect(luminance > previous, "tone \(tone)")
            previous = luminance
        }
        for tone in [30.0, 40, 50, 60, 70] {
            let hue = palette.tone(tone).lch.h
            #expect(abs(hue - 45) < 4, "tone \(tone) has hue \(hue)")
        }
    }

    @Test func chromaIsReducedToStayInGamut() {
        // Far more chroma than sRGB has at tone 95: still a valid, very light colour.
        let palette = TonalPalette(hue: 270, chroma: 150)
        let light = palette.tone(95)
        #expect(abs(light.lch.l - 95) < 0.6)
        #expect(light.lch.c < 150)
    }

    @Test func paletteFromSeedKeepsTheSeedsHue() {
        let seed = RGBColor(hex: 0xC0643A)
        let palette = TonalPalette(seed: seed)
        #expect(abs(palette.hue - seed.lch.h) < 0.001)
        #expect(abs(palette.chroma - seed.lch.c) < 0.001)
    }

    // MARK: - Scheme

    private static let seeds: [UInt32] = [MaterialScheme.cocoSeed.hex, 0x1E88E5, 0x43A047, 0x8E24AA, 0xFDD835]

    private struct Pair {
        let name: String
        let foreground: KeyPath<MaterialScheme, RGBColor>
        let background: KeyPath<MaterialScheme, RGBColor>
        let minimum: Double
    }

    /// The pairs the app draws text with. 4.5:1 is WCAG AA for body text; onSurface gets AAA.
    private static var pairs: [Pair] { [
        Pair(name: "onPrimary", foreground: \.onPrimary, background: \.primary, minimum: 4.5),
        Pair(name: "onPrimaryContainer", foreground: \.onPrimaryContainer, background: \.primaryContainer, minimum: 4.5),
        Pair(name: "onSecondaryContainer", foreground: \.onSecondaryContainer, background: \.secondaryContainer, minimum: 4.5),
        Pair(name: "onTertiaryContainer", foreground: \.onTertiaryContainer, background: \.tertiaryContainer, minimum: 4.5),
        Pair(name: "onError", foreground: \.onError, background: \.error, minimum: 4.5),
        Pair(name: "onErrorContainer", foreground: \.onErrorContainer, background: \.errorContainer, minimum: 4.5),
        Pair(name: "onSurface", foreground: \.onSurface, background: \.surface, minimum: 7),
        Pair(name: "onSurface/high", foreground: \.onSurface, background: \.surfaceContainerHighest, minimum: 7),
        Pair(name: "onSurfaceVariant", foreground: \.onSurfaceVariant, background: \.surface, minimum: 4.5),
        Pair(name: "onSurfaceVariant/high", foreground: \.onSurfaceVariant, background: \.surfaceContainerHighest, minimum: 4.5),
        Pair(name: "primary/surface", foreground: \.primary, background: \.surface, minimum: 4.5),
        Pair(name: "info/surface", foreground: \.info, background: \.surface, minimum: 4.5),
        Pair(name: "outline/surface", foreground: \.outline, background: \.surface, minimum: 3),
    ] }

    @Test(arguments: seeds)
    func textPairsMeetAAContrast(seed: UInt32) {
        for dark in [false, true] {
            let scheme = MaterialScheme(seed: RGBColor(hex: seed), dark: dark)
            for pair in Self.pairs {
                let ratio = RGBColor.contrastRatio(scheme[keyPath: pair.foreground], scheme[keyPath: pair.background])
                #expect(ratio >= pair.minimum, "\(pair.name) \(dark ? "dark" : "light") seed \(String(seed, radix: 16)): \(ratio)")
            }
        }
    }

    @Test func surfaceContainersStepTowardsOnSurface() {
        let levels: [KeyPath<MaterialScheme, RGBColor>] = [
            \.surfaceContainerLowest, \.surfaceContainerLow, \.surfaceContainer, \.surfaceContainerHigh,
            \.surfaceContainerHighest,
        ]
        let light = MaterialScheme(seed: MaterialScheme.cocoSeed, dark: false)
        let lightLuminance = levels.map { light[keyPath: $0].relativeLuminance }
        #expect(lightLuminance == lightLuminance.sorted(by: >))
        let dark = MaterialScheme(seed: MaterialScheme.cocoSeed, dark: true)
        let darkLuminance = levels.map { dark[keyPath: $0].relativeLuminance }
        #expect(darkLuminance == darkLuminance.sorted(by: <))
    }

    @Test func lightIsLightAndDarkIsDark() {
        let light = MaterialScheme(seed: MaterialScheme.cocoSeed, dark: false)
        let dark = MaterialScheme(seed: MaterialScheme.cocoSeed, dark: true)
        #expect(light.surface.lch.l > 95)
        #expect(dark.surface.lch.l < 10)
        #expect(light.primary.lch.l < dark.primary.lch.l)
    }

    @Test func primaryFollowsTheSeedAndNeutralsAreWarmButQuiet() {
        let seed = MaterialScheme.cocoSeed
        let light = MaterialScheme(seed: seed, dark: false)
        #expect(abs(light.primary.lch.h - seed.lch.h) < 4)
        #expect(light.primary.lch.c > 30)
        // Surfaces carry a hint of the seed (warm cream), not a colour.
        #expect(light.surface.lch.c < 8)
        #expect(light.surface.lch.c > 1)
    }

    @Test func tertiaryIsAnotherHue() {
        let seed = MaterialScheme.cocoSeed
        let scheme = MaterialScheme(seed: seed, dark: false)
        let difference = abs(scheme.tertiary.lch.h - seed.lch.h)
        #expect(min(difference, 360 - difference) > 45)
    }

    // MARK: - Type scale

    @Test func typeRolesShrinkWithinEachGroup() {
        let groups: [[MaterialTypeRole]] = [
            [.displayLarge, .displayMedium, .displaySmall],
            [.headlineLarge, .headlineMedium, .headlineSmall],
            [.titleLarge, .titleMedium, .titleSmall],
            [.bodyLarge, .bodyMedium, .bodySmall],
            [.labelLarge, .labelMedium, .labelSmall],
        ]
        for group in groups {
            let sizes = group.map(\.size)
            #expect(sizes == sizes.sorted(by: >))
        }
        #expect(MaterialTypeRole.displaySmall.size > MaterialTypeRole.headlineLarge.size)
        #expect(MaterialTypeRole.headlineSmall.size > MaterialTypeRole.titleLarge.size)
    }

    @Test func kitchenSizesAreReadableAcrossTheRoom() {
        // The smallest everyday text on the display is bigger than iOS's default body (17pt).
        for role in MaterialTypeRole.allCases {
            #expect(role.size >= 20, "\(role)")
        }
    }

    @Test func dynamicTypeIsClamped() {
        // Big roles barely grow (the clock already fills the screen); small ones grow more.
        let clock = MaterialTypeRole.displayLarge
        #expect(clock.clampedSize(scaled: clock.size * 3) == clock.size * clock.maxScale)
        let label = MaterialTypeRole.labelSmall
        #expect(label.clampedSize(scaled: label.size * 1.2) == label.size * 1.2)
        #expect(label.clampedSize(scaled: label.size * 5) == label.size * label.maxScale)
        // Smaller text settings shrink, but not below what reads across the kitchen.
        #expect(label.clampedSize(scaled: label.size * 0.5) == label.size * MaterialTypeRole.minScale)
    }

    // MARK: - Shape

    @Test func shapeScaleGrows() {
        let radii = MaterialShape.allCases.map(\.radius)
        #expect(radii == radii.sorted())
        #expect(MaterialShape.large.radius >= 16)
        #expect(MaterialShape.extraLarge.radius <= 32)
    }
}
