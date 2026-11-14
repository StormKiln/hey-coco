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
                    .font(.title3)
                    .foregroundStyle(.secondary)
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
                    .font(.title2.weight(.medium))
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
            }
            Spacer()
            Text("Timers")
                .font(.system(size: 48, weight: .semibold, design: .rounded))
            Spacer()
            Button {
                showingPresets = true
            } label: {
                Label("New timer", systemImage: "plus")
                    .labelStyle(.iconOnly)
                    .font(.system(size: 40, weight: .semibold))
                    .frame(width: 88, height: 88)
                    .background(.tint.opacity(0.15), in: Circle())
            }
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
            Text("No timers")
                .font(.system(size: 56, weight: .semibold, design: .rounded))
                .foregroundStyle(.secondary)
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
                    .font(.system(size: 40, weight: .semibold, design: .rounded))
                    .lineLimit(1)
                if timer.isLocalOnly {
                    Image(systemName: "icloud.slash")
                        .font(.title2)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Only on this iPad")
                }
            }
            Text(timer.isRinging ? "Time's up" : TimerFormat.countdown(remaining))
                .font(.system(size: 120, weight: .medium, design: .rounded))
                .monospacedDigit()
                .minimumScaleFactor(0.4)
                .lineLimit(1)
                .foregroundStyle(timer.isRinging ? .red : .primary)
            HStack(spacing: 20) {
                if timer.isRinging {
                    CardButton(title: "Stop", systemImage: "stop.fill", tint: .red, action: stop)
                } else {
                    CardButton(title: "+1 min", systemImage: "plus", tint: .accentColor, action: addMinute)
                    CardButton(title: "Cancel", systemImage: "xmark", tint: .secondary, action: cancel)
                }
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 32, style: .continuous))
    }
}

private struct CardButton: View {
    let title: String
    let systemImage: String
    let tint: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.system(size: 28, weight: .semibold, design: .rounded))
                .frame(minWidth: 160, minHeight: 72)
        }
        .buttonStyle(.bordered)
        .tint(tint)
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
                        .font(.system(size: 40, weight: .semibold, design: .rounded))
                        .frame(maxWidth: .infinity, minHeight: 110)
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }
}

private struct PresetPicker: View {
    let start: (Int) -> Void

    var body: some View {
        VStack(spacing: 24) {
            Text("New timer")
                .font(.system(size: 36, weight: .semibold, design: .rounded))
            PresetGrid(start: start)
        }
        .padding(32)
        .frame(width: 600)
    }
}
