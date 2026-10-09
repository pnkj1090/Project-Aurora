import Foundation

// MARK: - Asset Type

/// Controlled asset type classification (mirrors backend AssetType enum).
public enum AssetType: String, Codable, CaseIterable, Sendable {
    case image = "image"
    case document = "document"
    case file = "file"

    public var displayName: String {
        switch self {
        case .image: return "Image"
        case .document: return "Document"
        case .file: return "File"
        }
    }

    public var systemIcon: String {
        switch self {
        case .image: return "photo"
        case .document: return "doc.text"
        case .file: return "doc"
        }
    }
}

// MARK: - Asset Reference (metadata only, no binary content)

/// Temporary, server-assigned, metadata-only reference to a user-selected photo or file.
///
/// PHASE 5G INVARIANTS:
/// - Contains NO binary content whatsoever.
/// - No filesystem path, no EXIF data, no raw device URLs.
/// - asset_id, created_at, expires_at are always set by the backend.
/// - user_id is always derived from active Aurora identity.
public struct AssetReference: Codable, Identifiable, Equatable, Sendable {
    public let id: String           // maps to asset_id
    public let assetType: AssetType
    public let filename: String
    public let mimeType: String
    public let sizeBytes: Int
    public let source: String
    public let userId: String
    public let createdAt: String
    public let expiresAt: String
    public let width: Int?
    public let height: Int?

    enum CodingKeys: String, CodingKey {
        case id = "asset_id"
        case assetType = "asset_type"
        case filename
        case mimeType = "mime_type"
        case sizeBytes = "size_bytes"
        case source
        case userId = "user_id"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case width
        case height
    }

    public init(
        id: String,
        assetType: AssetType,
        filename: String,
        mimeType: String,
        sizeBytes: Int,
        source: String,
        userId: String,
        createdAt: String,
        expiresAt: String,
        width: Int? = nil,
        height: Int? = nil
    ) {
        self.id = id
        self.assetType = assetType
        self.filename = filename
        self.mimeType = mimeType
        self.sizeBytes = sizeBytes
        self.source = source
        self.userId = userId
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.width = width
        self.height = height
    }

    /// Human-readable file size string.
    public var formattedSize: String {
        let mb = Double(sizeBytes) / 1_000_000
        if mb >= 1.0 {
            return String(format: "%.1f MB", mb)
        } else {
            let kb = Double(sizeBytes) / 1_000
            return String(format: "%.0f KB", kb)
        }
    }
}

// MARK: - Asset Ingest Request (client → backend)

/// Controlled transport model for registering a temporary asset reference.
/// Backend generates: asset_id, created_at, expires_at.
/// Client NEVER supplies user_id.
public struct AssetIngestRequest: Codable, Sendable {
    public let assetType: String
    public let filename: String
    public let mimeType: String
    public let sizeBytes: Int
    public let source: String
    public let width: Int?
    public let height: Int?

    enum CodingKeys: String, CodingKey {
        case assetType = "asset_type"
        case filename
        case mimeType = "mime_type"
        case sizeBytes = "size_bytes"
        case source
        case width
        case height
    }

    public init(
        assetType: AssetType,
        filename: String,
        mimeType: String,
        sizeBytes: Int,
        source: String,
        width: Int? = nil,
        height: Int? = nil
    ) {
        self.assetType = assetType.rawValue
        self.filename = filename
        self.mimeType = mimeType
        self.sizeBytes = sizeBytes
        self.source = source
        self.width = width
        self.height = height
    }
}
