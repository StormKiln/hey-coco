import Foundation

/// An sRGB colour, channels 0...1. UI-free so the theme maths runs under `swift test` (#113);
/// the app turns it into a SwiftUI `Color`.
public struct RGBColor: Equatable, Hashable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    /// `0xRRGGBB`.
    public init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255)
    }

    public var hex: UInt32 {
        func byte(_ value: Double) -> UInt32 { UInt32((min(max(value, 0), 1) * 255).rounded()) }
        return byte(red) << 16 | byte(green) << 8 | byte(blue)
    }

    /// WCAG 2 relative luminance (0 black ... 1 white).
    public var relativeLuminance: Double {
        0.2126 * Self.linear(red) + 0.7152 * Self.linear(green) + 0.0722 * Self.linear(blue)
    }

    /// WCAG 2 contrast ratio, 1...21, either order.
    public static func contrastRatio(_ a: RGBColor, _ b: RGBColor) -> Double {
        let (la, lb) = (a.relativeLuminance, b.relativeLuminance)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    // MARK: - CIE LCh(ab), D65

    /// CIE L*C*h. L* is what Material 3 calls tone (0 black ... 100 white).
    public struct LCh: Equatable, Sendable {
        public var l: Double
        public var c: Double
        /// Degrees, 0..<360.
        public var h: Double

        public init(l: Double, c: Double, h: Double) {
            self.l = l
            self.c = c
            self.h = h
        }
    }

    public var lch: LCh {
        let (r, g, b) = (Self.linear(red), Self.linear(green), Self.linear(blue))
        let x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / Self.white.x
        let y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / Self.white.y
        let z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / Self.white.z
        let (fx, fy, fz) = (Self.labF(x), Self.labF(y), Self.labF(z))
        let l = 116 * fy - 16
        let a = 500 * (fx - fy)
        let bb = 200 * (fy - fz)
        let c = (a * a + bb * bb).squareRoot()
        var h = atan2(bb, a) * 180 / .pi
        if h < 0 { h += 360 }
        return LCh(l: max(l, 0), c: c, h: c < 1e-9 ? 0 : h)
    }

    /// The colour at `lch`, clipped to sRGB. Use `inGamut(lch:)` to check first.
    public init(lch: LCh) {
        let (r, g, b) = Self.linearRGB(lch)
        self.init(red: Self.gamma(min(max(r, 0), 1)), green: Self.gamma(min(max(g, 0), 1)),
                  blue: Self.gamma(min(max(b, 0), 1)))
    }

    /// Whether `lch` is a real sRGB colour (no channel clipped).
    public static func inGamut(_ lch: LCh) -> Bool {
        let (r, g, b) = linearRGB(lch)
        let tolerance = 1e-7
        return [r, g, b].allSatisfy { $0 >= -tolerance && $0 <= 1 + tolerance }
    }

    private static let white = (x: 0.95047, y: 1.0, z: 1.08883)

    private static func linearRGB(_ lch: LCh) -> (Double, Double, Double) {
        let radians = lch.h * .pi / 180
        let a = lch.c * cos(radians), b = lch.c * sin(radians)
        let fy = (lch.l + 16) / 116
        let fx = fy + a / 500
        let fz = fy - b / 200
        let x = labFInverse(fx) * white.x
        let y = labFInverse(fy) * white.y
        let z = labFInverse(fz) * white.z
        return (
            3.2404542 * x - 1.5371385 * y - 0.4985314 * z,
            -0.9692660 * x + 1.8760108 * y + 0.0415560 * z,
            0.0556434 * x - 0.2040259 * y + 1.0572252 * z
        )
    }

    private static let epsilon = 216.0 / 24389
    private static let kappa = 24389.0 / 27

    private static func labF(_ t: Double) -> Double {
        t > epsilon ? cbrt(t) : (kappa * t + 16) / 116
    }

    private static func labFInverse(_ f: Double) -> Double {
        let cube = f * f * f
        return cube > epsilon ? cube : (116 * f - 16) / kappa
    }

    private static func linear(_ channel: Double) -> Double {
        channel <= 0.04045 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
    }

    private static func gamma(_ channel: Double) -> Double {
        channel <= 0.0031308 ? channel * 12.92 : 1.055 * pow(channel, 1 / 2.4) - 0.055
    }
}

/// One hue and chroma at every tone (Material 3's tonal palette). Tone is L*; where sRGB can't
/// show the asked-for chroma at a tone, the chroma is reduced (hue and tone are kept), so tone
/// pairings keep their contrast whatever the seed.
public struct TonalPalette: Equatable, Sendable {
    public let hue: Double
    public let chroma: Double

    public init(hue: Double, chroma: Double) {
        self.hue = hue
        self.chroma = chroma
    }

    /// The seed's own hue and chroma.
    public init(seed: RGBColor) {
        let lch = seed.lch
        self.init(hue: lch.h, chroma: lch.c)
    }

    public func tone(_ tone: Double) -> RGBColor {
        let l = min(max(tone, 0), 100)
        if l <= 0 { return RGBColor(hex: 0x000000) }
        if l >= 100 { return RGBColor(hex: 0xFFFFFF) }
        let wanted = RGBColor.LCh(l: l, c: chroma, h: hue)
        if RGBColor.inGamut(wanted) { return RGBColor(lch: wanted) }
        // Binary search for the most chroma sRGB holds at this hue and tone.
        var low = 0.0, high = chroma
        for _ in 0..<24 {
            let mid = (low + high) / 2
            if RGBColor.inGamut(RGBColor.LCh(l: l, c: mid, h: hue)) { low = mid } else { high = mid }
        }
        return RGBColor(lch: RGBColor.LCh(l: l, c: low, h: hue))
    }
}
