import AppKit
import Foundation

let outputURL = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "AppIcon.iconset")
try? FileManager.default.removeItem(at: outputURL)
try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

let sizes: [(name: String, points: Int, scale: Int)] = [
    ("icon_16x16.png", 16, 1),
    ("icon_16x16@2x.png", 16, 2),
    ("icon_32x32.png", 32, 1),
    ("icon_32x32@2x.png", 32, 2),
    ("icon_128x128.png", 128, 1),
    ("icon_128x128@2x.png", 128, 2),
    ("icon_256x256.png", 256, 1),
    ("icon_256x256@2x.png", 256, 2),
    ("icon_512x512.png", 512, 1),
    ("icon_512x512@2x.png", 512, 2),
]

for item in sizes {
    let pixels = item.points * item.scale
    let image = NSImage(size: NSSize(width: pixels, height: pixels))
    image.lockFocus()

    let rect = NSRect(x: 0, y: 0, width: pixels, height: pixels)
    NSColor.clear.setFill()
    rect.fill()

    let cornerRadius = CGFloat(pixels) * 0.22
    let tileRect = rect.insetBy(dx: CGFloat(pixels) * 0.055, dy: CGFloat(pixels) * 0.055)
    let tilePath = NSBezierPath(roundedRect: tileRect, xRadius: cornerRadius, yRadius: cornerRadius)
    NSGradient(colors: [
        NSColor(red: 0.08, green: 0.60, blue: 0.35, alpha: 1.0),
        NSColor(red: 0.02, green: 0.33, blue: 0.58, alpha: 1.0),
    ])?.draw(in: tilePath, angle: -35)

    NSColor.white.withAlphaComponent(0.18).setStroke()
    tilePath.lineWidth = max(1, CGFloat(pixels) * 0.012)
    tilePath.stroke()

    let bubbleRect = NSRect(
        x: CGFloat(pixels) * 0.20,
        y: CGFloat(pixels) * 0.29,
        width: CGFloat(pixels) * 0.60,
        height: CGFloat(pixels) * 0.47
    )
    let bubblePath = NSBezierPath(roundedRect: bubbleRect, xRadius: CGFloat(pixels) * 0.14, yRadius: CGFloat(pixels) * 0.14)
    bubblePath.move(to: NSPoint(x: CGFloat(pixels) * 0.40, y: CGFloat(pixels) * 0.29))
    bubblePath.line(to: NSPoint(x: CGFloat(pixels) * 0.31, y: CGFloat(pixels) * 0.18))
    bubblePath.line(to: NSPoint(x: CGFloat(pixels) * 0.49, y: CGFloat(pixels) * 0.29))

    NSColor.white.withAlphaComponent(0.95).setFill()
    bubblePath.fill()

    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let fontSize = CGFloat(pixels) * 0.34
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: fontSize, weight: .black),
        .foregroundColor: NSColor(red: 0.04, green: 0.36, blue: 0.28, alpha: 1.0),
        .paragraphStyle: paragraph,
    ]
    let textRect = NSRect(
        x: bubbleRect.minX,
        y: bubbleRect.minY + CGFloat(pixels) * 0.035,
        width: bubbleRect.width,
        height: bubbleRect.height
    )
    NSString(string: "W").draw(in: textRect, withAttributes: attrs)

    image.unlockFocus()

    guard
        let tiff = image.tiffRepresentation,
        let bitmap = NSBitmapImageRep(data: tiff),
        let data = bitmap.representation(using: .png, properties: [:])
    else {
        throw NSError(domain: "IconGeneration", code: 1)
    }
    try data.write(to: outputURL.appendingPathComponent(item.name))
}
