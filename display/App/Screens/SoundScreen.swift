import CocoKit
import SwiftUI

/// `ViewSpec.sound` (#35): a big picture, the name and caption (shown, never spoken: #94), the
/// clip played once on arrival, a replay button, and the attribution. Every sound view the Hub sends plays the clip, including an
/// identical resend for "play it again" (`MediaCenter.soundRequestCount`). Back to idle ~10 s
/// after the clip ends. The picture stays below the top chrome row and in from the Close button's
/// column; in landscape it sits beside the words.
struct SoundScreen: View {
    static let idleAfterPlayback: Duration = .seconds(10)

    let payload: ViewSpec.SoundPayload
    @Environment(AppModel.self) private var model
    @State private var player: SoundClipPlayer?
    @State private var countdown: IdleCountdown?
    /// The clip currently loaded into `player`.
    @State private var loadedClip: String?
    @State private var failed = false

    var body: some View {
        // The whole screen's shape picks the layout (the area inside the chrome is nearly square
        // in portrait).
        GeometryReader { geometry in
            Group {
                if geometry.size.width > geometry.size.height {
                    HStack(spacing: 48) {
                        picture
                        details
                            .frame(maxWidth: .infinity)
                    }
                    .padding(.leading, 48)
                    .padding(.trailing, ScreenChrome.closeColumn)
                } else {
                    VStack(spacing: 28) {
                        picture
                        details
                    }
                    // In from the Close button's column, and as far on the left to stay centred.
                    .padding(.horizontal, ScreenChrome.closeColumn)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(.top, ScreenChrome.top)
            // Clear of the mic button.
            .padding(.bottom, ScreenChrome.bottom)
        }
        .task(id: payload.clipURL) { await loadAndPlay() }
        .onChange(of: model.media.soundRequestCount) {
            // A resend of the clip that is already loaded; a new clip plays from the task above.
            if loadedClip == payload.clipURL { replay() }
        }
        .onChange(of: player?.finishCount) { countdown?.arm() }
        .onDisappear(perform: tearDown)
        .closeButton()
    }

    private var picture: some View {
        RemoteImage(reference: payload.imageURL, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius(.extraLarge), style: .continuous))
            .shadow(color: .black.opacity(0.12), radius: 18, y: 6)
            .frame(maxWidth: 640, maxHeight: 440)
            .accessibilityHidden(true)
    }

    /// Name, caption, Play again, and the attribution.
    private var details: some View {
        VStack(spacing: 24) {
            Text(payload.name.capitalized)
                .m3Font(.displaySmall, weight: .bold)
                .foregroundStyle(Theme.onSurface)
                .lineLimit(1)
                .minimumScaleFactor(0.5)
            // What Coco used to say over the clip (#94): shown, not spoken.
            if let caption = payload.caption {
                Text(caption)
                    .m3Font(.titleLarge)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.7)
            }
            Button(action: replay) {
                Label("Play again", systemImage: "arrow.counterclockwise")
                    .m3Font(.titleSmall)
            }
            .buttonStyle(MaterialButtonStyle(colors: .filled, elevation: .level1))
            .disabled(loadedClip == nil)
            if failed {
                Text("Couldn't play the sound.")
                    .m3Font(.bodyMedium)
                    .foregroundStyle(Theme.error)
            }
            Text(payload.attribution)
                .m3Font(.labelSmall, weight: .regular)
                .foregroundStyle(Theme.onSurfaceVariant)
                .multilineTextAlignment(.center)
                .lineLimit(2)
        }
    }

    private func setUp() -> SoundClipPlayer {
        if let player { return player }
        let player = SoundClipPlayer(media: model.media)
        model.media.register(sound: player)
        self.player = player
        countdown = IdleCountdown(timeout: Self.idleAfterPlayback) { [model] in
            if case .sound = model.currentView { model.show(.idle) }
        }
        return player
    }

    private func loadAndPlay() async {
        let player = setUp()
        countdown?.disarm()
        failed = false
        loadedClip = nil
        do {
            let data = try await loadMedia(payload.clipURL, with: model.mediaLoader)
            guard !Task.isCancelled else { return }
            try player.load(data)
            loadedClip = payload.clipURL
            player.replay()
        } catch {
            guard !Task.isCancelled else { return }
            failed = true
            // Nothing will play, so don't stay here forever.
            countdown?.arm()
        }
    }

    private func replay() {
        countdown?.disarm()
        player?.replay()
    }

    private func tearDown() {
        countdown?.disarm()
        if let player {
            player.stop()
            model.media.unregister(sound: player)
        }
        player = nil
        countdown = nil
        loadedClip = nil
    }
}
