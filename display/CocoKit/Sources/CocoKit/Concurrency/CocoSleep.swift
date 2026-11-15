import Foundation

/// The real sleep, for `sleep:` parameters that tests replace.
///
/// Use this constant as the default value instead of writing the closure inline in an
/// initializer's default argument. On the Xcode 26.x toolchain an async closure literal used as a
/// default argument of a `@MainActor` class's initializer aborted at runtime ("freed pointer was
/// not the last allocation") when the task sleeping in it was cancelled. A shared constant (or a
/// closure passed explicitly) does not.
public let cocoRealSleep: @Sendable (Duration) async throws -> Void = { duration in
    try await Task.sleep(for: duration)
}
