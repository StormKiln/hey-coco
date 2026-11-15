import Foundation

/// Turns a Wikimedia Commons file title into a caption a person would write.
///
/// Commons titles are file names: "File:006 Giraffe portrait in the Lake Manyara National Park
/// Photo by Giles Laurent.jpg", "IMG_1234.JPG". The author is credited separately on screen, so
/// photographer credits are removed rather than repeated.
public enum PictureCaption {
    /// Captions longer than this are cut at a word boundary with "…".
    public static let maxLength = 60

    /// The tidied caption, or `fallback` (capitalised: the search query, e.g. "giraffe" →
    /// "Giraffe") when nothing readable is left or the title is a camera file name.
    public static func tidy(_ title: String, author: String? = nil, fallback: String) -> String {
        var text = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("File:") { text = String(text.dropFirst(5)) }
        text = removingExtension(text)
        text = text.replacingOccurrences(of: "_", with: " ")
        text = collapsingSpaces(text)
        if looksLikeCameraFile(text) { return fallbackCaption(fallback) }

        // Trailing credits: "… Photo by Giles Laurent", "… (photo: X)", "… © X". A bare "by" is
        // only a credit when the author follows it ("Red fox by Jane Doe"); "standing by the
        // lake" stays.
        text = replacing(#"[\s,;\-–—(]*\(?\s*((photo(graph)?|image|picture)\s*(by|:)|©|\(c\))\s*.*$"#, in: text)
        if let author = author?.trimmingCharacters(in: .whitespacesAndNewlines), author.count >= 3 {
            let name = NSRegularExpression.escapedPattern(for: author)
            text = replacing(#"[\s,;\-–—(]*\bby\s+"# + name + ".*$", in: text)
            // The author's name anywhere else (often appended: "Red fox Jane Doe").
            text = text.replacingOccurrences(of: author, with: "", options: [.caseInsensitive])
        }
        // Leading numbering: "006 ", "12. ", "3 - ", "IMG_1234 " / "DSC01234 ".
        text = replacing(#"^((IMG|DSC|DSCN|DSCF|P|PXL|DJI)[ _-]?\d+|\d+)\s*[.)\-–—:]?\s+"#, in: text)
        // Trailing camera/serial numbers and dates: "Giraffe 2019-05-04", "Giraffe (3)".
        text = replacing(#"\s*\(\d+\)$"#, in: text)
        text = replacing(#"\s+\d{4}[-.]\d{2}[-.]\d{2}$"#, in: text)
        text = collapsingSpaces(text).trimmingCharacters(in: CharacterSet(charactersIn: " ,;:-–—("))

        guard text.rangeOfCharacter(from: .letters) != nil, !looksLikeCameraFile(text) else {
            return fallbackCaption(fallback)
        }
        return truncated(text)
    }

    static func truncated(_ text: String) -> String {
        guard text.count > maxLength else { return text }
        let limit = text.index(text.startIndex, offsetBy: maxLength - 1)
        var cut = String(text[..<limit])
        if let space = cut.lastIndex(of: " "), cut.distance(from: cut.startIndex, to: space) >= maxLength / 2 {
            cut = String(cut[..<space])
        }
        return cut.trimmingCharacters(in: CharacterSet(charactersIn: " ,;:-–—(")) + "…"
    }

    /// "IMG 1234", "DSC01234", "PXL 20230101 123456789", "20190504 123456": a camera's name.
    static func looksLikeCameraFile(_ text: String) -> Bool {
        let pattern = #"^((IMG|DSC|DSCN|DSCF|DCIM|P|PXL|DJI|GOPR|MVIMG|Photo)[ _-]?)?[\d _\-.]+(MP)?$"#
        return text.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func removingExtension(_ text: String) -> String {
        replacing(#"\.(jpe?g|png|gif|tiff?|webp|svg|heic|bmp)$"#, in: text)
    }

    private static func fallbackCaption(_ query: String) -> String {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = trimmed.first else { return "" }
        return first.uppercased() + trimmed.dropFirst()
    }

    private static func collapsingSpaces(_ text: String) -> String {
        replacing(#"\s{2,}"#, in: text, with: " ").trimmingCharacters(in: .whitespaces)
    }

    private static func replacing(_ pattern: String, in text: String, with replacement: String = "") -> String {
        text.replacingOccurrences(
            of: pattern, with: replacement, options: [.regularExpression, .caseInsensitive])
    }
}
