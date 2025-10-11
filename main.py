import os
import io
import shutil
import argparse
import time
import hashlib
import subprocess
import json
import datetime as dt
import tempfile
from contextlib import contextmanager

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.auth.exceptions import RefreshError

from config import (
    SOURCE_FOLDER, AIRGAP_FOLDER_NAME, SIMULATED_ATTACK_FOLDER,
    ALGO_DISPLAY, EXT_TO_ID, HASH_FILE,
    AIRGAP_DRIVE_LETTER, AUTO_MOUNT_VHDX, VHDX_FILENAME_PREFIX,
    EVALUATION_FOLDER_NAME,
    CLOUD_UPLOAD_ENABLED, GDRIVE_CREDENTIALS_FILE, GDRIVE_TOKEN_FILE, GDRIVE_BACKUP_FOLDER_ID,
    GDRIVE_RAW_FOLDER_ID, GDRIVE_SCOPES, FORCE_UNMOUNT_AT_END, AIRGAP_VHDX_PATH
)

from utils import (
    load_json, save_json, ensure_dir, get_sha256,
    is_drive_mounted,find_vhdx_in_folder,
    show_all_hash_popup, save_per_file_plot, evaluate_and_save
)
from backup_restore import backup_file, restore_file
from simulate import simulate_ransomware_safe
from simulate_header import simulate_header_corruption_safe


from progress import emit, stage  # Dashboard


# ================= ARGPARSE MODE =================
parser = argparse.ArgumentParser()
parser.add_argument(
    "--mode",
    choices=("normal", "wannacry", "headercorrupt"),
    default="normal",
    help="Pilih mode: normal | wannacry | headercorrupt"
)
args = parser.parse_args()
# =================================================

if args.mode == "normal":
    emit("ransom_reset")  # Tambah event reset ransomware

# ==================== GOOGLE DRIVE HELPERS ====================
def md5_of_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def get_drive_service_oauth(credentials_file, token_file, scopes):
    """
    Robust OAuth:
    - Jika token.json ada tapi expired/invalid → refresh.
    - Jika refresh gagal (RefreshError) → hapus token.json dan paksa alur login baru.
    - Jika scope berubah dari sebelumnya → juga paksa login baru.
    """
    creds = None

    # 1) Muat token lama bila ada
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, scopes)
            # Deteksi mismatch scopes (misal kamu ganti GDRIVE_SCOPES)
            if set(getattr(creds, "scopes", []) or []) != set(scopes):
                # scope berubah → paksa login ulang
                creds = None
        except Exception:
            # token corrupt → paksa login ulang
            creds = None

    # 2) Jika ada creds tapi tidak valid → coba refresh
    if creds and not creds.valid:
        try:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
        except RefreshError:
            # Token revoked/expired → hapus & paksa login baru
            try:
                os.remove(token_file)
            except FileNotFoundError:
                pass
            creds = None

    # 3) Jika belum ada creds valid → jalankan alur login lokal
    if not creds:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
        # port=0 → pilih port bebas otomatis
        creds = flow.run_local_server(port=0)
        with open(token_file, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

def find_folder_id_by_name(service, name):
    safe = name.replace("'", "\\'")
    q = f"mimeType='application/vnd.google-apps.folder' and name='{safe}' and trashed=false"
    resp = service.files().list(q=q, fields="files(id,name)").execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None

def find_file_in_folder_by_name(service, folder_id, name):
    safe = name.replace("'", "\\'")
    q = f"'{folder_id}' in parents and name='{safe}' and trashed=false"
    resp = service.files().list(q=q, fields="files(id,name,md5Checksum,size)").execute()
    files = resp.get("files", [])
    return files[0] if files else None

def upload_file_to_drive(service, folder_id, local_file_path, description=None, on_duplicate="update"):
    file_name = os.path.basename(local_file_path)

    # MD5 lokal
    h = hashlib.md5()
    with open(local_file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    local_md5 = h.hexdigest()

    if on_duplicate in ("skip", "update"):
        existing = find_file_in_folder_by_name(service, folder_id, file_name)
        if existing:
            remote_md5 = existing.get("md5Checksum")
            if remote_md5 == local_md5:
                print(f"[GDRIVE] SKIP (identik): {file_name} (id={existing['id']})")
                emit("gdrive_upload_skip_identical", file=file_name, file_id=existing['id'])
                return existing["id"]
            if on_duplicate == "update":
                media = MediaFileUpload(local_file_path, resumable=True)
                body = {"name": file_name}
                if description:
                    body["description"] = description
                updated = service.files().update(
                    fileId=existing["id"], media_body=media, body=body
                ).execute()
                print(f"[GDRIVE] UPDATED: {file_name} (id={existing['id']})")
                emit("gdrive_upload_updated", file=file_name, file_id=existing['id'])
                return updated["id"]

    # create baru
    metadata = {"name": file_name, "parents": [folder_id]}
    if description:
        metadata["description"] = description
    media = MediaFileUpload(local_file_path, resumable=True)
    created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    print(f"[GDRIVE] CREATED: {file_name} (id={created['id']})")
    emit("gdrive_upload_created", file=file_name, file_id=created['id'])
    return created["id"]

def drive_list_children(service, folder_id):
    q = f"'{folder_id}' in parents and trashed=false"
    page_token = None
    while True:
        resp = service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType,md5Checksum,size)",
            pageToken=page_token
        ).execute()
        for it in resp.get("files", []):
            yield it
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

