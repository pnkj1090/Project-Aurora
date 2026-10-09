import Foundation

// MARK: - Asset ViewModel

/// Main actor observable ViewModel for managing temporarily selected assets.
/// All assets are ephemeral — cleared on session end.
/// No binary content is held in memory beyond initial size/dimension extraction.
@MainActor
public final class AssetViewModel: ObservableObject {

    // MARK: - State

    @Published public var selectedAssets: [AssetReference] = []
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil
    @Published public var permissionDenied: Bool = false

    private let apiClient: AuroraAPIClient

    // MARK: - Init

    public init(apiClient: AuroraAPIClient = AuroraAPIClient()) {
        self.apiClient = apiClient
    }

    // MARK: - Asset Management (in-memory only)

    /// Add a reported asset reference to the local selection list.
    public func addAsset(_ ref: AssetReference) {
        // Prevent duplicates
        guard !selectedAssets.contains(where: { $0.id == ref.id }) else { return }
        selectedAssets.append(ref)
    }

    /// Remove an asset from the local selection and notify the backend.
    public func removeAsset(id: String) {
        selectedAssets.removeAll { $0.id == id }
        Task {
            do {
                _ = try await apiClient.deleteAsset(assetId: id)
            } catch {
                // Best-effort: local removal succeeded; backend expiry will handle it
            }
        }
    }

    /// Clear all locally selected assets.
    public func clearAll() {
        let ids = selectedAssets.map { $0.id }
        selectedAssets.removeAll()
        // Best-effort backend cleanup
        Task {
            for id in ids {
                _ = try? await apiClient.deleteAsset(assetId: id)
            }
        }
    }

    // MARK: - Photo Selection

    /// Report selected photo metadata to backend and register the asset reference.
    public func reportPhotoToBackend(metadata: SelectedPhotoMetadata) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let request = metadata.assetIngestRequest
        do {
            let ref = try await apiClient.ingestAsset(request: request)
            addAsset(ref)
        } catch {
            errorMessage = "Failed to register photo: \(error.localizedDescription)"
        }
    }

    // MARK: - File Selection

    /// Report selected file metadata to backend and register the asset reference.
    public func reportFileToBackend(metadata: SelectedFileMetadata) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        let request = metadata.assetIngestRequest
        do {
            let ref = try await apiClient.ingestAsset(request: request)
            addAsset(ref)
        } catch {
            errorMessage = "Failed to register file: \(error.localizedDescription)"
        }
    }
}
