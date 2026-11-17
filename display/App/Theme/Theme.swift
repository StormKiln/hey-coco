import CocoKit
import SwiftUI
import UIKit

/// Coco's Material 3 look (#113). Colours are the roles of `MaterialScheme` (CocoKit) for the
/// terracotta seed, each a dynamic colour: the light scheme by day, the dark one when night mode
/// (or the system) turns the screen dark. They work in sheets and forms with no environment
/// plumbing, because UIKit resolves them per trait collection.
enum Theme {
    static let light = MaterialScheme(dark: false)
    static let dark = MaterialScheme(dark: true)

    static let primary = role(\.primary)
    static let onPrimary = role(\.onPrimary)
    static let primaryContainer = role(\.primaryContainer)
    static let onPrimaryContainer = role(\.onPrimaryContainer)
    static let secondary = role(\.secondary)
    static let secondaryContainer = role(\.secondaryContainer)
    static let onSecondaryContainer = role(\.onSecondaryContainer)
    static let tertiary = role(\.tertiary)
    static let tertiaryContainer = role(\.tertiaryContainer)
    static let onTertiaryContainer = role(\.onTertiaryContainer)
    static let error = role(\.error)
    static let onError = role(\.onError)
    static let errorContainer = role(\.errorContainer)
    static let onErrorContainer = role(\.onErrorContainer)
    static let info = role(\.info)

    static let surface = role(\.surface)
    static let onSurface = role(\.onSurface)
    static let onSurfaceVariant = role(\.onSurfaceVariant)
    static let surfaceContainerLowest = role(\.surfaceContainerLowest)
    static let surfaceContainerLow = role(\.surfaceContainerLow)
    static let surfaceContainer = role(\.surfaceContainer)
    static let surfaceContainerHigh = role(\.surfaceContainerHigh)
    static let surfaceContainerHighest = role(\.surfaceContainerHighest)
    static let inverseSurface = role(\.inverseSurface)
    static let inverseOnSurface = role(\.inverseOnSurface)
    static let outline = role(\.outline)
    static let outlineVariant = role(\.outlineVariant)
    static let surfaceTint = role(\.surfaceTint)

    /// The alarm flashes between these two (light scheme in both modes: it must shout at night).
    static let alarmRed = Color(light.error)
    static let alarmOrange = Color(MaterialScheme.Palettes(seed: MaterialScheme.cocoSeed).primary.tone(55))

    private static func role(_ role: KeyPath<MaterialScheme, RGBColor>) -> Color {
        Color(light: light[keyPath: role], dark: dark[keyPath: role])
    }

    static func radius(_ shape: MaterialShape) -> CGFloat { CGFloat(shape.radius) }

    /// A weather icon layer's colour (`MaterialScheme.weather(_:)`), by day and by night.
    static func weather(_ tint: WeatherTint) -> Color {
        Color(light: light.weather(tint), dark: dark.weather(tint))
    }
}

extension Color {
    init(_ rgb: RGBColor) {
        self.init(.sRGB, red: rgb.red, green: rgb.green, blue: rgb.blue)
    }

    /// A colour that follows the light/dark appearance.
    init(light: RGBColor, dark: RGBColor) {
        self.init(
            uiColor: UIColor { traits in
                let rgb = traits.userInterfaceStyle == .dark ? dark : light
                return UIColor(red: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1)
            })
    }
}

// MARK: - Type

extension MaterialTypeRole {
    /// The iOS text style Dynamic Type scales this role with.
    var textStyle: Font.TextStyle {
        switch self {
        case .displayLarge, .displayMedium, .displaySmall: .largeTitle
        case .headlineLarge, .headlineMedium, .headlineSmall: .title
        case .titleLarge, .titleMedium, .titleSmall: .title3
        case .bodyLarge, .bodyMedium, .bodySmall: .body
        case .labelLarge, .labelMedium, .labelSmall: .callout
        }
    }

    var fontWeight: Font.Weight {
        switch weight {
        case .light: .light
        case .regular: .regular
        case .medium: .medium
        case .semibold: .semibold
        case .bold: .bold
        }
    }
}

/// SF Rounded at a Material type role's size, following Dynamic Type within the role's range.
private struct MaterialFont: ViewModifier {
    let role: MaterialTypeRole
    let weight: Font.Weight?
    @ScaledMetric private var scaled: CGFloat

    init(role: MaterialTypeRole, weight: Font.Weight?) {
        self.role = role
        self.weight = weight
        _scaled = ScaledMetric(wrappedValue: CGFloat(role.size), relativeTo: role.textStyle)
    }

    func body(content: Content) -> some View {
        let size = CGFloat(role.clampedSize(scaled: Double(scaled)))
        return content.font(.system(size: size, weight: weight ?? role.fontWeight, design: .rounded))
    }
}

extension View {
    /// Text in one of Coco's Material type roles (`MaterialTypeRole`).
    func m3Font(_ role: MaterialTypeRole, weight: Font.Weight? = nil) -> some View {
        modifier(MaterialFont(role: role, weight: weight))
    }
}

// MARK: - Surfaces and elevation

/// M3 tonal elevation: raised surfaces take more of the primary colour (the surface tint)
/// rather than a heavier shadow; the shadow stays very soft.
enum Elevation: Int {
    case level0, level1, level2, level3

    var tintOpacity: Double { [0, 0.05, 0.08, 0.11][rawValue] }
    var shadowRadius: CGFloat { [0, 6, 12, 18][rawValue] }
    var shadowY: CGFloat { [0, 2, 4, 6][rawValue] }
    var shadowOpacity: Double { [0, 0.08, 0.10, 0.12][rawValue] }
}

private struct MaterialSurface: ViewModifier {
    let color: Color
    let radius: CGFloat
    let elevation: Elevation

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        return content.background {
            shape.fill(color)
                .overlay(shape.fill(Theme.surfaceTint.opacity(elevation.tintOpacity)))
                .shadow(
                    color: .black.opacity(elevation.shadowOpacity), radius: elevation.shadowRadius,
                    y: elevation.shadowY)
        }
    }
}

extension View {
    /// A Material surface behind the view: `color` in a rounded `shape` (`nil` is fully rounded,
    /// a pill), tinted and shadowed for its elevation.
    func m3Surface(
        _ color: Color = Theme.surfaceContainer, shape: MaterialShape? = .large, elevation: Elevation = .level0
    ) -> some View {
        modifier(MaterialSurface(color: color, radius: shape.map(Theme.radius) ?? 1000, elevation: elevation))
    }

    /// Settings-style lists on Coco's surfaces, tinted with the primary colour.
    func m3FormStyle() -> some View {
        scrollContentBackground(.hidden)
            .background(Theme.surfaceContainer.ignoresSafeArea())
            .tint(Theme.primary)
    }
}
