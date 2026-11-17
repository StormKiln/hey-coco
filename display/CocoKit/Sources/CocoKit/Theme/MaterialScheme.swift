import Foundation

/// Material 3 colour roles from one seed colour (#113), with the M3 baseline tone mapping: a light
/// scheme for the day and a dark one for night mode. The palettes are a simpler stand-in for HCT:
/// tone is CIE L* (the same as HCT's tone), hue and chroma are CIE LCh.
public struct MaterialScheme: Equatable, Sendable {
    /// Coco's seed: terracotta, the colour of a clay pot or a bread crust. Warm and friendly
    /// without shouting, it gives creamy surfaces by day and warm (not blue-grey) ones at night,
    /// sits well next to food photos and album art, and its tertiary (60° on) is a herb green.
    public static let cocoSeed = RGBColor(hex: 0xC0643A)

    /// M3's error red (#B3261E), for the alarm and failures.
    static let errorSeed = RGBColor(hex: 0xB3261E)
    /// A custom "info" blue (chance of rain, links), at the same tones as primary.
    static let infoSeed = RGBColor(hex: 0x2F6FDE)

    public let isDark: Bool

    public let primary: RGBColor
    public let onPrimary: RGBColor
    public let primaryContainer: RGBColor
    public let onPrimaryContainer: RGBColor
    public let secondary: RGBColor
    public let onSecondary: RGBColor
    public let secondaryContainer: RGBColor
    public let onSecondaryContainer: RGBColor
    public let tertiary: RGBColor
    public let onTertiary: RGBColor
    public let tertiaryContainer: RGBColor
    public let onTertiaryContainer: RGBColor
    public let error: RGBColor
    public let onError: RGBColor
    public let errorContainer: RGBColor
    public let onErrorContainer: RGBColor
    public let info: RGBColor
    public let onInfo: RGBColor

    public let surface: RGBColor
    public let onSurface: RGBColor
    public let onSurfaceVariant: RGBColor
    public let surfaceContainerLowest: RGBColor
    public let surfaceContainerLow: RGBColor
    public let surfaceContainer: RGBColor
    public let surfaceContainerHigh: RGBColor
    public let surfaceContainerHighest: RGBColor
    public let inverseSurface: RGBColor
    public let inverseOnSurface: RGBColor
    public let outline: RGBColor
    public let outlineVariant: RGBColor
    /// M3's surface tint: the primary colour laid over raised surfaces.
    public let surfaceTint: RGBColor
    public let scrim: RGBColor

    /// The palettes behind the roles, M3 "tonal spot" style.
    public struct Palettes: Equatable, Sendable {
        public let primary: TonalPalette
        public let secondary: TonalPalette
        public let tertiary: TonalPalette
        public let neutral: TonalPalette
        public let neutralVariant: TonalPalette
        public let error: TonalPalette
        public let info: TonalPalette

        public init(seed: RGBColor) {
            let lch = seed.lch
            // CIE chroma runs ~1.3x CAM16's, so these are M3's 36/24/6/8 scaled up a little.
            // Secondary stays low: sRGB caps primary's chroma near tone 90, and the two
            // containers should still differ.
            primary = TonalPalette(hue: lch.h, chroma: max(lch.c, 48))
            secondary = TonalPalette(hue: lch.h, chroma: 14)
            tertiary = TonalPalette(hue: (lch.h + 60).truncatingRemainder(dividingBy: 360), chroma: 32)
            neutral = TonalPalette(hue: lch.h, chroma: 4)
            neutralVariant = TonalPalette(hue: lch.h, chroma: 10)
            error = TonalPalette(seed: MaterialScheme.errorSeed)
            info = TonalPalette(seed: MaterialScheme.infoSeed)
        }
    }

