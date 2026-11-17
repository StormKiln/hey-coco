import CocoKit
import SwiftUI

/// `ViewSpec.weather`: readable across the kitchen. Now (big temperature, icon, description,
/// feels-like), the next 12 hours, and the next days, each on a Material card. Hours and day names
/// follow the Hub's timezone. Back to idle after `WeatherForecast.idleTimeout` without a touch
/// (not while Coco is listening or talking). The bottom centre stays clear for the mic button.
/// Laid out to fit the kitchen iPad (810x1080pt, 7th gen) in both orientations.
struct WeatherScreen: View {
    let payload: ViewSpec.WeatherPayload
    @Environment(AppModel.self) private var model
    @Environment(VoiceLoop.self) private var voice: VoiceLoop?
    @State private var autoReturn: AutoReturnTimer?

    private var timeZone: TimeZone { model.night.timeZone }
    private var busy: Bool { voice?.conversation.state.isActive ?? false }

    var body: some View {
        TimelineView(.everyMinute) { context in
            GeometryReader { geometry in
                let landscape = geometry.size.width > geometry.size.height
                content(at: context.date, landscape: landscape)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(.horizontal, 48)
        .padding(.top, ScreenChrome.top)
        .padding(.bottom, ScreenChrome.bottom)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded { autoReturn?.noteInteraction() })
        // A place the person named (#93), in the clear band above the content.
        .overlay(alignment: .top) {
            if let place = payload.place {
                Text(place)
                    .m3Font(.headlineSmall)
                    .foregroundStyle(Theme.onSurface)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    .padding(.horizontal, 160)
                    .padding(.top, 24)
                    .accessibilityAddTraits(.isHeader)
            }
        }
        // Bottom-left, like the idle screen's Timers button: the top-left corner holds the
        // wake-word hint.
        .overlay(alignment: .bottomLeading) {
            Button {
                model.show(.idle)
            } label: {
                Label("Home", systemImage: "chevron.left")
            }
            .buttonStyle(MaterialButtonStyle(colors: .tonal, elevation: .level1))
            .padding(32)
        }
        .onAppear {
            autoReturn = AutoReturnTimer(timeout: WeatherForecast.idleTimeout, views: [.weather]) {
                [model] in model.show(.idle)
            }
            updateAutoReturn()
        }
        .onDisappear { autoReturn = nil }
        .onChange(of: model.currentView) { updateAutoReturn() }
        .onChange(of: busy) { updateAutoReturn() }
    }

    @ViewBuilder
    private func content(at now: Date, landscape: Bool) -> some View {
        let hours = WeatherForecast.upcomingHours(payload.hourly, at: now)
        let days = WeatherForecast.upcomingDays(payload.daily, at: now, timeZone: timeZone)
        if let compare = payload.compare {
            // #111: the named place beside home, row by row.
            let count = landscape ? WeatherCompare.landscapeDayCount : WeatherCompare.portraitDayCount
            let pairs = WeatherCompare.pairs(place: Array(days.prefix(count)), home: compare.daily)
            Group {
                if landscape {
                    comparisonGrid(compare, pairs: pairs, now: now)
                } else {
                    comparisonList(compare, pairs: pairs, now: now)
                }
            }
                .padding(.horizontal, 28)
                .padding(.vertical, 20)
                .m3Surface(Theme.surfaceContainerLow, shape: .extraLarge)
                .frame(maxWidth: 1000)
        } else if landscape {
            // Now and the hours on the left, the week on the right: seven day rows fit the
            // kitchen iPad's 810pt height only in a column of their own.
            HStack(alignment: .top, spacing: 20) {
                VStack(spacing: 16) {
                    current(compact: true).card(Theme.surfaceContainer)
                    hourly(hours).card()
                }
                daily(days, now: now)
                    .card()
                    .frame(width: 530)
            }
        } else {
            VStack(spacing: 16) {
                current(compact: false).card(Theme.surfaceContainer)
                hourly(hours).card()
                // Four days in portrait: seven ran under the Home and mic buttons on the kitchen
                // iPad (7th gen, 4:3).
                daily(Array(days.prefix(4)), now: now).card()
            }
            .frame(maxWidth: 760)
        }
    }

    private func updateAutoReturn() {
        autoReturn?.update(view: model.currentView, busy: busy)
    }

    /// Icon, temperature, description and feels-like in a row; `compact` (landscape's narrower
    /// column) puts the words under the temperature.
    @ViewBuilder
    private func current(compact: Bool) -> some View {
        Group {
            if compact {
                VStack(spacing: 0) {
                    HStack(spacing: 24) {
                        currentIcon(size: 72)
                        currentTemperature(.displaySmall)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 16) {
                        currentDescription(lines: 1)
                        currentFeelsLike
                    }
                }
            } else {
                HStack(spacing: 28) {
                    currentIcon(size: 100)
                    currentTemperature(.displayMedium)
                    VStack(alignment: .leading, spacing: 4) {
                        currentDescription(lines: 2)
                        currentFeelsLike
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }

    private func currentIcon(size: CGFloat) -> some View {
        WeatherIcon(code: payload.current.code, isDay: payload.current.isDay)
            .font(.system(size: size))
    }

    private func currentTemperature(_ role: MaterialTypeRole) -> some View {
        Text(WeatherFormat.temperature(payload.current.temp))
            .m3Font(role)
            .foregroundStyle(Theme.onSurface)
            .monospacedDigit()
            .lineLimit(1)
            .minimumScaleFactor(0.5)
    }

    private func currentDescription(lines: Int) -> some View {
        Text(payload.current.description)
            .m3Font(.titleLarge)
            .foregroundStyle(Theme.onSurface)
            .lineLimit(lines)
            .minimumScaleFactor(0.6)
    }

    private var currentFeelsLike: some View {
        Text("Feels like \(WeatherFormat.temperature(payload.current.feelsLike))")
            .m3Font(.bodyLarge)
            .foregroundStyle(Theme.onSurfaceVariant)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }

    /// Two rows of six, so twelve hours fit at a size readable from across the room.
    private func hourly(_ hours: [WeatherHour]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 6), spacing: 12) {
            ForEach(hours, id: \.time) { hour in
                hourCell(hour)
            }
        }
    }

    private func hourCell(_ hour: WeatherHour) -> some View {
        VStack(spacing: 6) {
            Text(WeatherFormat.hourLabel(hour.time, timeZone: timeZone))
                .m3Font(.labelMedium, weight: .medium)
                .foregroundStyle(Theme.onSurfaceVariant)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            WeatherIcon(code: hour.code, isDay: WeatherFormat.isDaytime(hour.time, timeZone: timeZone))
                .font(.system(size: 34))
                .frame(height: 40)
            Text(WeatherFormat.temperature(hour.temp))
                .m3Font(.titleMedium, weight: .medium)
                .foregroundStyle(Theme.onSurface)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(WeatherForecast.precipText(hour.precipProbability) ?? " ")
                .m3Font(.labelSmall, weight: .medium)
                .foregroundStyle(Theme.info)
        }
    }

    private func daily(_ days: [WeatherDay], now: Date) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
            ForEach(days, id: \.date) { day in
                dayRow(day, now: now)
            }
        }
    }

    private func dayRow(_ day: WeatherDay, now: Date) -> some View {
        GridRow {
            Text(WeatherForecast.dayLabel(day, at: now, timeZone: timeZone))
                .m3Font(.titleMedium, weight: .medium)
                .foregroundStyle(Theme.onSurface)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .frame(maxWidth: .infinity, alignment: .leading)
            WeatherIcon(code: day.code, isDay: true)
                .font(.system(size: 32))
                .frame(width: 48)
            Text(WeatherForecast.precipText(day.precipProbability) ?? "")
                .m3Font(.labelMedium, weight: .medium)
                .foregroundStyle(Theme.info)
                .frame(minWidth: 56, alignment: .trailing)
            Text(WeatherFormat.temperature(day.high))
                .m3Font(.titleMedium)
                .foregroundStyle(Theme.onSurface)
                .monospacedDigit()
                .frame(minWidth: 64, alignment: .trailing)
            Text(WeatherFormat.temperature(day.low))
                .m3Font(.titleMedium, weight: .regular)
                .foregroundStyle(Theme.onSurfaceVariant)
                .monospacedDigit()
                .frame(minWidth: 64, alignment: .trailing)
        }
        .lineLimit(1)
    }

    // MARK: - Compared with home (#111)

    /// Landscape. Columns: period | the place | Home | difference (the place minus home, "+6°")
    /// with a rain cue ("Rain there, dry here"). Now, then the days; no hourly strip, so two
    /// places stay readable from across the room.
    private func comparisonGrid(
        _ compare: WeatherComparison, pairs: [WeatherCompare.DayPair], now: Date
    ) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 28, verticalSpacing: 14) {
            GridRow {
                Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                columnHeader(WeatherCompare.shortName(payload.place ?? ""))
                columnHeader(compare.name)
                columnHeader("")
            }
            GridRow {
                rowLabel("Now")
                nowCell(payload.current)
                nowCell(compare.current)
                cueCell(nowDifference(compare), nowCue(compare))
            }
            .accessibilityElement(children: .combine)
            Divider()
                .overlay(Theme.outlineVariant)
                .gridCellUnsizedAxes(.horizontal)
            ForEach(pairs, id: \.place.date) { pair in
                GridRow {
                    rowLabel(WeatherForecast.dayLabel(pair.place, at: now, timeZone: timeZone))
                    dayCell(pair.place)
                    homeDayCell(pair.home)
                    if let home = pair.home {
                        cueCell(dayDifference(pair.place, home), WeatherCompare.precipCue(place: pair.place, home: home))
                    } else {
                        Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    /// Portrait: too narrow for a label column, so each period's name (and its rain cue) sits on
    /// its own line above the place, Home and difference columns.
    private func comparisonList(
        _ compare: WeatherComparison, pairs: [WeatherCompare.DayPair], now: Date
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            comparisonColumns {
                columnHeader(WeatherCompare.shortName(payload.place ?? ""))
            } home: {
                columnHeader(compare.name)
            } difference: {
                Color.clear.frame(height: 1)
            }
            VStack(alignment: .leading, spacing: 2) {
                periodLine("Now", cue: nowCue(compare))
                comparisonColumns {
                    nowCell(payload.current, role: .headlineMedium)
                } home: {
                    nowCell(compare.current, role: .headlineMedium)
                } difference: {
                    differenceText(nowDifference(compare))
                }
            }
            .accessibilityElement(children: .combine)
            Divider().overlay(Theme.outlineVariant)
            ForEach(pairs, id: \.place.date) { pair in
                dayPairBlock(pair, now: now)
            }
        }
    }

    private func dayPairBlock(_ pair: WeatherCompare.DayPair, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            periodLine(
                WeatherForecast.dayLabel(pair.place, at: now, timeZone: timeZone),
                cue: pair.home.flatMap { WeatherCompare.precipCue(place: pair.place, home: $0) })
            comparisonColumns {
                dayCell(pair.place)
            } home: {
                homeDayCell(pair.home)
            } difference: {
                if let home = pair.home { differenceText(dayDifference(pair.place, home)) }
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The place and Home share the width evenly; the difference gets a narrow column.
    private func comparisonColumns<Place: View, Home: View, Difference: View>(
        @ViewBuilder place: () -> Place, @ViewBuilder home: () -> Home,
        @ViewBuilder difference: () -> Difference
    ) -> some View {
        HStack(spacing: 16) {
            place().frame(maxWidth: .infinity, alignment: .leading)
            home().frame(maxWidth: .infinity, alignment: .leading)
            difference().frame(width: 100, alignment: .leading)
        }
        // Rows keep their natural height: nothing gets squeezed smaller than its neighbours.
        .fixedSize(horizontal: false, vertical: true)
    }

    private func periodLine(_ label: String, cue: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            rowLabel(label)
                .layoutPriority(1)
            Spacer(minLength: 0)
            if let cue {
                Text(cue)
                    .m3Font(.labelMedium, weight: .medium)
                    .foregroundStyle(Theme.info)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private func nowDifference(_ compare: WeatherComparison) -> String {
        WeatherCompare.difference(place: payload.current.temp, home: compare.current.temp)
    }

    private func nowCue(_ compare: WeatherComparison) -> String? {
        WeatherCompare.precipCue(placeCode: payload.current.code, homeCode: compare.current.code)
    }

    private func dayDifference(_ place: WeatherDay, _ home: WeatherDay) -> String {
        WeatherCompare.difference(place: place.high, home: home.high)
    }

    /// Home's day, or a dash past the end of home's forecast.
    @ViewBuilder
    private func homeDayCell(_ home: WeatherDay?) -> some View {
        if let home {
            dayCell(home)
        } else {
            Text("—")
                .m3Font(.titleMedium, weight: .regular)
                .foregroundStyle(Theme.onSurfaceVariant)
        }
    }

    private func differenceText(_ difference: String) -> some View {
        Text(difference)
            .m3Font(.titleMedium)
            .foregroundStyle(Theme.onSurface)
            .monospacedDigit()
            .lineLimit(1)
            .minimumScaleFactor(0.6)
    }

    private func columnHeader(_ text: String) -> some View {
        Text(text)
            .m3Font(.titleMedium)
            .foregroundStyle(Theme.primary)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .accessibilityAddTraits(.isHeader)
    }

    private func rowLabel(_ text: String) -> some View {
        Text(text)
            .m3Font(.titleSmall, weight: .medium)
            .foregroundStyle(Theme.onSurface)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
    }

    private func nowCell(_ now: WeatherNow, role: MaterialTypeRole = .headlineLarge) -> some View {
        HStack(spacing: 12) {
            WeatherIcon(code: now.code, isDay: now.isDay)
                .font(.system(size: 44))
                .frame(width: 56)
            Text(WeatherFormat.temperature(now.temp))
                .m3Font(role, weight: .medium)
                .foregroundStyle(Theme.onSurface)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
    }

    /// Icon, high, low, and the chance of precipitation when it's worth showing.
    private func dayCell(_ day: WeatherDay) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            WeatherIcon(code: day.code, isDay: true)
                .font(.system(size: 32))
                .frame(width: 48)
            Text(WeatherFormat.temperature(day.high))
                .m3Font(.titleMedium)
                .foregroundStyle(Theme.onSurface)
                .monospacedDigit()
            Text(WeatherFormat.temperature(day.low))
                .m3Font(.titleSmall, weight: .regular)
                .foregroundStyle(Theme.onSurfaceVariant)
                .monospacedDigit()
            if let precip = WeatherForecast.precipText(day.precipProbability) {
                Text(precip)
                    .m3Font(.labelSmall, weight: .medium)
                    .foregroundStyle(Theme.info)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }

    private func cueCell(_ difference: String, _ precip: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            differenceText(difference)
            if let precip {
                Text(precip)
                    .m3Font(.labelSmall, weight: .medium)
                    .foregroundStyle(Theme.info)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .minimumScaleFactor(0.6)
    }
}

private extension View {
    /// A Material card on the Weather screen.
    func card(_ color: Color = Theme.surfaceContainerLow) -> some View {
        padding(.horizontal, 24)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .m3Surface(color, shape: .extraLarge)
    }
}
