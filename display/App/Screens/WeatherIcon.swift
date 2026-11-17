import CocoKit
import SwiftUI

/// A weather SF Symbol drawn in Coco's theme (`Theme.weather`): gold sun and moon, warm grey
/// clouds, the chance-of-rain blue for rain, by day and by night. (Multicolor draws clouds and
/// moons white, so they vanished on the light theme; system indigo made a purple moon.)
struct WeatherIcon: View {
    let code: Int
    let isDay: Bool

    var body: some View {
        let symbol = WeatherFormat.symbolName(code: code, isDay: isDay)
        let styles = WeatherFormat.tints(forSymbol: symbol).map(Theme.weather)
        Image(systemName: symbol)
            .symbolRenderingMode(.palette)
            .foregroundStyle(
                styles[0], styles.count > 1 ? styles[1] : styles[0],
                styles.count > 2 ? styles[2] : styles.last ?? styles[0])
    }
}
