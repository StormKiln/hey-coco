import Foundation

/// The page the YouTube screen loads into a `WKWebView`: the embed iframe
/// (`/embed/<id>?playsinline=1&enablejsapi=1&rel=0&modestbranding=1&autoplay=1`) driven by the
/// YouTube IFrame Player API, with a small bridge:
/// - JS → app: `webkit.messageHandlers.coco.postMessage({event, data})` for `ready`,
///   `state` (IFrame `onStateChange` code) and `error` (IFrame error code).
/// - app → JS: `cocoPlay()`, `cocoPause()`, `cocoStop()`, `cocoSetVolume(0–100)` via `script(_:)`.
public struct YouTubeEmbed: Sendable, Equatable {
    public static let messageHandlerName = "coco"

    public let videoID: String

    /// `nil` unless `videoID` looks like a YouTube id (letters, digits, `-`, `_`), so it can be
    /// put into the page and URL without escaping.
    public init?(videoID: String) {
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        guard (1...64).contains(videoID.count), videoID.allSatisfy(allowed.contains) else { return nil }
        self.videoID = videoID
    }

    public func embedURL(origin: URL) -> URL {
        var components = URLComponents(string: "https://www.youtube.com/embed/\(videoID)")!
        components.queryItems = [
            URLQueryItem(name: "playsinline", value: "1"),
            URLQueryItem(name: "enablejsapi", value: "1"),
            URLQueryItem(name: "rel", value: "0"),
            URLQueryItem(name: "modestbranding", value: "1"),
            URLQueryItem(name: "autoplay", value: "1"),
            URLQueryItem(name: "origin", value: origin.absoluteString.trimmingSuffix("/")),
        ]
        return components.url!
    }

    /// Load with `loadHTMLString(_:baseURL: origin)` so the embed's `origin` matches the page.
    public func html(origin: URL) -> String {
        let src = embedURL(origin: origin).absoluteString.replacingOccurrences(of: "&", with: "&amp;")
        let handler = Self.messageHandlerName
        return """
            <!doctype html>
            <html><head>
            <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
            <style>
            html,body{margin:0;padding:0;width:100%;height:100%;background:#000;overflow:hidden}
            #player{position:absolute;top:0;left:0;width:100%;height:100%;border:0}
            </style>
            </head><body>
            <iframe id="player" src="\(src)" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
            <script>
            function post(m){try{window.webkit.messageHandlers.\(handler).postMessage(m)}catch(e){}}
            var player=null;
            function onYouTubeIframeAPIReady(){
              player=new YT.Player('player',{events:{
                onReady:function(e){post({event:'ready'});e.target.playVideo();},
                onStateChange:function(e){post({event:'state',data:e.data});},
                onError:function(e){post({event:'error',data:e.data});}
              }});
            }
            function cocoPlay(){if(player&&player.playVideo){player.playVideo();}}
            function cocoPause(){if(player&&player.pauseVideo){player.pauseVideo();}}
            function cocoStop(){if(player&&player.stopVideo){player.stopVideo();}}
            function cocoSetVolume(v){if(player&&player.setVolume){player.setVolume(v);}}
            </script>
            <script src="https://www.youtube.com/iframe_api"></script>
            </body></html>
            """
    }

    public enum Control: Sendable, Equatable {
        case play, pause, stop
        /// 0...1, clamped.
        case setGain(Double)
    }

    /// JavaScript for a control, for `WKWebView.evaluateJavaScript`.
    public static func script(_ control: Control) -> String {
        switch control {
        case .play: "cocoPlay();"
        case .pause: "cocoPause();"
        case .stop: "cocoStop();"
        case .setGain(let gain):
            "cocoSetVolume(\(Int((min(1, max(0, gain)) * 100).rounded())));"
        }
    }

    /// Something to show for an IFrame API error code.
    public static func errorMessage(code: Int) -> String {
        switch code {
        case 101, 150: "This video can't be played here."
        case 100: "That video isn't available."
        default: "The video couldn't be played."
        }
    }
}

/// IFrame Player API `onStateChange` codes.
public enum YouTubePlayerState: Int, Sendable, Equatable {
    case unstarted = -1
    case ended = 0
    case playing = 1
    case paused = 2
    case buffering = 3
    case cued = 5

    public init?(code: Int) { self.init(rawValue: code) }

    /// Tells `MediaCenter` about a state change. Buffering and cueing change nothing.
    @MainActor
    public func report(to media: MediaCenter) {
        switch self {
        case .playing: media.playerStarted(.youtube)
        case .paused: media.playerPaused(.youtube)
        case .ended: media.playerEnded(.youtube)
        case .unstarted, .buffering, .cued: break
        }
    }
}

extension String {
    fileprivate func trimmingSuffix(_ suffix: String) -> String {
        hasSuffix(suffix) ? String(dropLast(suffix.count)) : self
    }
}
