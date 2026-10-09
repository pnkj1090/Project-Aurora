import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

// MARK: - MediaSelectionView

/// View for explicitly selecting photos and files to share with Aurora.
/// Uses PHPickerViewController for photos, UIDocumentPickerViewController for files.
/// No gallery browser, no file manager, no background access.
public struct MediaSelectionView: View {

    @StateObject private var viewModel = AssetViewModel()
    @State private var showingPhotoPickerSheet = false
    @State private var showingFilePickerSheet = false
    @State private var selectedItems: [PhotosPickerItem] = []

    public var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // Header
                headerSection

                // Selected assets list
                if viewModel.selectedAssets.isEmpty {
                    emptyState
                } else {
                    assetList
                }

                Spacer()

                // Permission denied banner
                if viewModel.permissionDenied {
                    permissionDeniedBanner
                }

                // Error banner
                if let error = viewModel.errorMessage {
                    errorBanner(error)
                }

                // Action buttons
                actionButtons
            }
            .navigationTitle("Selected Media")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    if !viewModel.selectedAssets.isEmpty {
                        Button("Clear All") {
                            viewModel.clearAll()
                        }
                        .foregroundColor(.red)
                        .accessibilityLabel("Clear all selected assets")
                        .accessibilityIdentifier("clear-all-assets-button")
                    }
                }
            }
        }
        // SwiftUI Photo Picker (PHPickerViewController under the hood)
        .photosPicker(
            isPresented: $showingPhotoPickerSheet,
            selection: $selectedItems,
            maxSelectionCount: 1,
            matching: .images
        )
        .onChange(of: selectedItems) { _, items in
            guard let item = items.first else { return }
            Task { await handlePhotoPickerItem(item) }
        }
        .fileImporter(
            isPresented: $showingFilePickerSheet,
            allowedContentTypes: [.pdf, .plainText, .spreadsheet, .presentation, .image],
            allowsMultipleSelection: false
        ) { result in
            Task { await handleFileImportResult(result) }
        }
        .overlay {
            if viewModel.isLoading {
                loadingOverlay
            }
        }
    }

    // MARK: - Subviews

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Share with Aurora")
                .font(.headline)
            Text("Aurora can see metadata (type and size) about your selected files. No content is analyzed.")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(.systemGroupedBackground))
        .accessibilityIdentifier("media-selection-header")
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "photo.on.rectangle.angled")
                .font(.system(size: 48))
                .foregroundColor(.secondary)
            Text("No items selected")
                .font(.headline)
                .foregroundColor(.secondary)
            Text("Tap a button below to share a photo or file with Aurora.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityIdentifier("media-empty-state")
    }

    private var assetList: some View {
        List {
            ForEach(viewModel.selectedAssets) { asset in
                SelectedAssetRow(asset: asset) {
                    viewModel.removeAsset(id: asset.id)
                }
            }
        }
        .listStyle(.insetGrouped)
        .accessibilityIdentifier("selected-assets-list")
    }

    private var actionButtons: some View {
        HStack(spacing: 12) {
            Button {
                selectedItems = []
                showingPhotoPickerSheet = true
            } label: {
                Label("Add Photo", systemImage: "photo")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityLabel("Select a photo to share")
            .accessibilityIdentifier("select-photo-button")

            Button {
                showingFilePickerSheet = true
            } label: {
                Label("Add File", systemImage: "doc")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityLabel("Select a file to share")
            .accessibilityIdentifier("select-file-button")
        }
        .padding()
    }

    private var permissionDeniedBanner: some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.orange)
            Text("Photos permission is required. Enable it in Settings.")
                .font(.caption)
            Spacer()
        }
        .padding()
        .background(Color.orange.opacity(0.12))
        .accessibilityIdentifier("permission-denied-banner")
    }

    private func errorBanner(_ message: String) -> some View {
        HStack {
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(.red)
            Text(message)
                .font(.caption)
            Spacer()
            Button {
                viewModel.errorMessage = nil
            } label: {
                Image(systemName: "xmark")
                    .font(.caption)
            }
        }
        .padding()
        .background(Color.red.opacity(0.1))
        .accessibilityIdentifier("asset-error-banner")
    }

    private var loadingOverlay: some View {
        ZStack {
            Color.black.opacity(0.1)
            VStack(spacing: 8) {
                ProgressView()
                Text("Registering asset...")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding()
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
        .ignoresSafeArea()
        .accessibilityIdentifier("asset-loading-overlay")
    }

    // MARK: - Handlers

    @MainActor
    private func handlePhotoPickerItem(_ item: PhotosPickerItem) async {
        // Load data for size extraction only — not persisted
        guard let data = try? await item.loadTransferable(type: Data.self) else { return }

        let filename = item.itemIdentifier.map { "photo_\($0)" } ?? "selected_photo"
        let mimeType = "image/jpeg"
        let sizeBytes = data.count
        var width: Int? = nil
        var height: Int? = nil

        if let image = UIImage(data: data) {
            width = Int(image.size.width)
            height = Int(image.size.height)
        }

        // Data released immediately — only metadata forwarded
        let metadata = SelectedPhotoMetadata(
            filename: "\(filename).jpg",
            mimeType: mimeType,
            sizeBytes: sizeBytes,
            width: width,
            height: height
        )
        await viewModel.reportPhotoToBackend(metadata: metadata)
    }

    @MainActor
    private func handleFileImportResult(_ result: Result<[URL], Error>) async {
        switch result {
        case .failure:
            viewModel.errorMessage = "Could not import the selected file."
        case .success(let urls):
            guard let url = urls.first else { return }

            guard url.startAccessingSecurityScopedResource() else {
                viewModel.errorMessage = "Could not access the selected file."
                return
            }
            defer { url.stopAccessingSecurityScopedResource() }

            let filename = url.lastPathComponent
            let ext = url.pathExtension.lowercased()
            let mimeType = UTType(filenameExtension: ext)?.preferredMIMEType ?? "application/octet-stream"
            let sizeBytes = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0

            // Security-scoped access released immediately via defer
            let metadata = SelectedFileMetadata(
                filename: filename,
                mimeType: mimeType,
                sizeBytes: sizeBytes
            )
            await viewModel.reportFileToBackend(metadata: metadata)
        }
    }
}

// MARK: - SelectedAssetRow

struct SelectedAssetRow: View {

    let asset: AssetReference
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Type icon
            Image(systemName: asset.assetType.systemIcon)
                .font(.title3)
                .foregroundColor(.accentColor)
                .frame(width: 32)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(asset.assetType.displayName)
                    .font(.subheadline)
                    .fontWeight(.medium)
                // Size only — no filename exposed in generic list
                Text(asset.formattedSize)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()

            Button(role: .destructive) {
                onRemove()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(asset.assetType.displayName)")
            .accessibilityIdentifier("remove-asset-\(asset.id)")
        }
        .padding(.vertical, 4)
        .accessibilityIdentifier("asset-row-\(asset.id)")
    }
}

// MARK: - Preview

#Preview {
    MediaSelectionView()
}