    public init(seed: RGBColor = MaterialScheme.cocoSeed, dark: Bool) {
        let p = Palettes(seed: seed)
        isDark = dark
        // Light: accent 40 on 100, containers 90 with 10. Dark: accent 80 on 20, containers 30 with 90.
        let accent = dark ? 80.0 : 40, onAccent = dark ? 20.0 : 100
        let container = dark ? 30.0 : 90, onContainer = dark ? 90.0 : 10
        primary = p.primary.tone(accent)
        onPrimary = p.primary.tone(onAccent)
        primaryContainer = p.primary.tone(container)
        onPrimaryContainer = p.primary.tone(onContainer)
        secondary = p.secondary.tone(accent)
        onSecondary = p.secondary.tone(onAccent)
        secondaryContainer = p.secondary.tone(container)
        onSecondaryContainer = p.secondary.tone(onContainer)
        tertiary = p.tertiary.tone(accent)
        onTertiary = p.tertiary.tone(onAccent)
        tertiaryContainer = p.tertiary.tone(container)
        onTertiaryContainer = p.tertiary.tone(onContainer)
        error = p.error.tone(accent)
        onError = p.error.tone(onAccent)
        errorContainer = p.error.tone(container)
        onErrorContainer = p.error.tone(onContainer)
        info = p.info.tone(accent)
        onInfo = p.info.tone(onAccent)

        surface = p.neutral.tone(dark ? 6 : 98)
        onSurface = p.neutral.tone(dark ? 90 : 10)
        onSurfaceVariant = p.neutralVariant.tone(dark ? 80 : 30)
        surfaceContainerLowest = p.neutral.tone(dark ? 4 : 100)
        surfaceContainerLow = p.neutral.tone(dark ? 10 : 96)
        surfaceContainer = p.neutral.tone(dark ? 12 : 94)
        surfaceContainerHigh = p.neutral.tone(dark ? 17 : 92)
        surfaceContainerHighest = p.neutral.tone(dark ? 22 : 90)
        inverseSurface = p.neutral.tone(dark ? 90 : 20)
        inverseOnSurface = p.neutral.tone(dark ? 20 : 95)
        outline = p.neutralVariant.tone(dark ? 60 : 50)
        outlineVariant = p.neutralVariant.tone(dark ? 30 : 80)
        surfaceTint = primary
        scrim = RGBColor(hex: 0x000000)
    }
}

/// M3's type roles, sized for reading across a kitchen on a 10.2" iPad (about 2–4 m away):
/// roughly 2x M3's phone sizes, and the smallest everyday text above iOS's 17pt body.
public enum MaterialTypeRole: String, CaseIterable, Sendable {
    case displayLarge, displayMedium, displaySmall
    case headlineLarge, headlineMedium, headlineSmall
    case titleLarge, titleMedium, titleSmall
    case bodyLarge, bodyMedium, bodySmall
    case labelLarge, labelMedium, labelSmall

    public enum Weight: Sendable { case light, regular, medium, semibold, bold }

    /// The point size at the default Dynamic Type setting.
    public var size: Double {
        switch self {
        case .displayLarge: 220  // the idle clock
        case .displayMedium: 120  // a timer's countdown
        case .displaySmall: 88  // the temperature, the alarm's title
        case .headlineLarge: 64  // an answer
        case .headlineMedium: 52  // the live transcript
        case .headlineSmall: 44  // the date, empty states
        case .titleLarge: 40
        case .titleMedium: 34
        case .titleSmall: 30
        case .bodyLarge: 32
        case .bodyMedium: 28
        case .bodySmall: 22
        case .labelLarge: 28
        case .labelMedium: 24
        case .labelSmall: 20
        }
    }

    public var weight: Weight {
        switch self {
        case .displayLarge: .light
        case .displayMedium, .displaySmall: .medium
        case .headlineLarge, .headlineMedium, .headlineSmall: .semibold
        case .titleLarge, .titleMedium, .titleSmall: .semibold
        case .bodyLarge, .bodyMedium, .bodySmall: .regular
        case .labelLarge, .labelMedium, .labelSmall: .semibold
        }
    }

    /// How far Dynamic Type may grow this role: kitchen text is already big, and the display
    /// roles fill the screen as they are.
    public var maxScale: Double {
        switch self {
        case .displayLarge, .displayMedium, .displaySmall: 1.1
        case .headlineLarge, .headlineMedium, .headlineSmall: 1.25
        case .titleLarge, .titleMedium, .titleSmall: 1.4
        case .bodyLarge, .bodyMedium, .bodySmall, .labelLarge, .labelMedium, .labelSmall: 1.6
        }
    }

    /// Smaller Dynamic Type settings shrink text no further than this.
    public static let minScale = 0.85

    /// `scaled` (the Dynamic Type size, e.g. from `@ScaledMetric`) kept within this role's range.
    public func clampedSize(scaled: Double) -> Double {
        min(max(scaled, size * Self.minScale), size * maxScale)
    }
}

/// M3's shape scale (corner radii), a step larger than on a phone.
public enum MaterialShape: CaseIterable, Sendable {
    case small, medium, large, extraLarge

    public var radius: Double {
        switch self {
        case .small: 12
        case .medium: 16
        case .large: 24
        case .extraLarge: 28
        }
    }
}
