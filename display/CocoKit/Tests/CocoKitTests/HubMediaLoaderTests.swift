import Foundation
import Testing
@testable import CocoKit

@Suite struct HubMediaLoaderTests {
    let host = "hub-\(UUID().uuidString.lowercased()).test"
    var endpoint: HubEndpoint { HubEndpoint(host: host, port: 8765, token: "tok") }

    private func makeLoader(cache: MediaCache? = nil) -> HubMediaLoader {
        HubMediaLoader(endpoint: endpoint, session: StubURLProtocol.session, cache: cache)
    }

    private func tempCache(maxFiles: Int = 64) -> MediaCache {
        MediaCache(
            directory: FileManager.default.temporaryDirectory
                .appendingPathComponent("coco-media-\(UUID().uuidString)"),
            maxFiles: maxFiles)
    }

    @Test func resolvesHubRelativePathsAgainstTheBaseURL() {
        let loader = makeLoader()
        #expect(
            loader.resolve("/v1/media/sounds/clips/cow.m4a")?.absoluteString
                == "http://\(host):8765/v1/media/sounds/clips/cow.m4a")
    }

    @Test func keepsAbsoluteURLsAndRejectsOtherSchemes() {
        let loader = makeLoader()
        let thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Cow.jpg/640px-Cow.jpg"
        #expect(loader.resolve(thumb)?.absoluteString == thumb)
        #expect(loader.resolve("file:///etc/passwd") == nil)
        #expect(loader.resolve("javascript:alert(1)") == nil)
        #expect(loader.resolve("") == nil)
    }

    @Test func sendsTheBearerTokenToTheHub() async throws {
        StubURLProtocol.register(host: host) { _ in (200, Data("clip".utf8)) }
        let data = try await makeLoader().data(for: "/v1/media/sounds/clips/cow.m4a")
        #expect(data == Data("clip".utf8))
        let request = try #require(StubURLProtocol.recorded(host: host).last)
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
        #expect(request.url?.path == "/v1/media/sounds/clips/cow.m4a")
    }

    @Test func neverSendsTheTokenToOtherHosts() async throws {
        let other = "pub-\(UUID().uuidString.lowercased()).test"
        StubURLProtocol.register(host: other) { _ in (200, Data("jpg".utf8)) }
        _ = try await makeLoader().data(for: "https://\(other)/a.jpg")
        // Same host, different port: still not the Hub.
        _ = try await makeLoader().data(for: "http://\(other):8765/b.jpg")
        let requests = StubURLProtocol.recorded(host: other)
        #expect(requests.count == 2)
        #expect(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == nil })
    }

    @Test func tokenIsNotSentToTheHubHostOnAnotherPort() async throws {
        StubURLProtocol.register(host: host) { _ in (200, Data()) }
        _ = try await makeLoader().data(for: "http://\(host):9999/x")
        let request = try #require(StubURLProtocol.recorded(host: host).last)
        #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test func mapsHTTPErrors() async {
        StubURLProtocol.register(host: host) { request in
            (request.url?.path == "/denied" ? 401 : 404, Data("nope".utf8))
        }
        let loader = makeLoader()
        await #expect(throws: HubError.unauthorized) { try await loader.data(for: "/denied") }
        await #expect(throws: HubError.server(code: "http_404", message: "nope")) {
            try await loader.data(for: "/missing")
        }
        await #expect(throws: HubError.self) { try await loader.data(for: "ftp://x/y") }
    }

    @Test func mapsNetworkFailures() async {
        StubURLProtocol.register(host: host) { _ in throw URLError(.cannotConnectToHost) }
        await #expect {
            try await makeLoader().data(for: "/v1/media/x.m4a")
        } throws: { error in
            if case .unreachable = error as? HubError { true } else { false }
        }
    }

    @Test func servesRepeatsFromTheDiskCache() async throws {
        StubURLProtocol.register(host: host) { _ in (200, Data("clip".utf8)) }
        let cache = tempCache()
        let loader = makeLoader(cache: cache)
        _ = try await loader.data(for: "/v1/media/sounds/clips/cow.m4a")
        let again = try await loader.data(for: "/v1/media/sounds/clips/cow.m4a")
        #expect(again == Data("clip".utf8))
        #expect(StubURLProtocol.recorded(host: host).count == 1)
    }

    @Test func failedResponsesAreNotCached() async throws {
        StubURLProtocol.register(host: host) { _ in (500, Data("boom".utf8)) }
        let cache = tempCache()
        let loader = makeLoader(cache: cache)
        _ = try? await loader.data(for: "/x")
        StubURLProtocol.register(host: host) { _ in (200, Data("ok".utf8)) }
        #expect(try await loader.data(for: "/x") == Data("ok".utf8))
    }

    @Test func cacheKeepsAtMostMaxFilesDroppingTheOldest() throws {
        let cache = tempCache(maxFiles: 2)
        let a = URL(string: "http://h/a")!
        let b = URL(string: "http://h/b")!
        let c = URL(string: "http://h/c")!
        cache.store(Data("a".utf8), for: a, at: Date(timeIntervalSince1970: 1))
        cache.store(Data("b".utf8), for: b, at: Date(timeIntervalSince1970: 2))
        cache.store(Data("c".utf8), for: c, at: Date(timeIntervalSince1970: 3))
        #expect(cache.data(for: a) == nil)
        #expect(cache.data(for: b) == Data("b".utf8))
        #expect(cache.data(for: c) == Data("c".utf8))
    }
}
