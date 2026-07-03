// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "BaselineIOS",
    defaultLocalization: "en",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "BaselineCore", targets: ["BaselineCore"]),
        .executable(name: "BaselineApp", targets: ["BaselineApp"]),
    ],
    targets: [
        .target(
            name: "BaselineCore",
            path: "Sources/BaselineCore"
        ),
        .executableTarget(
            name: "BaselineApp",
            dependencies: ["BaselineCore"],
            path: "Sources/BaselineApp"
        ),
        .testTarget(
            name: "BaselineCoreTests",
            dependencies: ["BaselineCore"],
            path: "Tests/BaselineCoreTests"
        ),
        .testTarget(
            name: "BaselineAppTests",
            dependencies: ["BaselineApp", "BaselineCore"],
            path: "Tests/BaselineAppTests"
        ),
    ]
)