def drive_walk(service, root_folder_id, prefix=""):
    stack = [(root_folder_id, prefix)]
    while stack:
        fid, pfx = stack.pop()
        for item in drive_list_children(service, fid):
            name = item["name"]
            mt = item.get("mimeType", "")
            if mt == "application/vnd.google-apps.folder":
                stack.append((item["id"], os.path.join(pfx, name)))
            else:
                rel_path = os.path.join(pfx, name) if pfx else name
                yield (item["id"], rel_path.replace("\\", "/"), item.get("md5Checksum"))

def download_drive_file(service, file_id, local_path):
    ensure_dir(os.path.dirname(local_path))
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


# ==================== POWER SHELL HELPERS ====================

def _ps_exe():
    """
    Cari executable PowerShell yang tersedia.
    Bisa dioverride lewat ENV:
      POWERSHELL_EXE=C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe
      atau C:\\Program Files\\PowerShell\\7\\pwsh.exe
    """
    candidates = [
        os.environ.get("POWERSHELL_EXE"),
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell.exe", "powershell",
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "pwsh.exe", "pwsh",
    ]
    for exe in candidates:
        if not exe:
            continue
        try:
            res = subprocess.run(
                [exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
                capture_output=True, text=True
            )
            if res.returncode == 0:
                return exe
        except FileNotFoundError:
            pass
    raise FileNotFoundError(
        "PowerShell tidak ditemukan. Set env POWERSHELL_EXE ke path penuh, "
        "mis: C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe "
        "atau C:\\Program Files\\PowerShell\\7\\pwsh.exe"
    )



# ==================== VHDX MOUNT/UNMOUNT – UTIL POWERSHELL ====================
def _run_ps(ps_script: str):
    exe = _ps_exe()
    res = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True, text=True
    )
    return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()


def is_drive_mounted_ps(letter: str) -> bool:
    """Deteksi akurat via PowerShell (lebih kuat dari fungsi lama)."""
    ps = rf"""
$dl = '{letter}'
$drv = Get-PSDrive -Name $dl -ErrorAction SilentlyContinue
$vol = Get-Volume -DriveLetter $dl -ErrorAction SilentlyContinue
if ($drv -or $vol) {{ 'OK' }} else {{ 'NO' }}
"""
    code, out, _ = _run_ps(ps)
    return "OK" in out

# ======== FUNGSI LAMA (dipertahankan) ========
def remove_drive_letter(letter):
    ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
