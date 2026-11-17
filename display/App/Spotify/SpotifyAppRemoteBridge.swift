import CocoKit
import Foundation
import SpotifyiOS

/// `SpotifyAppRemoting` with the Spotify iOS SDK (#116, docs/spotify.md): `authorizeAndPlayURI`
/// opens Spotify, starts the URI, and Spotify redirects back to `SpotifyWake.redirectURL`, which
/// brings Coco to the front again. The decisions (Guided Access, fallback to the plain
/// `spotify:` scheme) are CocoKit's `SpotifyWaker`; this only talks to the SDK.
///
/// Coco never connects the App Remote or keeps the access token: the redirect is the point.
@MainActor
final class SpotifyAppRemoteBridge: SpotifyAppRemoting {
    private var remote: SPTAppRemote?
    private var clientID: String?

    func authorizeAndPlay(
        clientID: String, uri: String, redirectURL: URL,
        completion: @escaping @MainActor @Sendable (Bool) -> Void
    ) {
        if remote == nil || self.clientID != clientID {
            let configuration = SPTConfiguration(clientID: clientID, redirectURL: redirectURL)
            remote = SPTAppRemote(configuration: configuration, logLevel: .error)
            self.clientID = clientID
        }
        // The SDK may call back on any thread: an explicitly @Sendable (non-isolated) block,
        // then hop to the main actor.
        remote?.authorizeAndPlayURI(uri, completionHandler: { @Sendable reachable in
            Task { @MainActor in completion(reachable) }
        })
    }

    func authorizationResult(from url: URL) -> SpotifyAuthorizationResult? {
        guard let remote, let parameters = remote.authorizationParameters(from: url) else { return nil }
        if parameters[SPTAppRemoteAccessTokenKey] != nil { return .authorized }
        return .failed(parameters[SPTAppRemoteErrorDescriptionKey] ?? "no access token in the redirect")
    }
}
