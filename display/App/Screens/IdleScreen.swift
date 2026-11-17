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
                        .m3Font(.displayLarge)
                        .monospacedDigit()
                        .foregroundStyle(Theme.onSurface)
                        .minimumScaleFactor(0.5)
                        .lineLimit(1)
                        // Hidden: 5 taps on the clock open diagnostics (#45).
                        .onTapGesture(count: 5) { openDiagnostics() }
                    Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                        .m3Font(.headlineSmall, weight: .regular)
                        .foregroundStyle(Theme.primary)
                    Spacer(minLength: 24)
                    ForecastView(idle: idle, now: context.date, connection: model.connection)
                        .padding(.horizontal, 32)
                        .padding(.vertical, 24)
                        .frame(maxWidth: 960)
                        .m3Surface(Theme.surfaceContainerLow, shape: .extraLarge, elevation: .level1)
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
            }
            .buttonStyle(MaterialButtonStyle(colors: .tonal, elevation: .level1))
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
        case .unreachable, .unauthorized, .needsPairing, .untrustedHub: false
        }
    }

    var body: some View {
        let status = idle.status(at: now)
        VStack(spacing: 20) {
            if status != .unavailable, status != .loading, let weather = idle.info?.weather {
                HStack(spacing: 24) {
                    WeatherIcon(code: weather.current.code, isDay: weather.current.isDay)
                        .font(.system(size: 72))
                    Text(WeatherFormat.temperature(weather.current.temp))
                        .m3Font(.displaySmall)
                        .monospacedDigit()
                        .foregroundStyle(Theme.onSurface)
                    Text(weather.current.description)
                        .m3Font(.titleMedium, weight: .regular)
                        .foregroundStyle(Theme.onSurfaceVariant)
                        .lineLimit(1)
                }
                HourlyStrip(hours: idle.upcomingHours(count: 6, at: now))
                if status == .stale {
                    Label(staleText, systemImage: "clock.badge.exclamationmark")
                        .m3Font(.labelMedium)
                        .labelStyle(MaterialLabelStyle())
                        .foregroundStyle(Theme.onTertiaryContainer)
                        .padding(.horizontal, 20)
                        .frame(minHeight: 48)
                        .m3Surface(Theme.tertiaryContainer, shape: .medium)
                }
            } else if status == .unavailable || !stillConnecting {
                Label("Forecast unavailable", systemImage: "icloud.slash")
                    .m3Font(.bodyLarge)
                    .labelStyle(MaterialLabelStyle())
                    .foregroundStyle(Theme.onSurfaceVariant)
            } else {
                ProgressView().controlSize(.large).tint(Theme.primary)
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
                        .m3Font(.labelMedium, weight: .medium)
                        .foregroundStyle(Theme.onSurfaceVariant)
                    WeatherIcon(code: hour.code, isDay: WeatherFormat.isDaytime(hour.time))
                        .font(.system(size: 40))
                        .frame(height: 48)
                    Text(WeatherFormat.temperature(hour.temp))
                        .m3Font(.titleMedium, weight: .medium)
                        .monospacedDigit()
                        .foregroundStyle(Theme.onSurface)
                    Text(WeatherForecast.precipText(hour.precipProbability) ?? " ")
                        .m3Font(.labelSmall, weight: .medium)
                        .foregroundStyle(Theme.info)
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
                    }
                    .buttonStyle(MaterialChipStyle(colors: timer.isRinging ? .alertChip : .accentChip))
                }
                if timers.timers.count > 4 {
                    Text("+\(timers.timers.count - 4)")
                        .m3Font(.titleSmall, weight: .medium)
                        .foregroundStyle(Theme.onSurfaceVariant)
                }
            }
        }
    }
}