mountvol {letter}: /D
try {{
  $part = Get-Partition -DriveLetter '{letter}'
  if ($part) {{
    Remove-PartitionAccessPath -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -AccessPath '{letter}:\'
  }}
}} catch {{}}
'REMOVED:{letter}'
"""
    code, out, err = _run_ps(ps)
    if "REMOVED:" in out:
        print(f"[VHD] Letter {letter}: dilepas.")
    else:
        print(f"[VHD] Warning: gagal melepas letter {letter}. out={out} err={err}")

def force_unmount_airgap_by_drive(letter:
                                   str):
    ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$dl = '{letter}'
try {{ mountvol ($dl + ':') /D }} catch {{}}
try {{
  $p = Get-Partition -DriveLetter $dl
  if ($p) {{
    try {{ Remove-PartitionAccessPath -DiskNumber $p.DiskNumber -PartitionNumber $p.PartitionNumber -AccessPath ($dl + ':\') -ErrorAction Stop }} catch {{}}
    try {{ Set-Disk -Number $p.DiskNumber -IsOffline $true -ErrorAction Stop }} catch {{}}
    'UNMOUNTED:' + $dl
    exit 0
  }}
}} catch {{}}
'NO_PARTITION:' + $dl
exit 1
"""
    rc, out, err = _run_ps(ps)
    print(f"[VHD] force_unmount stdout: {out}")
    return "UNMOUNTED:" in out

def repair_access_path(letter: str, diskno: int = None, partno: int = None):
    if diskno is not None and partno is not None:
        ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
mountvol /E | Out-Null
Set-Disk -Number {diskno} -IsOffline $false -IsReadOnly $false
Add-PartitionAccessPath -DiskNumber {diskno} -PartitionNumber {partno} -AccessPath '{letter}:\'
"""
    else:
        ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
mountvol /E | Out-Null
$p = Get-Partition -DriveLetter '{letter}'
if ($p) {{
  Set-Disk -Number $p.DiskNumber -IsOffline $false -IsReadOnly $false
  Add-PartitionAccessPath -DiskNumber $p.DiskNumber -PartitionNumber $p.PartitionNumber -AccessPath '{letter}:\'
}}
"""
    _run_ps(ps)

