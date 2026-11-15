import CocoKit
import SwiftUI

/// A weather SF Symbol drawn with palette colours that read on both the day (light) and night
/// (dark) themes. Multicolor draws clouds and moons white, so they vanished on the light theme.
struct WeatherIcon: View {
    let code: Int
    let isDay: Bool

    var body: some View {
        let symbol = WeatherFormat.symbolName(code: code, isDay: isDay)
        let styles = WeatherFormat.tints(forSymbol: symbol).map(Self.color)
        Image(systemName: symbol)
            .symbolRenderingMode(.palette)
            .foregroundStyle(
                styles[0], styles.count > 1 ? styles[1] : styles[0],
                styles.count > 2 ? styles[2] : styles.last ?? styles[0])
    }

    private static func color(_ tint: WeatherTint) -> Color {
        switch tint {
        case .cloud: Color(.systemGray)
        case .sun: Color(.systemYellow)
        case .moon: Color(.systemIndigo)
        case .rain: Color(.systemBlue)
        case .snow: Color(.systemTeal)
        case .fog: Color(.systemGray2)
        }
    }
}
