import CocoKit
import SwiftUI

/// `ViewSpec.nowPlaying` (#41): what's playing (Spotify via the Hub, or Apple Music on the iPad,
/// #42), readable from across the room. Pause/resume and next go to the Hub as touch utterances
/// with `media_playing: music`; the Hub drives Spotify or sends Apple Music its command. Returns to idle after `NowPlayingRules.idleTimeout` with nothing
/// playing. The bottom centre stays clear for the mic button.
struct NowPlayingScreen: View {
    @Environment(AppModel.self) private var model
    @State private var autoReturn: AutoReturnTimer?
    /// The stacked layout's text and controls, measured, so the artwork gets the rest.
    @State private var detailsHeight: CGFloat = 420

    /// Clear of the wake-word hint and Home button (top left), and the mic button (bottom
    /// centre: 112pt, 36pt off the bottom edge, plus a margin for its shadow).
    static let topInset: CGFloat = 168
    static let bottomInset: CGFloat = 172
    static let sideInset: CGFloat = 48
    static let artworkRange: ClosedRange<CGFloat> = 240...460

    var body: some View {
        GeometryReader { geometry in
            let available = CGSize(
                width: max(geometry.size.width - 2 * Self.sideInset, 0),
                height: max(geometry.size.height - Self.topInset - Self.bottomInset, 0))
            // Fits without scrolling in both orientations; scrolls rather than clip if Dynamic
            // Type makes it taller than the screen.
            ScrollView {
                content(in: available)
                    .frame(width: available.width)
                    .frame(minHeight: available.height)
                    .padding(.top, Self.topInset)
                    .padding(.bottom, Self.bottomInset)
                    .frame(maxWidth: .infinity)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .overlay(alignment: .topLeading) {
            Button {
                model.show(.idle)
            } label: {
                Label("Home", systemImage: "chevron.left")
            }
            .buttonStyle(MaterialButtonStyle(colors: .tonal, size: .medium, elevation: .level1))
            // Below the wake-word hint, which shares the top-left corner.
            .padding(.top, 88)
            .padding(.leading, 32)
        }
        .onAppear {
            autoReturn = AutoReturnTimer(timeout: NowPlayingRules.idleTimeout, views: [.nowPlaying]) {
                [model] in model.show(.idle)
            }
            updateAutoReturn()
        }
        .onDisappear { autoReturn = nil }
        .onChange(of: model.nowPlaying) { updateAutoReturn() }
        .onChange(of: model.currentView) { updateAutoReturn() }
    }

    @ViewBuilder
    private func content(in available: CGSize) -> some View {
        if let nowPlaying = model.nowPlaying {
            playing(nowPlaying, in: available)
        } else {
            nothingPlaying
        }
    }

    private var nothingPlaying: some View {
        VStack(spacing: 24) {
            Image(systemName: "music.note")
                .font(.system(size: 72, weight: .medium))
                .foregroundStyle(Theme.onSecondaryContainer)
                .frame(width: 160, height: 160)
                .m3Surface(Theme.secondaryContainer, shape: nil)
            Text("Nothing playing")
                .m3Font(.headlineMedium)
                .foregroundStyle(Theme.onSurfaceVariant)
            // Why Apple Music didn't start (#42), e.g. nothing found.
            if let notice = model.media.musicNotice {
                Text(notice)
                    .m3Font(.bodyLarge)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .multilineTextAlignment(.center)
            }
        }
    }

    private func updateAutoReturn() {
        autoReturn?.update(
            view: model.currentView, busy: !NowPlayingRules.shouldReturnToIdle(model.nowPlaying))
    }

    /// Side by side when the screen is wide (landscape), stacked when it is tall (portrait). The
    /// artwork takes the space the text and controls leave, within `artworkRange`, and a long
    /// title shrinks (to 60%) before it wraps past its line limit, so it never pushes the
    /// controls off screen or under the mic button.
    @ViewBuilder
    private func playing(_ nowPlaying: NowPlaying, in available: CGSize) -> some View {
        if available.width > available.height * 1.15 {
            let side = Self.clamp(min(available.height, available.width * 0.45))
            HStack(spacing: 56) {
                artwork(nowPlaying).frame(width: side, height: side)
                details(nowPlaying, alignment: .leading, titleLines: 3)
                    .frame(maxWidth: .infinity, maxHeight: available.height, alignment: .leading)
            }
        } else {
            let spacing: CGFloat = 24
            let side = Self.clamp(min(available.width, available.height - detailsHeight - spacing))
            VStack(spacing: spacing) {
                artwork(nowPlaying).frame(width: side, height: side)
                details(nowPlaying, alignment: .center, titleLines: 2)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { detailsHeight = $0 }
            }
        }
    }

    private static func clamp(_ side: CGFloat) -> CGFloat {
        min(max(side, artworkRange.lowerBound), artworkRange.upperBound)
    }

    private func artwork(_ nowPlaying: NowPlaying) -> some View {
        let shape = RoundedRectangle(cornerRadius: Theme.radius(.extraLarge), style: .continuous)
        return ZStack {
            shape.fill(Theme.primaryContainer)
            if let url = nowPlaying.artworkURL {
                // An absolute public CDN URL: `RemoteImage` never sends the Hub token there.
                RemoteImage(reference: url, contentMode: .fill)
            } else {
                Image(systemName: "music.note")
                    .font(.system(size: 120, weight: .medium))
                    .foregroundStyle(Theme.onPrimaryContainer)
            }
        }
        .clipShape(shape)
        .shadow(color: .black.opacity(0.12), radius: 18, y: 6)
        .accessibilityHidden(true)
    }

    private func details(
        _ nowPlaying: NowPlaying, alignment: HorizontalAlignment, titleLines: Int
    ) -> some View {
        VStack(alignment: alignment, spacing: 12) {
            Text(nowPlaying.title)
                .m3Font(.headlineMedium, weight: .bold)
                .foregroundStyle(Theme.onSurface)
                .lineLimit(titleLines)
                .minimumScaleFactor(0.6)
            if !nowPlaying.artist.isEmpty {
                Text(nowPlaying.artist)
                    .m3Font(.titleLarge, weight: .medium)
                    .foregroundStyle(Theme.primary)
                    .lineLimit(1)
            }
            if let album = nowPlaying.album {
                Text(album)
                    .m3Font(.bodyLarge)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .lineLimit(1)
            }
            statusChip(nowPlaying).padding(.top, 4)
            controls(nowPlaying).padding(.top, 16)
        }
        .multilineTextAlignment(alignment == .center ? .center : .leading)
    }

    private func statusChip(_ nowPlaying: NowPlaying) -> some View {
        Label(
            nowPlaying.isPlaying ? "Playing" : "Paused",
            systemImage: nowPlaying.isPlaying ? "speaker.wave.2.fill" : "pause.fill"
        )
        .m3Font(.labelMedium, weight: .medium)
        .labelStyle(MaterialLabelStyle())
        .foregroundStyle(Theme.onSecondaryContainer)
        .padding(.horizontal, 20)
        .frame(minHeight: 48)
        .m3Surface(Theme.secondaryContainer, shape: .medium)
    }

    private func controls(_ nowPlaying: NowPlaying) -> some View {
        let playPause = NowPlayingControl.playPause(for: nowPlaying)
        return HStack(spacing: 32) {
            controlButton(
                playPause, systemImage: playPause == .pause ? "pause.fill" : "play.fill",
                label: playPause == .pause ? "Pause" : "Play", colors: .filled, diameter: 120)
            controlButton(.next, systemImage: "forward.fill", label: "Next", colors: .tonal, diameter: 104)
        }
    }

    private func controlButton(
        _ control: NowPlayingControl, systemImage: String, label: String, colors: MaterialColors,
        diameter: CGFloat
    ) -> some View {
        Button {
            Task { await model.send(control) }
        } label: {
            Image(systemName: systemImage)
                .font(.system(size: 48, weight: .semibold))
        }
        .buttonStyle(MaterialIconButtonStyle(colors: colors, diameter: diameter, elevation: .level1))
        .accessibilityLabel(label)
    }
}

/// One line on the idle screen while music plays: artwork thumb · title — artist. Tapping it
/// opens the Now Playing screen.
struct NowPlayingStrip: View {
    let nowPlaying: NowPlaying
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: 16) {
                Group {
                    if let url = nowPlaying.artworkURL {
                        RemoteImage(reference: url, contentMode: .fill)
                    } else {
                        Image(systemName: "music.note")
                            .font(.system(size: 28))
                            .foregroundStyle(Theme.onPrimaryContainer)
                    }
                }
                .frame(width: 48, height: 48)
                .background(Theme.primaryContainer)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radius(.small), style: .continuous))
                Image(systemName: "speaker.wave.2.fill").foregroundStyle(Theme.primary)
                Text(NowPlayingRules.stripText(nowPlaying))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .padding(.leading, -14)
            .padding(.vertical, 8)
        }
        // An M3 assist chip with the artwork as its leading image.
        .buttonStyle(MaterialChipStyle())
        .frame(maxWidth: 820)
        .accessibilityLabel("Now playing: \(NowPlayingRules.stripText(nowPlaying))")
    }
}
