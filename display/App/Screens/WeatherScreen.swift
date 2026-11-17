import CocoKit
import SwiftUI

/// `ViewSpec.weather`: readable across the kitchen. Now (big temperature, icon, description,
/// feels-like), the next 12 hours, and the next days. Hours and day names follow the Hub's
/// timezone. Back to idle after `WeatherForecast.idleTimeout` without a touch (not while Coco
/// is listening or talking). The bottom centre stays clear for the mic button.
struct WeatherScreen: View {
    let payload: ViewSpec.WeatherPayload
    @Environment(AppModel.self) private var model
    @Environment(VoiceLoop.self) private var voice: VoiceLoop?
    @State private var autoReturn: AutoReturnTimer?

    private var timeZone: TimeZone { model.night.timeZone }
    private var busy: Bool { voice?.conversation.state.isActive ?? false }

    var body: some View {
        TimelineView(.everyMinute) { context in
            let hours = WeatherForecast.upcomingHours(payload.hourly, at: context.date)
            let days = WeatherForecast.upcomingDays(payload.daily, at: context.date, timeZone: timeZone)
            GeometryReader { geometry in
                if let compare = payload.compare {
                    // #111: the named place beside home, row by row.
                    let landscape = geometry.size.width > geometry.size.height
                    let count = landscape ? WeatherCompare.landscapeDayCount : WeatherCompare.portraitDayCount
                    comparison(compare, days: Array(days.prefix(count)), now: context.date)
                        .frame(maxWidth: 1000)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if geometry.size.width > geometry.size.height {
                    HStack(alignment: .center, spacing: 56) {
                        VStack(spacing: 48) {
                            current
                            hourly(hours)
                        }
                        .frame(maxWidth: .infinity)
                        daily(days, now: context.date)
                            .frame(maxWidth: 560)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    VStack(spacing: 48) {
                        current
                        hourly(hours)
                        // Four days in portrait: seven ran under the Home and mic buttons, and the
                        // kitchen iPad (7th gen, 4:3) is shorter than the simulator it was laid out on.
                        daily(Array(days.prefix(4)), now: context.date)
                            .frame(maxWidth: 700)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .padding(.horizontal, 48)
        .padding(.top, 96)
        // Clear of the mic button.
        .padding(.bottom, 140)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded { autoReturn?.noteInteraction() })
        // A place the person named (#93), in the clear band above the content.
        .overlay(alignment: .top) {
            if let place = payload.place {
                Text(place)
                    .font(.system(size: 44, weight: .semibold, design: .rounded))
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
                    .font(.title2.weight(.medium))
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                    .background(.thinMaterial, in: Capsule())
            }
            .buttonStyle(.plain)
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

    private func updateAutoReturn() {
        autoReturn?.update(view: model.currentView, busy: busy)
    }

    private var current: some View {
        HStack(spacing: 32) {
            WeatherIcon(code: payload.current.code, isDay: payload.current.isDay)
                .font(.system(size: 120))
            Text(WeatherFormat.temperature(payload.current.temp))
                .font(.system(size: 150, weight: .medium, design: .rounded))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.5)
            VStack(alignment: .leading, spacing: 8) {
                Text(payload.current.description)
                    .font(.system(size: 44, weight: .semibold, design: .rounded))
                    .lineLimit(2)
                    .minimumScaleFactor(0.6)
                Text("Feels like \(WeatherFormat.temperature(payload.current.feelsLike))")
                    .font(.system(size: 32, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// Two rows of six, so twelve hours fit at a size readable from across the room.
    private func hourly(_ hours: [WeatherHour]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 6), spacing: 28) {
            ForEach(hours, id: \.time) { hour in
                VStack(spacing: 8) {
                    Text(WeatherFormat.hourLabel(hour.time, timeZone: timeZone))
                        .font(.system(size: 24, weight: .medium, design: .rounded))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                    WeatherIcon(code: hour.code, isDay: WeatherFormat.isDaytime(hour.time, timeZone: timeZone))
                        .font(.system(size: 36))
                        .frame(height: 44)
                    Text(WeatherFormat.temperature(hour.temp))
                        .font(.system(size: 34, weight: .medium, design: .rounded))
                        .monospacedDigit()
                    Text(WeatherForecast.precipText(hour.precipProbability) ?? " ")
                        .font(.system(size: 22, weight: .medium, design: .rounded))
                        .foregroundStyle(.blue)
                }
            }
        }
    }

    private func daily(_ days: [WeatherDay], now: Date) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 18) {
            ForEach(days, id: \.date) { day in
                GridRow {
                    Text(WeatherForecast.dayLabel(day, at: now, timeZone: timeZone))
                        .font(.system(size: 34, weight: .medium, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    WeatherIcon(code: day.code, isDay: true)
                        .font(.system(size: 34))
                        .frame(width: 52)
                    Text(WeatherForecast.precipText(day.precipProbability) ?? "")
                        .font(.system(size: 24, weight: .medium, design: .rounded))
                        .foregroundStyle(.blue)
                        .frame(minWidth: 64, alignment: .trailing)
                    Text(WeatherFormat.temperature(day.high))
                        .font(.system(size: 34, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .frame(minWidth: 72, alignment: .trailing)
                    Text(WeatherFormat.temperature(day.low))
                        .font(.system(size: 34, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .frame(minWidth: 72, alignment: .trailing)
                }
            }
        }
    }

    // MARK: - Compared with home (#111)

    /// Columns: period | the place | Home | difference (the place minus home, "+6°") with a
    /// rain cue ("Rain there, dry here"). Now, then the days; no hourly strip, so two places
    /// stay readable from across the room.
    private func comparison(_ compare: WeatherComparison, days: [WeatherDay], now: Date) -> some View {
        let pairs = WeatherCompare.pairs(place: days, home: compare.daily)
        return Grid(alignment: .leading, horizontalSpacing: 28, verticalSpacing: 22) {
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
                cueCell(
                    WeatherCompare.difference(place: payload.current.temp, home: compare.current.temp),
                    WeatherCompare.precipCue(placeCode: payload.current.code, homeCode: compare.current.code))
            }
            .accessibilityElement(children: .combine)
            Divider().gridCellUnsizedAxes(.horizontal)
            ForEach(pairs, id: \.place.date) { pair in
                GridRow {
                    rowLabel(WeatherForecast.dayLabel(pair.place, at: now, timeZone: timeZone))
                    dayCell(pair.place)
                    if let home = pair.home {
                        dayCell(home)
                        cueCell(
                            WeatherCompare.difference(place: pair.place.high, home: home.high),
                            WeatherCompare.precipCue(place: pair.place, home: home))
                    } else {
                        Text("—")
                            .font(.system(size: 34, design: .rounded))
                            .foregroundStyle(.secondary)
                        Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    private func columnHeader(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 34, weight: .semibold, design: .rounded))
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .accessibilityAddTraits(.isHeader)
    }

    private func rowLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 34, weight: .medium, design: .rounded))
            .lineLimit(1)
            .minimumScaleFactor(0.6)
    }

    private func nowCell(_ now: WeatherNow) -> some View {
        HStack(spacing: 14) {
            WeatherIcon(code: now.code, isDay: now.isDay)
                .font(.system(size: 48))
                .frame(width: 64)
            Text(WeatherFormat.temperature(now.temp))
                .font(.system(size: 64, weight: .medium, design: .rounded))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
    }

    /// Icon, high, low, and the chance of precipitation when it's worth showing.
    private func dayCell(_ day: WeatherDay) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            WeatherIcon(code: day.code, isDay: true)
                .font(.system(size: 34))
                .frame(width: 52)
            Text(WeatherFormat.temperature(day.high))
                .font(.system(size: 38, weight: .semibold, design: .rounded))
                .monospacedDigit()
            Text(WeatherFormat.temperature(day.low))
                .font(.system(size: 30, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(.secondary)
            if let precip = WeatherForecast.precipText(day.precipProbability) {
                Text(precip)
                    .font(.system(size: 22, weight: .medium, design: .rounded))
                    .foregroundStyle(.blue)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }

    private func cueCell(_ difference: String, _ precip: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(difference)
                .font(.system(size: 38, weight: .semibold, design: .rounded))
                .monospacedDigit()
            if let precip {
                Text(precip)
                    .font(.system(size: 22, weight: .medium, design: .rounded))
                    .foregroundStyle(.blue)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.6)
    }
}
