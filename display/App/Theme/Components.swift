import CocoKit
import SwiftUI

/// The colours of one Material component variant: container and content.
struct MaterialColors {
    let container: Color
    let content: Color
    var outline: Color? = nil

    static let filled = MaterialColors(container: Theme.primary, content: Theme.onPrimary)
    static let tonal = MaterialColors(container: Theme.secondaryContainer, content: Theme.onSecondaryContainer)
    static let primaryContainer = MaterialColors(container: Theme.primaryContainer, content: Theme.onPrimaryContainer)
    static let tertiaryContainer = MaterialColors(
        container: Theme.tertiaryContainer, content: Theme.onTertiaryContainer)
    static let danger = MaterialColors(container: Theme.error, content: Theme.onError)
    static let dangerContainer = MaterialColors(container: Theme.errorContainer, content: Theme.onErrorContainer)
    static let outlined = MaterialColors(container: .clear, content: Theme.primary, outline: Theme.outline)
    static let text = MaterialColors(container: .clear, content: Theme.primary)
    /// A surface-coloured control, e.g. over the idle screen.
    static let surface = MaterialColors(container: Theme.surfaceContainerHigh, content: Theme.onSurface)
}

/// M3 common buttons: pill shaped, a state layer when pressed, and at least 56pt tall (well over
/// the 44pt touch target).
struct MaterialButtonStyle: ButtonStyle {
    enum Size {
        case medium, large

        var minHeight: CGFloat { self == .large ? 72 : 56 }
        var role: MaterialTypeRole { self == .large ? .labelLarge : .labelMedium }
        var padding: CGFloat { self == .large ? 32 : 24 }
    }

    var colors: MaterialColors = .filled
    var size: Size = .large
    var elevation: Elevation = .level0

    func makeBody(configuration: Configuration) -> some View {
        MaterialButtonBody(configuration: configuration, colors: colors, size: size, elevation: elevation)
    }
}

private struct MaterialButtonBody: View {
    let configuration: ButtonStyleConfiguration
    let colors: MaterialColors
    let size: MaterialButtonStyle.Size
    let elevation: Elevation
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .m3Font(size.role)
            .labelStyle(MaterialLabelStyle())
            .padding(.horizontal, size.padding)
            .frame(minHeight: size.minHeight)
            .foregroundStyle(colors.content)
            .m3Surface(colors.container, shape: nil, elevation: isEnabled ? elevation : .level0)
            .overlay {
                if let outline = colors.outline { Capsule().strokeBorder(outline, lineWidth: 1.5) }
            }
            .overlay(Capsule().fill(colors.content.opacity(configuration.isPressed ? 0.12 : 0)))
            .contentShape(Capsule())
            .opacity(isEnabled ? 1 : 0.38)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

/// Icon and title with M3's 8pt-ish gap, scaled up for the kitchen.
struct MaterialLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 12) {
            configuration.icon
            configuration.title
        }
    }
}

/// A round icon button (M3 "icon button", filled or tonal). `diameter` is at least 56pt.
struct MaterialIconButtonStyle: ButtonStyle {
    var colors: MaterialColors = .tonal
    var diameter: CGFloat = 88
    var elevation: Elevation = .level0

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .labelStyle(.iconOnly)
            .foregroundStyle(colors.content)
            .frame(width: diameter, height: diameter)
            .m3Surface(colors.container, shape: nil, elevation: elevation)
            .overlay(Circle().fill(colors.content.opacity(configuration.isPressed ? 0.12 : 0)))
            .contentShape(Circle())
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

/// M3 chips (assist / suggestion), kitchen sized: a medium-rounded rectangle on a low surface
/// with a hairline outline, or a tonal container when it needs attention.
struct MaterialChipStyle: ButtonStyle {
    var colors: MaterialColors = MaterialColors(
        container: Theme.surfaceContainerLow, content: Theme.onSurface, outline: Theme.outlineVariant)
    var role: MaterialTypeRole = .titleSmall

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: Theme.radius(.medium), style: .continuous)
        return configuration.label
            .m3Font(role, weight: .medium)
            .labelStyle(MaterialLabelStyle())
            .foregroundStyle(colors.content)
            .padding(.horizontal, 24)
            .frame(minHeight: 64)
            .background(shape.fill(colors.container))
            .overlay {
                if let outline = colors.outline { shape.strokeBorder(outline, lineWidth: 1.5) }
            }
            .overlay(shape.fill(colors.content.opacity(configuration.isPressed ? 0.12 : 0)))
            .contentShape(shape)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

extension MaterialColors {
    /// A chip that needs attention (a ringing timer).
    static let alertChip = MaterialColors(container: Theme.errorContainer, content: Theme.onErrorContainer)
    /// A chip on the accent (a running timer).
    static let accentChip = MaterialColors(container: Theme.primaryContainer, content: Theme.onPrimaryContainer)
}
