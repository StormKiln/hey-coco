import Foundation

#if canImport(UIKit)
    import UIKit
#endif

/// One reading of how the device is holding up, for the voice test (#6).
public struct DeviceConditions: Sendable, Equatable {
    public enum Thermal: Int, Sendable, Comparable, CustomStringConvertible {
        case nominal, fair, serious, critical

        public static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }

        public var description: String {
            switch self {
            case .nominal: "nominal"
            case .fair: "fair"
            case .serious: "serious"
            case .critical: "critical"
            }
        }

        public init(_ state: ProcessInfo.ThermalState) {
            switch state {
            case .nominal: self = .nominal
            case .fair: self = .fair
            case .serious: self = .serious
            case .critical: self = .critical
            @unknown default: self = .critical
            }
        }
    }

    public enum BatteryState: String, Sendable {
        case unknown, unplugged, charging, full
    }

    public var thermal: Thermal?
    /// 0...1, `nil` where there is no battery to read (a Mac, the simulator).
    public var batteryLevel: Double?
    public var batteryState: BatteryState
    /// CPU time this process has used so far (user + system), in seconds.
    public var cpuSeconds: Double?

    public init(
        thermal: Thermal?, batteryLevel: Double?, batteryState: BatteryState, cpuSeconds: Double?
    ) {
        self.thermal = thermal
        self.batteryLevel = batteryLevel
        self.batteryState = batteryState
        self.cpuSeconds = cpuSeconds
    }
}

@MainActor
public protocol DeviceConditionsSource: AnyObject {
    func sample() -> DeviceConditions
}

/// `ProcessInfo.thermalState`, `UIDevice` battery (iOS only) and `getrusage` CPU time.
@MainActor
public final class LiveDeviceConditions: DeviceConditionsSource {
    public init() {}

    public func sample() -> DeviceConditions {
        var level: Double?
        var state = DeviceConditions.BatteryState.unknown
        #if canImport(UIKit)
            let device = UIDevice.current
            // Turned on for the whole app once the test starts; harmless and cheap.
            device.isBatteryMonitoringEnabled = true
            if device.batteryLevel >= 0 { level = Double(device.batteryLevel) }
            switch device.batteryState {
            case .unplugged: state = .unplugged
            case .charging: state = .charging
            case .full: state = .full
            case .unknown: state = .unknown
            @unknown default: state = .unknown
            }
        #endif
        return DeviceConditions(
            thermal: .init(ProcessInfo.processInfo.thermalState), batteryLevel: level,
            batteryState: state, cpuSeconds: Self.processCPUSeconds())
    }

    private static func processCPUSeconds() -> Double? {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        func seconds(_ t: timeval) -> Double { Double(t.tv_sec) + Double(t.tv_usec) / 1_000_000 }
        return seconds(usage.ru_utime) + seconds(usage.ru_stime)
    }
}