def wait_for_drive(letter: str, timeout_s: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if is_drive_mounted_ps(letter):
            return True
        time.sleep(0.3)
    return is_drive_mounted_ps(letter)

# ======== FUNGSI BARU (stabil) ========
def attempt_mount_vhdx_and_assign(vhdx_path, drive_letter, read_only=False):
    ro = "-ReadOnly" if read_only else ""
    ps = rf"""
$ErrorActionPreference = 'Stop'

# Jika VHDX sudah attached, lepas dulu
try {{
  $v = Get-VHD -Path '{vhdx_path}' -ErrorAction Stop
  if ($v.Attached) {{
    Dismount-VHD -Path '{vhdx_path}' -ErrorAction SilentlyContinue
  }}
}} catch {{ }}

# Mount ulang
Mount-VHD -Path '{vhdx_path}' {ro} | Out-Null

# Cari disk
$disk = Get-Disk | Where-Object {{ $_.Location -like '*{os.path.basename(vhdx_path)}*' }} | Select-Object -First 1
if (-not $disk) {{ throw 'Disk VHDX tidak ditemukan' }}

# Pastikan online
if ($disk.IsOffline) {{ Set-Disk -Number $disk.Number -IsOffline $false }}
try {{ Set-Disk -Number $disk.Number -IsReadOnly $false }} catch {{ }}

# Ambil partisi utama
$part = Get-Partition -DiskNumber $disk.Number | Sort-Object Size -Descending | Select-Object -First 1
if (-not $part) {{ throw 'Partisi tidak ditemukan' }}

# Hapus konflik drive letter
try {{ mountvol {drive_letter}: /D }} catch {{ }}

# Assign drive letter
try {{
  Set-Partition -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber -NewDriveLetter '{drive_letter}'
}} catch {{
  Add-PartitionAccessPath -DiskNumber $disk.Number -PartitionNumber $part.PartitionNumber -AccessPath '{drive_letter}:\'
}}

"ASSIGNED:{drive_letter}|DISKNO:" + $disk.Number + "|PART:" + $part.PartitionNumber
"""
    code, out, err = _run_ps(ps)
    if code == 0 and out.startswith("ASSIGNED:"):
        print(f"[VHD] Mount & assign OK: {out}")
        emit("airgap_mount_ok", drive=drive_letter, info=out)
        return True
    print(f"[VHD] Gagal mount/assign: rc={code} out={out} err={err}")
    emit("airgap_mount_fail", drive=drive_letter, rc=code, out=out, err=err)
    return False

def reset_airgap(vhdx_path, drive_letter):
    """Panggil reset-airgap.ps1 sebelum mount VHDX (tanpa ubah alur)."""
    ps_script = os.path.join(os.path.dirname(vhdx_path), "reset-airgap.ps1")
    if not os.path.exists(ps_script):
        print(f"[WARN] reset-airgap.ps1 tidak ditemukan di {ps_script}")
        emit("airgap_reset_missing", path=ps_script)
        return
    exe = _ps_exe()  # <<< gunakan exe yang terdeteksi
    cmd = [
        exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ps_script,
        "-VhdxPath", vhdx_path,
        "-DriveLetter", drive_letter
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("[RESET-AIRGAP] stdout:", res.stdout.strip())
    if res.stderr.strip():
        print("[RESET-AIRGAP] stderr:", res.stderr.strip())
    emit("airgap_reset_done", stdout=res.stdout.strip(), stderr=res.stderr.strip())


def dismount_vhdx_and_cleanup(vhdx_path, drive_letter):
    """Lepas letter & dismount VHDX (tanpa Get-DiskImage)."""
    ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
try {{ mountvol {drive_letter}: /D }} catch {{}}
try {{
  $p = Get-Partition -DriveLetter '{drive_letter}'
  if ($p) {{
    Remove-PartitionAccessPath -DiskNumber $p.DiskNumber -PartitionNumber $p.PartitionNumber -AccessPath '{drive_letter}:\'
  }}
}} catch {{}}
try {{
  Dismount-VHD -Path '{vhdx_path}' -ErrorAction Stop
  'UNMOUNTED:{vhdx_path}'
}} catch {{
  'FAILED_UNMOUNT:{vhdx_path}'
}}
"""
    code, out, err = _run_ps(ps)
    print(f"[VHD] Cleanup rc={code}, out={out}, err={err}")
    emit("airgap_unmount", rc=code, out=out, err=err)

# ==================== PATCH: MOUNT-ONLY MODE ====================
@contextmanager
def with_vhd_mounted(vhdx_candidates, drive_letter, read_only=False, wait_timeout_s=30.0, leave_mounted=False):
    """
    yield False  → drive sudah terpasang (bukan milik skrip)
    yield True   → drive dipasang oleh skrip
    Jika leave_mounted=True, drive TIDAK di-unmount saat keluar konteks.
    """
    owned = False
    mounted_vhdx_path = None
    try:
        if is_drive_mounted_ps(drive_letter):
            print(f"[VHD] {drive_letter}: sudah terpasang (pre-mounted).")
            emit("airgap_pre_mounted", drive=drive_letter)
            yield False
            return

        for vpath in (vhdx_candidates or []):
            print(f"[VHD] Mencoba mount & assign {drive_letter}: {vpath}")
            emit("airgap_mount_attempt", drive=drive_letter, vhdx=vpath)
            if attempt_mount_vhdx_and_assign(vpath, drive_letter, read_only=read_only):
                if wait_for_drive(drive_letter, timeout_s=wait_timeout_s):
                    owned, mounted_vhdx_path = True, vpath
                    print(f"[VHD] STATUS: MOUNTED {vpath} -> {drive_letter}")
                    break
                else:
                    print(f"[VHD] Letter {drive_letter} belum terlihat; mencoba repair...")
                    emit("airgap_mount_repair", drive=drive_letter)
                    repair_access_path(drive_letter)
                    if wait_for_drive(drive_letter, timeout_s=10.0):
                        owned, mounted_vhdx_path = True, vpath
                        print(f"[VHD] STATUS: MOUNTED (setelah repair) {vpath} -> {drive_letter}")
                        break
        if not owned:
            print("[VHD] Gagal mount semua kandidat. Pakai fallback lokal.")
            emit("airgap_mount_all_failed", drive=drive_letter)
        yield owned
    finally:
        # >>> HANYA UNMOUNT jika kita yang mount DAN tidak diminta leave_mounted
        if owned and mounted_vhdx_path and not leave_mounted:
            print(f"[VHD] UNMOUNT: {mounted_vhdx_path}")
            dismount_vhdx_and_cleanup(mounted_vhdx_path, drive_letter)


# ==================== TRANSFER ====================
def transfer_to_airgap(output_folder, airgap_folder):
    print(f"[INFO] Transfer data dari {output_folder} ke {airgap_folder}...")
    shutil.copytree(output_folder, airgap_folder, dirs_exist_ok=True)


# ==================== MAIN ====================
def main():
    emit("pipeline_start")

    base_folder = os.path.dirname(SOURCE_FOLDER)
    output_folder = os.path.join(base_folder, "backup_results")
    restore_folder = os.path.join(base_folder, "restore_results")
    evaluation_folder = os.path.join(base_folder, EVALUATION_FOLDER_NAME)
    default_local_airgap = os.path.join(base_folder, AIRGAP_FOLDER_NAME)
    simulated_attack_folder = os.path.join(base_folder, SIMULATED_ATTACK_FOLDER)
    ##ensure_dir(output_folder)
    ##ensure_dir(restore_folder)
    ##ensure_dir(evaluation_folder)

    # Hash.json di air-gapped drive bila ada, else lokal (pakai deteksi PS)
    airgap_hash_folder = os.path.join(f"{AIRGAP_DRIVE_LETTER}:\\", AIRGAP_FOLDER_NAME)
    if is_drive_mounted_ps(AIRGAP_DRIVE_LETTER):
        os.makedirs(airgap_hash_folder, exist_ok=True)
        hash_file_path = os.path.join(airgap_hash_folder, HASH_FILE)
        emit("hash_location_airgap", path=hash_file_path)
    else:
        os.makedirs(default_local_airgap, exist_ok=True)
        hash_file_path = os.path.join(default_local_airgap, HASH_FILE)
        emit("hash_location_local", path=hash_file_path)

    # Bersihkan folder hasil
    for folder in [output_folder, restore_folder, evaluation_folder, default_local_airgap, simulated_attack_folder]:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"[CLEANUP] Folder '{folder}' dibersihkan.")
        ensure_dir(folder)
    emit("workspace_prepared",
         output_folder=output_folder, restore_folder=restore_folder,
         evaluation_folder=evaluation_folder)

    algoritma_list = ["lz4", "zstd", "gzip", "brotli", "snappy"]
    total_rasio = {algo: [] for algo in algoritma_list}
    total_waktu = {algo: [] for algo in algoritma_list}

    # === Load hash dari lokasi hash ===
    hash_memory = load_json(hash_file_path, {})
    emit("hash_loaded", entries=len(hash_memory))

    # === Koneksi Drive + tentukan folder sumber ===
    if not CLOUD_UPLOAD_ENABLED:
        emit("error_config", message="CLOUD_UPLOAD_ENABLED=False")
        raise RuntimeError("CLOUD_UPLOAD_ENABLED=False. Untuk baca langsung dari Drive, aktifkan dulu di config.")

    print("[GDRIVE] Autentikasi OAuth...")
    with stage("drive_auth"):
        gsvc = get_drive_service_oauth(GDRIVE_CREDENTIALS_FILE, GDRIVE_TOKEN_FILE, GDRIVE_SCOPES)
    emit("drive_auth_ok")

    drive_source_folder_id = GDRIVE_RAW_FOLDER_ID or find_folder_id_by_name(gsvc, "source data")
    if not drive_source_folder_id:
        emit("error_source_folder_missing")
        raise RuntimeError("Folder Drive bernama 'source data' tidak ditemukan dan GDRIVE_RAW_FOLDER_ID kosong.")

    print(f"[GDRIVE] Enumerasi file (rekursif) dari folder sumber id={drive_source_folder_id} ...")
    with stage("drive_enumerate"):
        drive_files = list(drive_walk(gsvc, drive_source_folder_id, prefix=""))
    if not drive_files:
        emit("drive_empty")
        print("[INFO] Tidak ada file di Drive sumber.")
        return
    emit("drive_enumerated", count=len(drive_files))
    print(f"[GDRIVE] Ditemukan {len(drive_files)} file di Drive sumber.")

    # === Backup per file (langsung dari Drive; download sementara per file) ===
    with tempfile.TemporaryDirectory() as tmpdir:
        for file_id, rel_path, _md5 in drive_files:
            local_tmp_path = os.path.join(tmpdir, rel_path)
            ensure_dir(os.path.dirname(local_tmp_path))
            print(f"[GDRIVE] Download sementara: {rel_path}")

            with stage("download_file", file=rel_path):
                download_drive_file(gsvc, file_id, local_tmp_path)

            original_hash = get_sha256(local_tmp_path)
            emit("hash_original", file=rel_path, sha256=original_hash, size=os.path.getsize(local_tmp_path))

            # kunci hash pakai path relatif di Drive agar konsisten
            hash_memory[rel_path] = original_hash
            save_json(hash_file_path, hash_memory)

            ukuran_asli = os.path.getsize(local_tmp_path)
            rasio_list, waktu_list = [], []
            for algo in algoritma_list:
                with stage("backup_file", file=rel_path, algo=algo):
                    comp_file, durasi = backup_file(local_tmp_path, algo, output_folder, original_hash, source_folder=tmpdir)
                ukuran_comp = os.path.getsize(comp_file)
                rasio = (ukuran_comp / ukuran_asli) if ukuran_asli > 0 else 0
                total_waktu[algo].append(durasi)
                total_rasio[algo].append(rasio)
                rasio_list.append(rasio)
                waktu_list.append(durasi)
                emit("backup_result", file=rel_path, algo=algo,
                     size_in=ukuran_asli, size_out=ukuran_comp, ratio=rasio, duration_ms=durasi)

            save_per_file_plot(rel_path, algoritma_list, rasio_list, waktu_list, evaluation_folder)
            emit("perfile_plot_saved", file=rel_path)

    # === Transfer ke air-gap (fisik atau simulasi) ===
    vhdx_candidates = [AIRGAP_VHDX_PATH]
    print(f"[DEBUG] is_drive_mounted_ps({AIRGAP_DRIVE_LETTER}) = {is_drive_mounted_ps(AIRGAP_DRIVE_LETTER)}", flush=True)
    print(f"[DEBUG] vhdx_candidates = {vhdx_candidates}", flush=True)

    # Reset dulu sebelum mount supaya tidak terkunci
    reset_airgap(AIRGAP_VHDX_PATH, AIRGAP_DRIVE_LETTER)

    # >>> Mount-Only Mode: leave_mounted=True
    with with_vhd_mounted(vhdx_candidates, AIRGAP_DRIVE_LETTER, read_only=False, leave_mounted=True) as owned_mount:
        if is_drive_mounted_ps(AIRGAP_DRIVE_LETTER):
            airgap_folder = os.path.join(f"{AIRGAP_DRIVE_LETTER}:\\", AIRGAP_FOLDER_NAME)
            ensure_dir(airgap_folder)
            with stage("transfer_to_airgap", dst=airgap_folder):
                print(f"[INFO] Transfer ke airgap: {output_folder} -> {airgap_folder}", flush=True)
                transfer_to_airgap(output_folder, airgap_folder)
                print(f"[INFO] Transfer selesai ke {airgap_folder}", flush=True)
            emit("transfer_done", dst=airgap_folder)
        else:
            print("[VHD] Tidak ada drive airgap aktif. Pakai fallback lokal.", flush=True)
            fallback = os.path.join(base_folder, "local_airgap")
            ensure_dir(fallback)
            with stage("transfer_to_airgap_fallback", dst=fallback):
                print(f"[INFO] Transfer ke fallback: {output_folder} -> {fallback}", flush=True)
                transfer_to_airgap(output_folder, fallback)
            emit("transfer_done_fallback", dst=fallback)

        # >>> Karena mount-only, JANGAN paksa unmount pre-mounted
        # if not owned_mount and FORCE_UNMOUNT_AT_END and is_drive_mounted_ps(AIRGAP_DRIVE_LETTER):
        #     print(f"[VHD] Forcing unmount drive {AIRGAP_DRIVE_LETTER} (pre-mounted) ...", flush=True)
        #     force_unmount_airgap_by_drive(AIRGAP_DRIVE_LETTER)

    # === Upload hasil backup ke Google Drive (de-dup) ===
    try:
        emit("upload_backup_start")
        print("[GDRIVE] Upload hasil backup ke folder BackupResults...")
        uploaded_backup = []
        for root, _, files in os.walk(output_folder):
            for f in files:
                path = os.path.join(root, f)
                fid = upload_file_to_drive(
                    gsvc, GDRIVE_BACKUP_FOLDER_ID, path,
                    description=f"uploaded {dt.datetime.now().isoformat()}",
                    on_duplicate="update"
                )
                uploaded_backup.append((path, fid))
        print(f"[GDRIVE] Selesai upload {len(uploaded_backup)} file backup.")
        emit("upload_backup_done", count=len(uploaded_backup))
    except Exception as e:
        emit("upload_backup_error", error=repr(e))
        print(f"[GDRIVE] Upload gagal: {e}")


    # === MODE RANSOMWARE ===
    if args.mode == "wannacry":
        simulated_extension = ".wncry"

        # Emit total file kalau bisa dihitung
        total_files = sum(len(files) for _, _, files in os.walk(output_folder))
        emit("ransom_scan_start", total=total_files)

        #mulai tahap enkripsi
        with stage("ransom_encrypt", ext=simulated_extension):
            encrypted_files = simulate_ransomware_safe(
                source_folder=output_folder,
                extension=simulated_extension
            )
        emit("ransom_simulation_end", count=len(encrypted_files))
        print(f"[WANNA-CRY SIMULATION] {len(encrypted_files)} file terenkripsi di {output_folder} (ext {simulated_extension})")

    # === MODE HEADER CORRUPTION ===
    elif args.mode == "headercorrupt":
        with stage("simulate_header_corruption", header_size=64):
            results = simulate_header_corruption_safe(
                source_folder=output_folder,
                header_size=64,
                extensions=None,
                dry_run=False,
                force=True,
                report_file=os.path.join(base_folder, "header_report.json"),
                show_progress=True
            )
        emit("simulate_header_corruption_done", count=len(results))
        print(f"[HEADER-CORRUPTION SIM] {len(results)} file diproses. Ringkasan tersimpan di header_report.json")


   # === RESTORE dari Drive BackupResults ===
    restore_cache = os.path.join(base_folder, "_restore_cache_drive")
    ensure_dir(restore_cache)

    allowed_exts = [ext.lower() for ext in EXT_TO_ID.keys()]
    print(f"[RESTORE] Mengunduh file backup dari Drive folder BackupResults ({GDRIVE_BACKUP_FOLDER_ID}) ...")

    def download_backup_folder():
        q = f"'{GDRIVE_BACKUP_FOLDER_ID}' in parents and trashed=false"
        page_token = None
        drive_files_seen = set()   # >>> PATCH ORPHAN CHECK

        while True:
            resp = gsvc.files().list(
                q=q,
                fields="nextPageToken, files(id,name,md5Checksum)",
                pageToken=page_token
            ).execute()

            files = resp.get('files', [])
            for it in files:
                name = it["name"]
                drive_files_seen.add(name)

                ext = os.path.splitext(name)[1].lstrip('.').lower()
                if ext not in allowed_exts:
                    continue

                local_path = os.path.join(restore_cache, name)
                remote_md5 = it.get("md5Checksum")

                if remote_md5 and os.path.exists(local_path) and md5_of_file(local_path) == remote_md5:
                    print(f"[GDRIVE] RESTORE SKIP (identik): {name}")
                    continue

                download_drive_file(gsvc, it["id"], local_path)

                if remote_md5 and md5_of_file(local_path) != remote_md5:
                    print(f"[GDRIVE] RESTORE WARNING MD5 mismatch: {name}")

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        # Hapus orphan
        local_files = set(os.listdir(restore_cache))
        orphan_files = local_files - drive_files_seen
        for orphan in orphan_files:
            try:
                os.remove(os.path.join(restore_cache, orphan))
                print(f"[SYNC] Orphan file {orphan} dihapus (tidak ada di Drive).")
            except Exception as e:
                print(f"[SYNC] Gagal hapus orphan {orphan}: {e}")

    with stage("restore_download_backup_folder"):
        download_backup_folder()

    # === Restore file dari cache ke restore_folder ===
    backup_files = []
    for root, _, files in os.walk(restore_cache):
        for file in files:
            if os.path.splitext(file)[1].lstrip('.').lower() in EXT_TO_ID:
                backup_files.append(os.path.join(root, file))

    emit("restore_cache_ready", count=len(backup_files))

    hash_results = []
    for backup_file_path in backup_files:
        try:
            with stage("restore_file", file=os.path.basename(backup_file_path)):
                algo_id, restored_path = restore_file(backup_file_path, restore_folder, restore_cache)

            rel_inside_restore = os.path.relpath(restored_path, restore_folder)
            parts = rel_inside_restore.split(os.sep, 1)
            lookup_key = parts[1] if len(parts) > 1 else parts[0]
            from_hash = hash_memory.get(lookup_key, "")
            restored_hash = get_sha256(restored_path)
            match = restored_hash == from_hash

            print(f"[VALIDATION] {rel_inside_restore} : {'Cocok' if match else 'Tidak Cocok'}")
            hash_results.append((rel_inside_restore, ALGO_DISPLAY[algo_id], from_hash, restored_hash, match))

            emit("restore_validated", file=rel_inside_restore, algo=ALGO_DISPLAY[algo_id],
                 ok=match, sha_in=from_hash, sha_out=restored_hash)

        except Exception as e:
            emit("restore_error", file=os.path.basename(backup_file_path), error=repr(e))
            print(f"Gagal merestore {backup_file_path}: {e}")

    show_all_hash_popup(hash_results, save_folder=restore_folder)
    emit("restore_done", validated=len(hash_results))
    print("Proses restore selesai.")

    # === Evaluasi ringkas ===
    with stage("evaluate_and_save"):
        eval_summary = evaluate_and_save(total_rasio, total_waktu, evaluation_folder)
    print("[DONE] Seluruh proses selesai. Ringkasan evaluation:")
    print(json.dumps(eval_summary, indent=2))
    emit("evaluate_done", summary=eval_summary)

    try:
        save_json(hash_file_path, hash_memory)
        emit("hash_saved", path=hash_file_path)
    except FileNotFoundError:
        fallback_hash = os.path.join(default_local_airgap, HASH_FILE)
        print(f"[WARN] Drive {AIRGAP_DRIVE_LETTER}: tidak ada. Simpan hash ke fallback: {fallback_hash}")
        save_json(fallback_hash, hash_memory)
        emit("hash_saved_fallback", path=fallback_hash)

    emit("pipeline_end")


if __name__ == "__main__":
    main()
