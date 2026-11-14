import CocoKit
import SwiftUI
import UIKit

/// Loads media bytes: through the Hub loader when connected (adds the token for Hub paths only),
/// else straight from public absolute URLs.
@MainActor
func loadMedia(_ reference: String, with loader: HubMediaLoader?) async throws -> Data {
    if let loader { return try await loader.data(for: reference) }
    guard let url = URL(string: reference), url.scheme == "https" || url.scheme == "http" else {
        throw HubError.unreachable("Not connected to Coco Hub.")
    }
    let (data, response) = try await URLSession.shared.data(from: url)
    guard let status = (response as? HTTPURLResponse)?.statusCode, (200..<300).contains(status) else {
        throw HubError.server(code: "http_error", message: url.absoluteString)
    }
    return data
}

/// An image fetched with `loadMedia` (not `AsyncImage`: Hub images need the bearer header).
struct RemoteImage: View {
    let reference: String
    var contentMode: ContentMode = .fit
    @Environment(AppModel.self) private var model
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                    .transition(.opacity)
            } else if failed {
                Image(systemName: "photo")
                    .font(.system(size: 96))
                    .foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.large)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: image)
        .task(id: reference) {
            image = nil
            failed = false
            do {
                let data = try await loadMedia(reference, with: model.mediaLoader)
                guard !Task.isCancelled else { return }
                if let decoded = UIImage(data: data) { image = decoded } else { failed = true }
            } catch {
                if !Task.isCancelled { failed = true }
            }
        }
    }
}
