import Foundation

extension MaterialScheme {
    /// A warm gold for the sun and (paler) the moon: a fixed hue, so any seed keeps a yellow sun.
    static let sunHue = 80.0

    /// The colour of one weather icon layer (`WeatherFormat.tints(forSymbol:)`) in this scheme:
    /// rain is the chance-of-rain blue (`info`), clouds and fog the warm neutrals, the sun and
    /// moon gold. Every layer keeps 3:1 against the screen's surfaces, by day and by night.
    public func weather(_ tint: WeatherTint) -> RGBColor {
        weatherColors[tint] ?? onSurfaceVariant
    }

    static func weatherColors(palettes p: Palettes, dark: Bool) -> [WeatherTint: RGBColor] {
        let sun = TonalPalette(hue: sunHue, chroma: 70)
        let moon = TonalPalette(hue: sunHue, chroma: 36)
        let snow = TonalPalette(hue: p.info.hue, chroma: 24)
        // Light tones stay at or under ~55 (3:1 on surfaceContainer, tone 94); dark ones at or
        // over ~50 (3:1 on tone 12).
        return [
            .sun: sun.tone(dark ? 82 : 56),
            .moon: moon.tone(dark ? 86 : 52),
            .rain: p.info.tone(dark ? 80 : 40),
            .snow: snow.tone(dark ? 88 : 52),
            .cloud: p.neutralVariant.tone(dark ? 76 : 54),
            .fog: p.neutralVariant.tone(dark ? 58 : 40),
        ]
    }
}
