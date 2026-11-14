import CocoKit
import SwiftUI
import WebKit

/// `ViewSpec.youtube` (#39): the video full screen in an embedded IFrame player, with a large
/// close button. Voice pause/resume/stop arrive as commands through `MediaCenter`; when the
/// video ends `MediaCenter.onFinished` returns to idle.
struct YouTubeScreen: View {
    let videoID: String
    let title: String
    @Environment(AppModel.self) private var model
    @State private var errorMessage: String?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let embed = YouTubeEmbed(videoID: videoID) {
                YouTubePlayerView(embed: embed, media: model.media, errorMessage: $errorMessage)
                    .ignoresSafeArea()
                    .accessibilityLabel(title)
            } else {
                message(YouTubeEmbed.errorMessage(code: 100))
            }
            if let errorMessage { message(errorMessage) }
        }
        .overlay(alignment: .topLeading) {
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(size: 34, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 88, height: 88)
                    .background(.black.opacity(0.55), in: Circle())
            }
            .padding(24)
            .accessibilityLabel("Close video")
        }
    }

    private func message(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 40, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .multilineTextAlignment(.center)
            .padding(48)
    }

    private func close() {
        model.media.stop(.youtube)
        model.show(.idle)
    }
}

/// The `WKWebView` running `YouTubeEmbed`'s page. Its coordinator is the `YouTubeControlling`
/// player registered with `MediaCenter` for as long as the view exists.
struct YouTubePlayerView: UIViewRepresentable {
    /// The page's origin. YouTube checks the embedding origin/referrer; an app-specific https
    /// origin avoids the "video player configuration error" seen with a missing one.
    static let origin = URL(string: "https://\((Bundle.main.bundleIdentifier ?? "heycoco").lowercased())")!

    let embed: YouTubeEmbed
    let media: MediaCenter
    @Binding var errorMessage: String?

    func makeCoordinator() -> Coordinator { Coordinator(media: media) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.allowsPictureInPictureMediaPlayback = false
        configuration.userContentController.add(
            WeakScriptMessageHandler(context.coordinator), name: YouTubeEmbed.messageHandlerName)
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        context.coordinator.webView = webView
        context.coordinator.onError = { errorMessage = $0 }
        media.register(youtube: context.coordinator)
        context.coordinator.load(embed)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.onError = { errorMessage = $0 }
        if context.coordinator.loaded != embed {
            errorMessage = nil
            context.coordinator.load(embed)
        }
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: YouTubeEmbed.messageHandlerName)
        webView.evaluateJavaScript(YouTubeEmbed.script(.stop))
        webView.loadHTMLString("", baseURL: nil)
        coordinator.media.unregister(youtube: coordinator)
    }

    @MainActor
    final class Coordinator: NSObject, YouTubeControlling, WKScriptMessageHandler {
        let media: MediaCenter
        weak var webView: WKWebView?
        var onError: ((String) -> Void)?
        private(set) var loaded: YouTubeEmbed?
        private var gain = 1.0

        init(media: MediaCenter) { self.media = media }

        func load(_ embed: YouTubeEmbed) {
            loaded = embed
            webView?.loadHTMLString(embed.html(origin: YouTubePlayerView.origin), baseURL: YouTubePlayerView.origin)
        }

        // MARK: YouTubeControlling

        func pause() { run(.pause) }
        func resume() { run(.play) }
        func stop() { run(.stop) }
        func setGain(_ gain: Double) {
            self.gain = gain
            run(.setGain(gain))
        }

        private func run(_ control: YouTubeEmbed.Control) {
            webView?.evaluateJavaScript(YouTubeEmbed.script(control))
        }

        // MARK: WKScriptMessageHandler

        func userContentController(
            _ userContentController: WKUserContentController, didReceive message: WKScriptMessage
        ) {
            guard let body = message.body as? [String: Any], let event = body["event"] as? String else { return }
            let code = (body["data"] as? NSNumber)?.intValue
            switch event {
            case "ready":
                run(.setGain(gain))
            case "state":
                code.flatMap(YouTubePlayerState.init(code:))?.report(to: media)
            case "error":
                onError?(YouTubeEmbed.errorMessage(code: code ?? -1))
            default:
                break
            }
        }
    }
}

/// `WKUserContentController` retains its handlers; this breaks the cycle with the coordinator.
private final class WeakScriptMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: (any WKScriptMessageHandler)?

    init(_ target: any WKScriptMessageHandler) { self.target = target }

    func userContentController(
        _ userContentController: WKUserContentController, didReceive message: WKScriptMessage
    ) {
        target?.userContentController(userContentController, didReceive: message)
    }
}
