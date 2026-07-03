#if os(iOS)
import BaselineCore
import SwiftUI

@main
struct BaselineApp: App {
    @StateObject private var model = BaselineAppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}
#endif
