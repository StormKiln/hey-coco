import CryptoKit
import Foundation

/// Fetches media the Hub refers to: Hub-relative paths (`/v1/media/sounds/clips/cow.m4a`) are
/// resolved against the Hub's base URL and fetched with `Authorization: Bearer`; absolute public
/// URLs (Wikimedia pictures) are fetched as they are. The token only ever goes to the Hub's own
/// origin (scheme, host and port), never to another host.
///
/// Because the Hub's media needs the header, screens can't use `AsyncImage` or a plain
/// `AVPlayer(url:)`; they load bytes through this and play/show them from memory.
public struct HubMediaLoader: Sendable {
    public let endpoint: HubEndpoint
    public let session: URLSession
    public let cache: MediaCache?
    public var timeout: TimeInterval = 20

    public init(endpoint: HubEndpoint, session: URLSession = .shared, cache: MediaCache? = .standard) {
        self.endpoint = endpoint
        self.session = session
        self.cache = cache
    }

    /// The absolute http(s) URL for `reference`, or `nil` if it is empty or another scheme.
    public func resolve(_ reference: String) -> URL? {
        guard !reference.isEmpty,
            let url = URL(string: reference, relativeTo: endpoint.baseURL)?.absoluteURL,
            let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
            url.host != nil
        else { return nil }
        return url
    }

    /// True when `url` is the Hub itself, so it may carry the token.
    public func isHub(_ url: URL) -> Bool {
        let base = endpoint.baseURL
        return url.scheme?.lowercased() == base.scheme
            && url.host?.lowercased() == base.host?.lowercased()
            && (url.port ?? 80) == endpoint.port
    }

    public func request(for url: URL) -> URLRequest {
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        if isHub(url) {
            request.setValue("Bearer \(endpoint.token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    /// The bytes behind `reference`, from the disk cache when possible.
    public func data(for reference: String) async throws(HubError) -> Data {
        guard let url = resolve(reference) else {
            throw .server(code: "bad_media_url", message: "Not a media URL: \(reference)")
        }
        if let cached = cache?.data(for: url) { return cached }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request(for: url))
        } catch {
            throw .unreachable(error.localizedDescription)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        switch status {
        case 200..<300:
            cache?.store(data, for: url)
            return data
        case 401, 403:
            throw .unauthorized
        default:
            throw .server(code: "http_\(status)", message: String(decoding: data, as: UTF8.self))
        }
    }
}

/// A small on-disk cache: one file per URL, at most `maxFiles`, least recently used dropped
/// first. Best effort: any file error is a cache miss.
public struct MediaCache: Sendable {
    public let directory: URL
    public let maxFiles: Int

    public init(directory: URL, maxFiles: Int = 64) {
        self.directory = directory
        self.maxFiles = maxFiles
    }

    /// `Caches/CocoMedia`.
    public static let standard = MediaCache(
        directory: FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("CocoMedia", isDirectory: true))

    public func data(for url: URL) -> Data? {
        let file = file(for: url)
        guard let data = try? Data(contentsOf: file) else { return nil }
        // Mark as recently used.
        try? FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: file.path)
        return data
    }

    public func store(_ data: Data, for url: URL, at date: Date = Date()) {
        let manager = FileManager.default
        try? manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = file(for: url)
        guard (try? data.write(to: file, options: .atomic)) != nil else { return }
        try? manager.setAttributes([.modificationDate: date], ofItemAtPath: file.path)
        trim()
    }

    private func file(for url: URL) -> URL {
        let digest = SHA256.hash(data: Data(url.absoluteString.utf8))
        let name = digest.map { String(format: "%02x", $0) }.joined()
        let ext = url.pathExtension
        return directory.appendingPathComponent(ext.isEmpty ? name : "\(name).\(ext)")
    }

    private func trim() {
        let manager = FileManager.default
        guard
            let files = try? manager.contentsOfDirectory(
                at: directory, includingPropertiesForKeys: [.contentModificationDateKey]),
            files.count > maxFiles
        else { return }
        func modified(_ url: URL) -> Date {
            (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate)
                ?? .distantPast
        }
        let oldestFirst = files.sorted { modified($0) < modified($1) }
        for file in oldestFirst.prefix(files.count - maxFiles) {
            try? manager.removeItem(at: file)
        }
    }
}
