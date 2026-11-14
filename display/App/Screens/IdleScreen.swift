import CocoKit
import SwiftUI

/// What the kitchen sees most of the day: a huge clock (from the iPad's own clock, so it never
/// depends on the Hub or the internet), the date, current weather and the next hours, and any
/// running timers. The bottom centre stays clear for the mic button.
struct IdleScreen: View {
    @Environment(AppModel.self) private var model
    @Environment(TimerStore.self) private var timers
    @Environment(IdleModel.self) private var idle
    @Environment(\.openDiagnostics) private var openDiagnostics

    var body: some View {
        ZStack {
            PhotoSlot()
            TimelineView(.everyMinute) { context in
                VStack(spacing: 0) {
                    Spacer(minLength: 24)
                    Text(context.date, format: .dateTime.hour().minute())
                        .font(.system(size: 220, weight: .light, design: .rounded))
                        .monospacedDigit()
                        .minimumScaleFactor(0.5)
                        .lineLimit(1)
                        // Hidden: 5 taps on the clock open diagnostics (#45).
                        .onTapGesture(count: 5) { openDiagnostics() }
                    Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                        .font(.system(size: 44, weight: .regular, design: .rounded))
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 24)
                    ForecastView(idle: idle, now: context.date, connection: model.connection)
                    if !timers.timers.isEmpty {
                        ActiveTimersStrip(timers: timers) { model.show(.timers) }
                            .padding(.top, 28)
                    }
                    if let nowPlaying = model.nowPlaying, NowPlayingRules.showsIdleStrip(nowPlaying) {
                        NowPlayingStrip(nowPlaying: nowPlaying) { model.show(.nowPlaying) }
                            .padding(.top, 28)
                    }
                    Spacer(minLength: 24)
                }
                .padding(.horizontal, 48)
                // Bottom centre is reserved for the tap-to-talk mic button.
                .padding(.bottom, 140)
            }
        }
        .overlay(alignment: .bottomLeading) {
            Button {
                model.show(.timers)
            } label: {
                Label("Timers", systemImage: "timer")
                    .font(.title2.weight(.medium))
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                    .background(.thinMaterial, in: Capsule())
            }
            .buttonStyle(.plain)
            .padding(32)
        }
    }
}

/// Reserved for the family photo slideshow (later phase). Intentionally empty for now.
private struct PhotoSlot: View {
    var body: some View {
        Color.clear
    }
}

private struct ForecastView: View {
    let idle: IdleModel
    let now: Date
    let connection: ConnectionState

    /// Never fetched: a spinner only while a Hub connection is actually being made.
    private var stillConnecting: Bool {
        switch connection {
        case .searching, .connecting, .connected: true
        case .unreachable, .unauthorized: false
        }
    }

    var body: some View {
        let status = idle.status(at: now)
        VStack(spacing: 20) {
            if status != .unavailable, status != .loading, let weather = idle.info?.weather {
                HStack(spacing: 24) {
                    Image(systemName: WeatherFormat.symbolName(code: weather.current.code, isDay: weather.current.isDay))
                        .symbolRenderingMode(.multicolor)
                        .font(.system(size: 72))
                    Text(WeatherFormat.temperature(weather.current.temp))
                        .font(.system(size: 88, weight: .medium, design: .rounded))
                        .monospacedDigit()
                    Text(weather.current.description)
                        .font(.system(size: 36, design: .rounded))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                HourlyStrip(hours: idle.upcomingHours(count: 6, at: now))
                if status == .stale {
                    Label(staleText, systemImage: "clock.badge.exclamationmark")
                        .font(.title3)
                        .foregroundStyle(.orange)
                }
            } else if status == .unavailable || !stillConnecting {
                Label("Forecast unavailable", systemImage: "icloud.slash")
                    .font(.system(size: 32, design: .rounded))
                    .foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.large)
            }
        }
    }

    private var staleText: String {
        guard let updated = idle.lastUpdated else { return "Forecast may be out of date" }
        return "Forecast from \(updated.formatted(date: .omitted, time: .shortened))"
    }
}

private struct HourlyStrip: View {
    let hours: [WeatherHour]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(hours, id: \.time) { hour in
                VStack(spacing: 10) {
                    Text(WeatherFormat.hourLabel(hour.time))
                        .font(.system(size: 26, weight: .medium, design: .rounded))
                        .foregroundStyle(.secondary)
                    Image(systemName: WeatherFormat.symbolName(code: hour.code, isDay: WeatherFormat.isDaytime(hour.time)))
                        .symbolRenderingMode(.multicolor)
                        .font(.system(size: 40))
                        .frame(height: 48)
                    Text(WeatherFormat.temperature(hour.temp))
                        .font(.system(size: 36, weight: .medium, design: .rounded))
                        .monospacedDigit()
                    Text(hour.precipProbability >= 30 ? "\(hour.precipProbability)%" : " ")
                        .font(.system(size: 22, design: .rounded))
                        .foregroundStyle(.blue)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: 900)
    }
}

/// Compact running timers on the idle screen; tapping opens the timers screen.
private struct ActiveTimersStrip: View {
    let timers: TimerStore
    let open: () -> Void

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            HStack(spacing: 16) {
                ForEach(timers.timers.prefix(4)) { timer in
                    Button(action: open) {
                        HStack(spacing: 12) {
                            Image(systemName: timer.isRinging ? "bell.and.waves.left.and.right.fill" : "timer")
                            Text(timer.displayName).lineLimit(1)
                            Text(TimerFormat.countdown(timers.remaining(of: timer, at: context.date)))
                                .monospacedDigit()
                                .fontWeight(.semibold)
                        }
                        .font(.system(size: 30, design: .rounded))
                        .padding(.horizontal, 24)
                        .padding(.vertical, 14)
                        .background(timer.isRinging ? AnyShapeStyle(.red.opacity(0.3)) : AnyShapeStyle(.thinMaterial), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
                if timers.timers.count > 4 {
                    Text("+\(timers.timers.count - 4)")
                        .font(.system(size: 30, design: .rounded))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
