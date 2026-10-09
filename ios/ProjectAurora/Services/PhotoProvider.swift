import PhotosUI
import SwiftUI

// MARK: - Protocol

/// Protocol for explicit user photo selection.
/// Uses PHPickerViewController ONLY — no PHPhotoLibrary broad authorization.
/// No background access. No library enumeration.
public protocol PhotoProviderProtocol {
    /// Check current Photos authorization status (for UI feedback only).
    func authorizationStatus() -> PHAuthorizationStatus

    /// Present the system photo picker for single explicit selection.
    /// Returns selected asset metadata or nil if cancelled.
    func selectPhoto(
        in viewController: UIViewController,
        completion: @escaping (SelectedPhotoMetadata?) -> Void
    )
}

// MARK: - Metadata

/// Explicit-selection metadata returned from PhotoProvider.
/// No binary content, no filesystem path, no EXIF.
public struct SelectedPhotoMetadata: Sendable {
    public let filename: String
    public let mimeType: String
    public let sizeBytes: Int
    public let width: Int?
    public let height: Int?

    public init(
        filename: String,
        mimeType: String,
        sizeBytes: Int,
        width: Int? = nil,
        height: Int? = nil
    ) {
        self.filename = filename
        self.mimeType = mimeType
        self.sizeBytes = sizeBytes
        self.width = width
        self.height = height
    }

    public var assetIngestRequest: AssetIngestRequest {
        AssetIngestRequest(
            assetType: .image,
            filename: filename,
            mimeType: mimeType,
            sizeBytes: sizeBytes,
            source: "ios_photos",
            width: width,
            height: height
        )
    }
}

// MARK: - System Implementation

/// System photo provider using PHPickerViewController (modern explicit-selection API).
/// - Does NOT request broad PHPhotoLibrary access.
/// - Does NOT enumerate the photo library.
/// - User selects a specific item; no background access occurs.
public final class SystemPhotoProvider: NSObject, PhotoProviderProtocol {

    private var completion: ((SelectedPhotoMetadata?) -> Void)?

    public func authorizationStatus() -> PHAuthorizationStatus {
        return PHPhotoLibrary.authorizationStatus(for: .readWrite)
    }

    public func selectPhoto(
        in viewController: UIViewController,
        completion: @escaping (SelectedPhotoMetadata?) -> Void
    ) {
        self.completion = completion

        var config = PHPickerConfiguration()
        config.selectionLimit = 1
        config.filter = .images
        // Important: does not request full library access
        config.preferredAssetRepresentationMode = .current

        let picker = PHPickerViewController(configuration: config)
        picker.delegate = self
        viewController.present(picker, animated: true)
    }
}

extension SystemPhotoProvider: PHPickerViewControllerDelegate {
    public func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: true)
        guard let result = results.first else {
            completion?(nil)
            return
        }

        let provider = result.itemProvider
        // Determine MIME type
        var mimeType = "image/jpeg"
        if provider.hasItemConformingToTypeIdentifier("public.png") {
            mimeType = "image/png"
        } else if provider.hasItemConformingToTypeIdentifier("image/heic") {
            mimeType = "image/heic"
        }

        let suggestedName = provider.suggestedName ?? "photo"
        let ext: String
        switch mimeType {
        case "image/png": ext = "png"
        case "image/heic": ext = "heic"
        default: ext = "jpg"
        }
        let filename = "\(suggestedName).\(ext)"

        // Load data to determine size only — no persistent storage
        provider.loadDataRepresentation(forTypeIdentifier: "public.image") { [weak self] data, error in
            DispatchQueue.main.async {
                guard let data = data else {
                    self?.completion?(nil)
                    return
                }
                let sizeBytes = data.count
                var width: Int? = nil
                var height: Int? = nil

                if let image = UIImage(data: data) {
                    width = Int(image.size.width)
                    height = Int(image.size.height)
                }

                // Data is immediately discarded — only metadata is returned
                let metadata = SelectedPhotoMetadata(
                    filename: filename,
                    mimeType: mimeType,
                    sizeBytes: sizeBytes,
                    width: width,
                    height: height
                )
                self?.completion?(metadata)
            }
        }
    }
}

// MARK: - Mock Implementation

/// Mock photo provider for SwiftUI previews and unit tests.
public final class MockPhotoProvider: PhotoProviderProtocol {

    public var mockMetadata: SelectedPhotoMetadata?
    public var mockStatus: PHAuthorizationStatus = .authorized

    public init(
        metadata: SelectedPhotoMetadata? = SelectedPhotoMetadata(
            filename: "mock_photo.jpg",
            mimeType: "image/jpeg",
            sizeBytes: 1_200_000,
            width: 4032,
            height: 3024
        )
    ) {
        self.mockMetadata = metadata
    }

    public func authorizationStatus() -> PHAuthorizationStatus { mockStatus }

    public func selectPhoto(
        in viewController: UIViewController,
        completion: @escaping (SelectedPhotoMetadata?) -> Void
    ) {
        completion(mockMetadata)
    }
}
