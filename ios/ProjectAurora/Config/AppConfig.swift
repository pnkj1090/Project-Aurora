import Foundation

/// Central client configuration for Project Aurora iOS client.
public enum AppConfig {
    /// Base URL for the backend API.
    /// In local development with iOS Simulator, 127.0.0.1 points directly to the host machine.
    /// When running on a physical iPhone, replace with the host machine's LAN IP.
    public static var backendBaseURL: URL {
        #if targetEnvironment(simulator)
        return URL(string: "http://127.0.0.1:8000")!
        #else
        // LAN IP of your host Mac for physical device testing
        return URL(string: "http://192.168.1.8:8000")!
        #endif
    }
    
    public static let appName = "Project Aurora"
    public static let clientVersion = "0.1.0"
}
