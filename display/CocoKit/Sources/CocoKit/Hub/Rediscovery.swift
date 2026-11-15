import Foundation

/// When a Hub found with Bonjour counts as gone from its address (its IP changed, say after a
/// DHCP renewal) so discovery runs again. A manual host is never rediscovered.
public struct RediscoveryPolicy: Sendable, Equatable {
    /// Consecutive failed connection attempts to one address.
    public var maxFailures: Int
    /// Seconds since the first of those failures.
    public var maxOutage: TimeInterval

    public init(maxFailures: Int = 5, maxOutage: TimeInterval = 30) {
        self.maxFailures = maxFailures
        self.maxOutage = maxOutage
    }
}

/// Counts consecutive failures to reach one address; stale after `maxFailures` of them or
/// `maxOutage` seconds since the first, whichever comes first.
public struct OutageTracker: Sendable {
    public let policy: RediscoveryPolicy
    public private(set) var failures = 0
    public private(set) var firstFailure: Date?

    public init(policy: RediscoveryPolicy) { self.policy = policy }

    /// Records a failure at `now`; true when the address should be given up on.
    public mutating func failed(at now: Date) -> Bool {
        failures += 1
        if firstFailure == nil { firstFailure = now }
        return isStale(at: now)
    }

    public func isStale(at now: Date) -> Bool {
        guard let firstFailure else { return false }
        return failures >= policy.maxFailures || now.timeIntervalSince(firstFailure) >= policy.maxOutage
    }

    /// The address answered.
    public mutating func reset() {
        failures = 0
        firstFailure = nil
    }
}

extension ConnectionState {
    /// The banner for a manual host that doesn't answer: there is nothing to rediscover, so
    /// point the person at the Mac and Settings.
    public static func manualHostUnreachableMessage(host: String) -> String {
        "Can't reach \(host) — check the Mac. Tap for Settings."
    }
}
