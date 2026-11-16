import Foundation
import Observation

/// Settings' "Home location" (#96): where the Hub gets home weather for.
///
/// Find sends the typed name to the Hub, which looks it up and saves the best match at once;
/// the other matches (`choices`) let the person pick a different one. "Use the Hub's default"
/// goes back to the location in the Hub's config file. Settings is adult territory already, so
/// there is no extra lock.
@MainActor
@Observable
public final class HomeLocationModel {
    public enum Status: Sendable, Equatable {
        case idle, loading, saving
        case failed(String)
    }

    public private(set) var status: Status = .idle
    /// What the Hub uses now; `nil` until loaded.
    public private(set) var settings: LocationSettings?
    /// After Find: the saved match first, then the other matches. Empty otherwise.
    public private(set) var choices: [HomeLocation] = []
    /// The last name fitted more than one sizeable place: worth checking the pick.
    public private(set) var ambiguous = false
    /// The place name being typed.
    public var query = ""

    public init() {}

    /// The Settings row: "Portland, Oregon", "Home (Hub default)", "Not set".
    public var summary: String {
        guard let settings else { return "…" }
        guard let location = settings.location else { return "Not set" }
        return switch settings.source {
        case .override: location.label
        case .config: "\(location.label) (Hub default)"
        }
    }

    public var isBusy: Bool { status == .loading || status == .saving }

    public var canFind: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isBusy
    }

    /// Only an overridden location has a default to go back to.
    public var canUseHubDefault: Bool { settings?.source == .override && !isBusy }

    public func isCurrent(_ place: HomeLocation) -> Bool {
        settings?.location == place
    }

    /// `api` is `nil` while no Hub is connected.
    public func load(using api: (any HubAPI)?) async {
        guard let api else {
            status = .failed(Self.notConnected)
            return
        }
        status = .loading
        do throws(HubError) {
            settings = try await api.location()
            status = .idle
        } catch {
            status = .failed(Self.message(error))
        }
    }

    /// Looks up `query` and saves the best match.
    public func find(using api: (any HubAPI)?) async {
        let name = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        guard let api else {
            status = .failed(Self.notConnected)
            return
        }
        status = .saving
        do throws(HubError) {
            let result = try await api.setLocation(query: name)
            settings = result
            choices = result.location.map { [$0] + result.alternatives } ?? []
            ambiguous = result.ambiguous
            status = .idle
        } catch {
            choices = []
            ambiguous = false
            status = .failed(Self.message(error))
        }
    }

    /// Saves one of `choices` instead.
    public func pick(_ place: HomeLocation, using api: (any HubAPI)?) async {
        guard let api else {
            status = .failed(Self.notConnected)
            return
        }
        status = .saving
        do throws(HubError) {
            settings = try await api.setLocation(place: place)
            ambiguous = false
            status = .idle
        } catch {
            status = .failed(Self.message(error))
        }
    }

    /// Back to the location in the Hub's config file.
    public func useHubDefault(using api: (any HubAPI)?) async {
        guard let api else {
            status = .failed(Self.notConnected)
            return
        }
        status = .saving
        do throws(HubError) {
            settings = try await api.resetLocation()
            choices = []
            ambiguous = false
            query = ""
            status = .idle
        } catch {
            status = .failed(Self.message(error))
        }
    }

    static let notConnected = "Not connected to Coco Hub."

    /// The Hub's own words for its refusals ("Coco couldn't find …"); otherwise as diagnostics.
    static func message(_ error: HubError) -> String {
        if case .server(let code, let message) = error, !code.hasPrefix("http_") {
            return message
        }
        return DiagnosticsFormat.error(error)
    }
}
