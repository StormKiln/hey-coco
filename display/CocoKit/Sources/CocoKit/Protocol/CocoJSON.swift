import Foundation

/// JSON coding configured for the Hub ↔ Display protocol (`docs/protocol/README.md`).
///
/// Keys are mapped explicitly with `CodingKeys` on each model, so only dates need configuring:
/// ISO 8601 UTC with a `Z` suffix and whole seconds (`2026-11-14T15:04:05Z`).
public enum CocoJSON {
    public static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    public static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}

/// Reads the `type` discriminator shared by views, commands and events.
enum TypeKey: String, CodingKey {
    case type
}
