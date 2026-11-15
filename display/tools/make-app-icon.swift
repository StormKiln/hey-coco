// Draws Coco Display's 1024×1024 app icon with CoreGraphics: a white speech bubble with a
// stylised "C" on a warm gradient. Shapes only (no fonts, no third-party artwork), opaque
// (App Store icons must not have an alpha channel).
//
//   make display-icon      # or: swift display/tools/make-app-icon.swift <out.png>

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let size = 1024
let s = CGFloat(size)
let out = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "display/App/Assets.xcassets/AppIcon.appiconset/AppIcon.png"

func rgb(_ hex: UInt32) -> CGColor {
    CGColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

let space = CGColorSpace(name: CGColorSpace.sRGB)!
guard
    let ctx = CGContext(
        data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: space,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
else { fatalError("no context") }

// CoreGraphics' origin is bottom-left; flip so the coordinates below read top-down.
ctx.translateBy(x: 0, y: s)
ctx.scaleBy(x: 1, y: -1)

// Background: warm diagonal gradient, apricot top-left to coral bottom-right. iOS masks the
// corners itself, so the square is filled edge to edge.
let background = CGGradient(
    colorsSpace: space, colors: [rgb(0xFFC371), rgb(0xFF8A5C), rgb(0xF0584A)] as CFArray,
    locations: [0, 0.55, 1])!
ctx.drawLinearGradient(
    background, start: CGPoint(x: 0, y: 0), end: CGPoint(x: s, y: s), options: [])

// Speech bubble: a rounded rectangle plus a tail at the bottom left.
let bubble = CGRect(x: 172, y: 196, width: 680, height: 560)
let radius: CGFloat = 200
let path = CGMutablePath()
path.addRoundedRect(in: bubble, cornerWidth: radius, cornerHeight: radius)
// Tail: starts well inside the bubble so the join is hidden.
let tail = CGMutablePath()
tail.move(to: CGPoint(x: 290, y: 640))
tail.addQuadCurve(to: CGPoint(x: 236, y: 872), control: CGPoint(x: 300, y: 820))
tail.addQuadCurve(to: CGPoint(x: 480, y: 700), control: CGPoint(x: 390, y: 840))
tail.closeSubpath()

// Soft shadow under the bubble. Fill the two shapes separately: in one path their opposite
// windings would punch a hole where they overlap.
func fillBubble(_ color: CGColor) {
    ctx.setFillColor(color)
    ctx.addPath(path)
    ctx.fillPath()
    ctx.addPath(tail)
    ctx.fillPath()
}
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: 18), blur: 40, color: rgb(0xB8402F).copy(alpha: 0.45)!)
ctx.beginTransparencyLayer(auxiliaryInfo: nil)
fillBubble(rgb(0xFFFFFF))
ctx.endTransparencyLayer()
ctx.restoreGState()
fillBubble(rgb(0xFFF8F2))

// The "C": a thick round-capped arc, open to the right, in the gradient's coral.
let center = CGPoint(x: bubble.midX - 30, y: bubble.midY)
let arcRadius: CGFloat = 150
let gap: CGFloat = .pi / 3.6  // half-opening of the C, either side of the +x axis
ctx.setLineCap(.round)
ctx.setLineWidth(92)
ctx.setStrokeColor(rgb(0xF2694B))
ctx.addArc(center: center, radius: arcRadius, startAngle: gap, endAngle: 2 * .pi - gap, clockwise: false)
ctx.strokePath()

// A dot in the C's mouth, like a cursor or a word about to be spoken.
ctx.setFillColor(rgb(0xFFAE5E))
let dot: CGFloat = 46
ctx.fillEllipse(in: CGRect(x: center.x + arcRadius + 24 - dot, y: center.y - dot, width: dot * 2, height: dot * 2))

guard let image = ctx.makeImage() else { fatalError("no image") }
let url = URL(fileURLWithPath: out)
try? FileManager.default.createDirectory(
    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
else { fatalError("cannot write \(out)") }
CGImageDestinationAddImage(dest, image, nil)
guard CGImageDestinationFinalize(dest) else { fatalError("cannot write \(out)") }
print("Wrote \(out) (\(size)×\(size))")
