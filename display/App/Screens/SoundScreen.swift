import CocoKit
import SwiftUI

/// `ViewSpec.sound` (#35): a big picture, the name and caption (shown, never spoken: #94), the
/// clip played once on arrival, a replay button, and the attribution. Every sound view the Hub sends plays the clip, including an
/// identical resend for "play it again" (`MediaCenter.soundRequestCount`). Back to idle ~10 s
/// after the clip ends.
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
        VStack(spacing: 28) {
            RemoteImage(reference: payload.imageURL, contentMode: .fit)
                .frame(maxWidth: 720, maxHeight: 520)
                .clipShape(RoundedRectangle(cornerRadius: 36, style: .continuous))
                .accessibilityHidden(true)
            Text(payload.name.capitalized)
                .font(.system(size: 72, weight: .bold, design: .rounded))
            // What Coco used to say over the clip (#94): shown, not spoken.
            if let caption = payload.caption {
                Text(caption)
                    .font(.system(size: 40, weight: .semibold, design: .rounded))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.7)
            }
            Button(action: replay) {
                Label("Play again", systemImage: "arrow.counterclockwise")
                    .font(.system(size: 32, weight: .semibold, design: .rounded))
                    .padding(.horizontal, 36)
                    .padding(.vertical, 18)
                    .background(.tint.opacity(0.15), in: Capsule())
            }
            .buttonStyle(.plain)
            .foregroundStyle(.tint)
            .disabled(loadedClip == nil)
            if failed {
                Text("Couldn't play the sound.")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            Text(payload.attribution)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
        }
        .padding(.horizontal, 48)
        // Clear of the mic button.
        .padding(.bottom, 140)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task(id: payload.clipURL) { await loadAndPlay() }
        .onChange(of: model.media.soundRequestCount) {
            // A resend of the clip that is already loaded; a new clip plays from the task above.
            if loadedClip == payload.clipURL { replay() }
        }
        .onChange(of: player?.finishCount) { countdown?.arm() }
        .onDisappear(perform: tearDown)
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
