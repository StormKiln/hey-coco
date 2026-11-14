import CocoKit
import SwiftUI

/// `ViewSpec.nowPlaying` (#41): what the Hub is playing on its speakers, readable from across the
/// room. Pause/resume and next go to the Hub as touch utterances with `media_playing: music`, so
/// the Hub drives Spotify. Returns to idle after `NowPlayingRules.idleTimeout` with nothing
/// playing. The bottom centre stays clear for the mic button.
struct NowPlayingScreen: View {
    @Environment(AppModel.self) private var model
    @State private var autoReturn: AutoReturnTimer?

    var body: some View {
        Group {
            if let nowPlaying = model.nowPlaying {
                playing(nowPlaying)
            } else {
                VStack(spacing: 24) {
                    Image(systemName: "music.note")
                        .font(.system(size: 120))
                        .foregroundStyle(.secondary)
                    Text("Nothing playing")
                        .font(.system(size: 48, weight: .semibold, design: .rounded))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, 48)
        .padding(.bottom, 140)
        .overlay(alignment: .topLeading) {
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
            autoReturn = AutoReturnTimer(timeout: NowPlayingRules.idleTimeout, views: [.nowPlaying]) {
                [model] in model.show(.idle)
            }
            updateAutoReturn()
        }
        .onDisappear { autoReturn = nil }
        .onChange(of: model.nowPlaying) { updateAutoReturn() }
        .onChange(of: model.currentView) { updateAutoReturn() }
    }

    private func updateAutoReturn() {
        autoReturn?.update(
            view: model.currentView, busy: !NowPlayingRules.shouldReturnToIdle(model.nowPlaying))
    }

    @ViewBuilder
    private func playing(_ nowPlaying: NowPlaying) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 56) {
                artwork(nowPlaying).frame(width: 460, height: 460)
                details(nowPlaying, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            VStack(spacing: 36) {
                artwork(nowPlaying).frame(width: 420, height: 420)
                details(nowPlaying, alignment: .center)
            }
        }
    }

    private func artwork(_ nowPlaying: NowPlaying) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 24).fill(.quaternary)
            if let url = nowPlaying.artworkURL {
                // An absolute public CDN URL: `RemoteImage` never sends the Hub token there.
                RemoteImage(reference: url, contentMode: .fill)
            } else {
                Image(systemName: "music.note").font(.system(size: 140)).foregroundStyle(.secondary)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 24))
        .shadow(radius: 16, y: 8)
        .accessibilityHidden(true)
    }

    private func details(_ nowPlaying: NowPlaying, alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 14) {
            Text(nowPlaying.title)
                .font(.system(size: 60, weight: .bold, design: .rounded))
                .lineLimit(2)
                .minimumScaleFactor(0.6)
            Text(nowPlaying.artist)
                .font(.system(size: 42, weight: .medium, design: .rounded))
                .lineLimit(1)
            if let album = nowPlaying.album {
                Text(album)
                    .font(.system(size: 32, design: .rounded))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Label(
                nowPlaying.isPlaying ? "Playing" : "Paused",
                systemImage: nowPlaying.isPlaying ? "speaker.wave.2.fill" : "pause.fill"
            )
            .font(.title2)
            .foregroundStyle(.secondary)
            .padding(.top, 4)
            controls(nowPlaying).padding(.top, 20)
        }
        .multilineTextAlignment(alignment == .center ? .center : .leading)
    }

    private func controls(_ nowPlaying: NowPlaying) -> some View {
        let playPause = NowPlayingControl.playPause(for: nowPlaying)
        return HStack(spacing: 32) {
            controlButton(
                playPause, systemImage: playPause == .pause ? "pause.fill" : "play.fill",
                label: playPause == .pause ? "Pause" : "Play")
            controlButton(.next, systemImage: "forward.fill", label: "Next")
        }
    }

    private func controlButton(_ control: NowPlayingControl, systemImage: String, label: String) -> some View {
        Button {
            Task { await model.send(control) }
        } label: {
            Image(systemName: systemImage)
                .font(.system(size: 48, weight: .semibold))
                .frame(width: 120, height: 120)
                .background(.thinMaterial, in: Circle())
        }
        .buttonStyle(.plain)
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
                        Image(systemName: "music.note").font(.system(size: 28))
                    }
                }
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                Image(systemName: "speaker.wave.2.fill").foregroundStyle(.secondary)
                Text(NowPlayingRules.stripText(nowPlaying))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .font(.system(size: 30, design: .rounded))
            .padding(.leading, 12)
            .padding(.trailing, 28)
            .padding(.vertical, 10)
            .background(.thinMaterial, in: Capsule())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: 820)
        .accessibilityLabel("Now playing: \(NowPlayingRules.stripText(nowPlaying))")
    }
}
