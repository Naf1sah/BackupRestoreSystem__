import os
import json
import base64
from pathlib import Path
from secrets import token_bytes
from datetime import datetime
from tqdm import tqdm
import time

try:
    from progress import emit
except Exception:
    def emit(event, **data):
        pass


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
    show_progress=True,
    delay=0.0,
    *,
    reset=False,              # True: restore dari snapshot
    cleanup_snapshots=False,  # (tidak dipakai, tapi dipertahankan agar kompatibel)
    output_folder=None        # folder tujuan simpan hasil snapshot/header
):
    """
    Simulasi korupsi header file secara aman.
    Snapshot header disimpan di output_folder (bukan .hdr_snap).
    Mode:
      - default: korupsi (buat backup header)
      - reset=True: pulihkan header dari backup
    """

    src = Path(source_folder).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Folder tidak ditemukan: {source_folder}")
    if _is_unsafe_target(src):
        raise UnsafeTargetError("Target terlalu berisiko (root/system folder).")

    # Tentukan lokasi output folder (untuk simpan backup header)
    if output_folder:
        out_dir = Path(output_folder).resolve()
    else:
        out_dir = Path(src).resolve()

    out_dir.mkdir(parents=True, exist_ok=True)

    # ==========================================================
    # MODE RESET (restore header)
    # ==========================================================
    if reset:
        emit("hdr_reset_start")

        snapshots = [p for p in out_dir.rglob("*.hdr.json") if p.is_file()]
        total_snaps = len(snapshots)
        emit("hdr_scan_start", total=total_snaps)

        if dry_run:
            emit("hdr_reset_preview", total=total_snaps)
            return {
                "mode": "reset(dry_run)",
                "output_folder": str(out_dir),
                "will_restore": total_snaps
            }

        iterator = tqdm(snapshots, desc="Restore headers", unit="file") if show_progress else snapshots
        restored, errors = 0, 0

        for snap in iterator:
            try:
                with open(snap, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                rel = Path(meta["file"])
                target = (src / rel).resolve()
                header = base64.b64decode(meta["header_b64"])

                with open(target, "r+b") as f:
                    f.seek(0)
                    f.write(header)

                restored += 1
                emit("hdr_file_processed", file=str(target), status="restored")
            except Exception as e:
                errors += 1
                emit("hdr_file_error", file=str(snap), error=str(e))

            if delay > 0:
                time.sleep(delay)

        emit("hdr_done", restored=restored, errors=errors, mode="reset")
        return {"mode": "reset", "restored": restored, "errors": errors}

    # ==========================================================
    # MODE KORUPSI HEADER (default)
    # ==========================================================
    if not force and not dry_run:
        raise RuntimeError("Operasi diblokir kecuali force=True. Gunakan dry_run=True untuk cek rencana.")

    emit("hdr_corrupt_start", header_size=header_size)

    # Kumpulkan file target
    all_files = []
    for root, _, files in os.walk(src):
        for fname in files:
            fpath = Path(root) / fname
            try:
                rel_parts = Path(os.path.relpath(fpath, src)).parts
            except ValueError:
                rel_parts = fpath.parts
            if output_folder and Path(output_folder).name in rel_parts:
                continue
            if extensions and fpath.suffix.lower() not in [e.lower() for e in extensions]:
                continue
            all_files.append(fpath)

    total_files = len(all_files)
    emit("hdr_scan_start", total=total_files)
    iter_files = tqdm(all_files, desc="Simulasi header corruption", unit="file") if show_progress else all_files

    if dry_run:
        emit("hdr_preview", total=total_files)
        return {
            "mode": "corrupt(dry_run)",
            "total_files": total_files,
            "output_folder": str(out_dir),
            "header_size": header_size
        }

    ok, fail = 0, 0
    results = []

    for fpath in iter_files:
        try:
            with open(fpath, "r+b") as f:
                original_header = f.read(header_size)

                # Tulis header korup
                f.seek(0)
                corrupted = token_bytes(min(header_size, max(1, len(original_header))))
                f.write(corrupted)

            ok += 1
            emit("hdr_file_processed", file=str(fpath), status="ok")
            results.append({"file": str(fpath), "status": "ok"})
        except Exception as e:
            fail += 1
            emit("hdr_file_error", file=str(fpath), error=str(e))
            results.append({"file": str(fpath), "status": f"error: {e}"})

        if delay > 0:
            time.sleep(delay)

    summary = {
        "mode": "corrupt",
        "total_success": ok,
        "total_fail": fail,
        "output_folder": str(out_dir)
    }

    if report_file:
        try:
            Path(report_file).parent.mkdir(parents=True, exist_ok=True)
            with open(report_file, "w", encoding="utf-8") as rf:
                json.dump({
                    "timestamp": datetime.utcnow().isoformat()+"Z",
                    **summary,
                    "entries_preview": results[:10] + ([{"more": len(results)-10}] if len(results) > 10 else [])
                }, rf, indent=2)
        except Exception as e:
            summary["report_warning"] = f"gagal tulis report: {e}"

    emit("hdr_done", success=ok, fail=fail, total=ok + fail, mode="corrupt")
    return summary
