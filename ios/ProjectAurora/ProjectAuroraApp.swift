import SwiftUI

@main
struct ProjectAuroraApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .onOpenURL { url in
                    _ = DeepLinkNavigator.shared.handle(url: url)
                }
        }
    }
}
