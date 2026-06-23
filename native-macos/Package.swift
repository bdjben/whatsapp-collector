// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "WhatsAppCollectorNative",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "WhatsAppCollectorNative", targets: ["WhatsAppCollectorNative"])
    ],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.9.3")
    ],
    targets: [
        .executableTarget(
            name: "WhatsAppCollectorNative",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle")
            ],
            path: "Sources/WhatsAppCollectorNative"
        )
    ]
)
