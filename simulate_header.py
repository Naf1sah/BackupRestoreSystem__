import os
import json
from pathlib import Path
from secrets import token_bytes
from datetime import datetime
from tqdm import tqdm
import time  # opsional untuk simulasi delay

class UnsafeTargetError(Exception):
    pass

def _is_unsafe_target(path: Path) -> bool:
    p = str(path.resolve())
    roots = {"/", "C:\\", "C:\\"}
    if p in roots or path.parent == path:
        return True
    if (path / "DO_NOT_TOUCH").exists():
        return True
    return False

def simulate_header_corruption_safe(
    source_folder,
    header_size=64,
    extensions=None,
    dry_run=False,
    force=False,
    report_file=None,
    show_progress=True,  # <<< tambahkan parameter ini
    delay=0.0            # <<< opsional, untuk simulasi proses
):
    """
    Simulasi korupsi header di-place pada file yang ada di source_folder.
    Progress bar ditampilkan jika show_progress=True.
    """
    src = Path(source_folder)
    if not src.exists():
        raise FileNotFoundError(f"Folder tidak ditemukan: {source_folder}")

    if _is_unsafe_target(src):
        raise UnsafeTargetError("Target terlalu berisiko (root/system folder).")

    if not force and not dry_run:
        raise RuntimeError("Operasi diblokir kecuali force=True. Gunakan dry_run=True untuk cek rencana aksi.")

    # Kumpulkan semua file yang akan diubah
    all_files = []
    for root, _, files in os.walk(src):
        for fname in files:
            fpath = Path(root) / fname
            if extensions and fpath.suffix.lower() not in [e.lower() for e in extensions]:
                continue
            all_files.append(fpath)

    results = []

    # Pilih iterator: pakai tqdm jika show_progress True
    iter_files = tqdm(all_files, desc="Simulasi header corruption", unit="file") if show_progress else all_files

    for fpath in iter_files:
        if dry_run:
            results.append({'file': str(fpath), 'bytes_changed': header_size, 'status': 'dry_run'})
            continue

        try:
            with open(fpath, "r+b") as f:
                original_header = f.read(header_size)
                f.seek(0)
                corrupted_header = token_bytes(min(header_size, max(1, len(original_header))))
                f.write(corrupted_header)

            results.append({
                'file': str(fpath),
                'bytes_changed': len(original_header),
                'status': 'ok'
            })
        except Exception as e:
            results.append({
                'file': str(fpath),
                'bytes_changed': 0,
                'status': f'error: {e}'
            })

        if delay > 0:
            time.sleep(delay)  # opsional untuk efek progres

    # Tulis report jika diminta
    if report_file:
        try:
            report_path = Path(report_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as rf:
                json.dump({'timestamp': datetime.utcnow().isoformat()+"Z", 'entries': results}, rf, indent=2)
        except Exception as e:
            results.append({'file': None, 'bytes_changed': 0, 'status': f'warning: gagal tulis report: {e}'})

    return results
