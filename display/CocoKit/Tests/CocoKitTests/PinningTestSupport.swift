import Foundation
import Synchronization
@testable import CocoKit

/// Two self-signed EC P-256 certificates made by the Hub's own generator
/// (`coco_hub.identity`), DER, with their SHA-256 and short fingerprints.
enum TestCerts {
    static let hubA = Data(base64Encoded: """
        MIIBhzCCAS6gAwIBAgIUDSV+SEwj2ClE2D/REC/TY4HVku4wCgYIKoZIzj0EAwIwJDEiMCAGA1UEAwwZQ29jbyBIdWIgM2JmZTExODlhMWFlYTc5ZDAeFw0yNjExMTMxNzA0NDhaFw00NjExMDkxNzA0NDhaMCQxIjAgBgNVBAMMGUNvY28gSHViIDNiZmUxMTg5YTFhZWE3OWQwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARlJ1uyJiMqENOz4nr4CRfc5iW52VZPTeVWkDwpwG+jbuVJvHPPEx7ISLZxFkKT/HVxQb9aPagQehjWOp9KJNwIoz4wPDAsBgNVHREEJTAjgglsb2NhbGhvc3SHBH8AAAGHEAAAAAAAAAAAAAAAAAAAAAEwDAYDVR0TAQH/BAIwADAKBggqhkjOPQQDAgNHADBEAiBtQ3LrqJboYEsBUMDj8QEfL+OwKracn1ozDqU1TQ/6PAIgDZb3OTd+ziSfZqhs0iVM0Im5sEsvLGwwc/M5T919FpI=
        """, options: .ignoreUnknownCharacters)!
    static let hubASHA256Hex = "986b1f9c5b9d9174cec25439c644aaf440ef0e107b3a36be5625c7f0fcb88922"
    static let hubAShort = "986B-1F9C-5B9D-9174-CEC2-5439"

    static let hubB = Data(base64Encoded: """
        MIIBhzCCAS6gAwIBAgIUSJTQzaY6t2ak5eC+NcgTdk4J8gkwCgYIKoZIzj0EAwIwJDEiMCAGA1UEAwwZQ29jbyBIdWIgMzEyZWU4Zjc1M2VlZDI5OTAeFw0yNjExMTMxNzA0NDhaFw00NjExMDkxNzA0NDhaMCQxIjAgBgNVBAMMGUNvY28gSHViIDMxMmVlOGY3NTNlZWQyOTkwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQUyXUuJuslJz9dm6JR1ms7Zc2eHyahSb/2wvFgrfx0PYwbGmYp3eGNDagL39gbvzMRFJCy+ylrF1HfXRIJlHq2oz4wPDAsBgNVHREEJTAjgglsb2NhbGhvc3SHBH8AAAGHEAAAAAAAAAAAAAAAAAAAAAEwDAYDVR0TAQH/BAIwADAKBggqhkjOPQQDAgNHADBEAiBJ0Kt6KYhBvA4khnwEy7v6SBc37q8SvcpNB2HX1ORRlAIgT5AZ4dbRaJj39iBPmWT29tjFPy/GJa6EcQ0VfbGmloE=
        """, options: .ignoreUnknownCharacters)!
}

extension HubPin {
    /// The pin for `TestCerts.hubA`.
    static let test = HubPin(hubID: "3bfe1189a1aea79d", certSHA256: HubFingerprint.sha256(TestCerts.hubA))
    /// Another Hub (`TestCerts.hubB`).
    static let other = HubPin(hubID: "312ee8f753eed299", certSHA256: HubFingerprint.sha256(TestCerts.hubB))
}

/// Answers `identify` per host (default: the test Hub), and records which hosts were probed.
final class FakeHubProbe: HubProbe {
    private let results: [String: Result<HubPin, HubProbeError>]
    private let fallback: Result<HubPin, HubProbeError>
    let probed = Mutex<[String]>([])

    init(
        _ results: [String: Result<HubPin, HubProbeError>] = [:],
        default fallback: Result<HubPin, HubProbeError> = .success(.test)
    ) {
        self.results = results
        self.fallback = fallback
    }

    func identify(_ address: HubAddress) async -> Result<HubPin, HubProbeError> {
        probed.withLock { $0.append(address.host) }
        return results[address.host] ?? fallback
    }
}

/// Sleeps for no time, so retry loops in tests run immediately (yielding once).
struct InstantSleeper: Sleeper {
    func sleep(for duration: Duration) async throws {
        try Task.checkCancellation()
        await Task.yield()
    }
}
