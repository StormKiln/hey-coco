import CocoKit
import SwiftUI

/// `ViewSpec.timers`: big countdown cards, add-time and cancel, and "+" for touch timers that
/// work even with the Hub down. Returns to idle when the last timer goes (`AppModel`), or after
/// a minute of showing no timers. The bottom centre stays clear for the mic button.
struct TimersScreen: View {
    @Environment(AppModel.self) private var model
    @Environment(TimerStore.self) private var timers
    @State private var showingPresets = false

    static let presetMinutes = [1, 3, 5, 10, 15, 30]

    var body: some View {
        VStack(spacing: 24) {
            header
            if timers.timers.isEmpty {
                emptyState
            } else {
                TimelineView(.periodic(from: .now, by: 0.5)) { context in
                    ScrollView {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 440), spacing: 24)], spacing: 24) {
                            ForEach(timers.timers) { timer in
                                TimerCard(
                                    timer: timer,
                                    remaining: timers.remaining(of: timer, at: context.date),
                                    addMinute: { Task { await model.addTime(to: timer, seconds: 60) } },
                                    cancel: { Task { await timers.cancel(id: timer.id) } },
                                    stop: { Task { await timers.dismiss(id: timer.id) } })
                            }
                        }
                        .padding(.horizontal, 32)
                    }
                }
            }
            if timers.hasPendingSync {
                Label("Saved on this iPad. Will sync with Coco Hub when it's back.", systemImage: "icloud.slash")
                    .m3Font(.labelMedium, weight: .medium)
                    .labelStyle(MaterialLabelStyle())
                    .foregroundStyle(Theme.onSurfaceVariant)
            }
        }
        .padding(.top, 72)
        // Bottom centre is reserved for the tap-to-talk mic button.
        .padding(.bottom, 140)
        .task(id: timers.timers.isEmpty) {
            guard timers.timers.isEmpty else { return }
            try? await Task.sleep(for: .seconds(60))
            if !Task.isCancelled, timers.timers.isEmpty, model.currentView == .timers {
                model.show(.idle)
            }
        }
    }

    private var header: some View {
        HStack {
            Button {
                model.show(.idle)
            } label: {
                Label("Clock", systemImage: "chevron.left")
            }
            .buttonStyle(MaterialButtonStyle(colors: .tonal, size: .medium))
            Spacer()
            Text("Timers")
                .m3Font(.headlineMedium)
                .foregroundStyle(Theme.onSurface)
            Spacer()
            Button {
                showingPresets = true
            } label: {
                Label("New timer", systemImage: "plus")
                    .font(.system(size: 40, weight: .semibold))
            }
            .buttonStyle(MaterialIconButtonStyle(colors: .primaryContainer, diameter: 88, elevation: .level1))
            .accessibilityLabel("New timer")
            .popover(isPresented: $showingPresets) {
                PresetPicker { minutes in
                    showingPresets = false
                    start(minutes: minutes)
                }
            }
        }
        .padding(.horizontal, 32)
    }

    private var emptyState: some View {
        VStack(spacing: 32) {
            Spacer()
            Image(systemName: "timer")
                .font(.system(size: 72, weight: .medium))
                .foregroundStyle(Theme.onSecondaryContainer)
                .frame(width: 144, height: 144)
                .m3Surface(Theme.secondaryContainer, shape: nil)
                .accessibilityHidden(true)
            Text("No timers")
                .m3Font(.headlineMedium)
                .foregroundStyle(Theme.onSurfaceVariant)
            PresetGrid(start: start)
                .frame(maxWidth: 720)
            Spacer()
        }
    }

    private func start(minutes: Int) {
        Task { await timers.create(durationS: minutes * 60) }
    }
}

private struct TimerCard: View {
    let timer: DisplayTimer
    let remaining: TimeInterval
    let addMinute: () -> Void
    let cancel: () -> Void
    let stop: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text(timer.displayName)
                    .m3Font(.titleLarge)
                    .foregroundStyle(timer.isRinging ? Theme.onErrorContainer : Theme.onSurface)
                    .lineLimit(1)
                if timer.isLocalOnly {
                    Image(systemName: "icloud.slash")
                        .font(.title2)
                        .foregroundStyle(Theme.onSurfaceVariant)
                        .accessibilityLabel("Only on this iPad")
                }
            }
            Text(timer.isRinging ? "Time's up" : TimerFormat.countdown(remaining))
                .m3Font(.displayMedium)
                .monospacedDigit()
                .minimumScaleFactor(0.4)
                .lineLimit(1)
                .foregroundStyle(timer.isRinging ? Theme.onErrorContainer : Theme.primary)
            HStack(spacing: 20) {
                if timer.isRinging {
                    CardButton(title: "Stop", systemImage: "stop.fill", colors: .danger, action: stop)
                } else {
                    CardButton(title: "+1 min", systemImage: "plus", colors: .tonal, action: addMinute)
                    CardButton(title: "Cancel", systemImage: "xmark", colors: .outlined, action: cancel)
                }
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity)
        // An M3 card; a ringing one turns to the error container.
        .m3Surface(
            timer.isRinging ? Theme.errorContainer : Theme.surfaceContainerLow, shape: .extraLarge,
            elevation: .level1)
    }
}

private struct CardButton: View {
    let title: String
    let systemImage: String
    let colors: MaterialColors
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .frame(minWidth: 120)
        }
        .buttonStyle(MaterialButtonStyle(colors: colors))
    }
}

private struct PresetGrid: View {
    let start: (Int) -> Void

    var body: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 20), count: 3), spacing: 20) {
            ForEach(TimersScreen.presetMinutes, id: \.self) { minutes in
                Button {
                    start(minutes)
                } label: {
                    Text("\(minutes) min")
                        .m3Font(.titleLarge)
                        .frame(maxWidth: .infinity, minHeight: 110)
                }
                // Large M3 buttons: the seed's primary container, rounded (not pill) in a grid.
                .buttonStyle(PresetButtonStyle())
            }
        }
    }
}

private struct PresetPicker: View {
    let start: (Int) -> Void

    var body: some View {
        VStack(spacing: 24) {
            Text("New timer")
                .m3Font(.titleMedium)
                .foregroundStyle(Theme.onSurface)
            PresetGrid(start: start)
        }
        .padding(32)
        .frame(width: 600)
    }
}

/// A preset tile: primary container, extra-large corners, a state layer when pressed.
private struct PresetButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: Theme.radius(.extraLarge), style: .continuous)
        return configuration.label
            .foregroundStyle(Theme.onPrimaryContainer)
            .background(shape.fill(Theme.primaryContainer))
            .overlay(shape.fill(Theme.onPrimaryContainer.opacity(configuration.isPressed ? 0.12 : 0)))
            .contentShape(shape)
    }
}
