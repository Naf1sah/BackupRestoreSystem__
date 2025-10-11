# ================== CONFIG ==================
ALGO_DISPLAY = {
    "lz4": "LZ4",
    "zstd": "ZSTD",
    "gzip": "GZIP",
    "brotli": "Brotli",
    "snappy": "Snappy",
}

EXT_TO_ID = {
    "lz4": "lz4",
    "zst": "zstd",
    "gz": "gzip",
    "br": "brotli",
    "snappy": "snappy",
}

HASH_FILE = "hash_storage.json"
EVAL_FILE = "evaluation_results.json"
AIRGAP_FOLDER_NAME = "airgapped_storage"
SIMULATED_ATTACK_FOLDER = "backup_results"
EVALUATION_FOLDER_NAME = "evaluation"

AIRGAP_DRIVE_LETTER = "G"
AIRGAP_VHDX_PATH = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\Data\AirgaStorage.vhdx"

FORCE_UNMOUNT_AT_END = True
AUTO_MOUNT_VHDX = False
VHDX_FILENAME_PREFIX = "airgap"

SOURCE_FOLDER = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\Data\source_data"

ENABLE_SYNC_RAW_TO_SOURCE = False
RAW_DATA_FOLDER = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\Data\source_data_raw"

EXCLUDE_WHEN_SYNC = {
    AIRGAP_FOLDER_NAME,
    SIMULATED_ATTACK_FOLDER,
    EVALUATION_FOLDER_NAME,
    "backup_results",
    "restore_results",
}

# ---- Google Drive ----
CLOUD_UPLOAD_ENABLED = True

# Pakai HANYA SATU path yang benar ke OAuth client JSON:
GDRIVE_CREDENTIALS_FILE = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\credentials.json"
# (hapus baris "credentials.json" yang menimpa!)

GDRIVE_TOKEN_FILE = "token.json"

# Folder IDs (cek lagi memang folder asli, bukan shortcut):
GDRIVE_BACKUP_FOLDER_ID = "10YgQr4Wh-Xh3UX3vssAXaU7QUyK60SH3"
GDRIVE_RAW_FOLDER_ID    = "1Q603DmHehooE1JV6Xhe36qLU3oLdYZN3"

# Scope: tambah readonly agar bisa list/download file yang bukan dibuat app
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
# ============================================
