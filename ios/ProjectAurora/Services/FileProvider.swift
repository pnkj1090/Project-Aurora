import UIKit
import UniformTypeIdentifiers

// MARK: - Protocol

/// Protocol for explicit user file selection.
/// Uses UIDocumentPickerViewController ONLY.
/// No filesystem traversal. No background access.
public protocol FileProviderProtocol {
    /// Present the system document picker for single explicit file selection.
    func selectFile(
        in viewController: UIViewController,
        allowedTypes: [UTType],
        completion: @escaping (SelectedFileMetadata?) -> Void
    )
}

// MARK: - Metadata

/// Explicit-selection metadata returned from FileProvider.
/// No binary content, no filesystem path.
public struct SelectedFileMetadata: Sendable {
    public let filename: String
    public let mimeType: String
    public let sizeBytes: Int

    public init(filename: String, mimeType: String, sizeBytes: Int) {
        self.filename = filename
        self.mimeType = mimeType
        self.sizeBytes = sizeBytes
    }

    public var assetIngestRequest: AssetIngestRequest {
        AssetIngestRequest(
            assetType: .file,
            filename: filename,
            mimeType: mimeType,
            sizeBytes: sizeBytes,
            source: "ios_files"
        )
    }
}

// MARK: - System Implementation

/// System file provider using UIDocumentPickerViewController.
/// - Does NOT traverse the filesystem.
/// - User explicitly selects a file.
/// - No background or persistent file access.
public final class SystemFileProvider: NSObject, FileProviderProtocol {

    private var completion: ((SelectedFileMetadata?) -> Void)?

    public func selectFile(
        in viewController: UIViewController,
        allowedTypes: [UTType] = [.pdf, .plainText, .image, .data],
        completion: @escaping (SelectedFileMetadata?) -> Void
    ) {
        self.completion = completion

        let picker = UIDocumentPickerViewController(forOpeningContentTypes: allowedTypes)
        picker.allowsMultipleSelection = false
        picker.delegate = self
        viewController.present(picker, animated: true)
    }
}

extension SystemFileProvider: UIDocumentPickerDelegate {
    public func documentPicker(
        _ controller: UIDocumentPickerViewController,
        didPickDocumentsAt urls: [URL]
    ) {
        guard let url = urls.first else {
            completion?(nil)
            return
        }

        // Scoped access to extract size metadata only
        guard url.startAccessingSecurityScopedResource() else {
            completion?(nil)
            return
        }
        defer { url.stopAccessingSecurityScopedResource() }

        let filename = url.lastPathComponent
        let ext = url.pathExtension.lowercased()
        let mimeType = UTType(filenameExtension: ext)?.preferredMIMEType ?? "application/octet-stream"

        var sizeBytes = 0
        if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path) {
            sizeBytes = (attrs[.size] as? Int) ?? 0
        }

        let metadata = SelectedFileMetadata(
            filename: filename,
            mimeType: mimeType,
            sizeBytes: sizeBytes
        )
        // Security-scoped resource released immediately via defer — no persistent access
        DispatchQueue.main.async { [weak self] in
            self?.completion?(metadata)
        }
    }

    public func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        completion?(nil)
    }
}

// MARK: - Mock Implementation

/// Mock file provider for SwiftUI previews and unit tests.
public final class MockFileProvider: FileProviderProtocol {

    public var mockMetadata: SelectedFileMetadata?

    public init(
        metadata: SelectedFileMetadata? = SelectedFileMetadata(
            filename: "mock_document.pdf",
            mimeType: "application/pdf",
            sizeBytes: 450_000
        )
    ) {
        self.mockMetadata = metadata
    }

    public func selectFile(
        in viewController: UIViewController,
        allowedTypes: [UTType] = [],
        completion: @escaping (SelectedFileMetadata?) -> Void
    ) {
        completion(mockMetadata)
    }
}
