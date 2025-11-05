import os, json, re, pathlib, datetime as dt
from flask import Blueprint, jsonify
from simulate_header import simulate_header_corruption_safe

bp = Blueprint("api", __name__)
LOG_PATH = pathlib.Path(os.getenv("PROGRESS_LOG_PATH", "progress_events.jsonl"))


# ---------- FUNGSI MEMBACA LOG ----------
def _iter_events():
    if not LOG_PATH.exists():
        return
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


# ---------- /api/summary ----------
@bp.route("/summary")
def api_summary():
    files_seen = {}
    backup = {}
    restore = {}
    counts = {"errors": 0}
    algo_names = {"lz4", "zstd", "gzip", "brotli", "snappy"}

    for ev in _iter_events() or []:
        typ = (ev.get("event") or ev.get("type") or "").strip()
        data = ev.get("data") or {}
        f = data.get("file") or data.get("filepath") or data.get("name") or data.get("rel_path")

        if typ == "hash_original":
            if not f: continue
            files_seen[f] = {"size": int(data.get("size", 0) or 0), "sha": str(data.get("sha256", "") or "")}

        elif typ == "backup_result":
            algo = data.get("algo")
            if not f or not algo: continue
            backup[(f, str(algo))] = {
                "ratio": float(data.get("ratio")) if data.get("ratio") is not None else None,
                "dur": float(data.get("duration_ms")) if data.get("duration_ms") is not None else None,
            }

        elif typ == "restore_validated":
            if not f: continue
            parts = re.split(r"[\\/]+", f)
            if len(parts) >= 2 and parts[0].lower() in algo_names:
                f = parts[1]
            algo = str(data.get("algo") or (parts[0] if parts else ""))
            if not algo: continue

            bucket = restore.setdefault(f, {})
            bucket[algo] = {
                "algo": algo,
                "ok": bool(data.get("ok")),
                "sha_in": data.get("sha_in"),
                "sha_out": data.get("sha_out"),
            }

        elif typ.endswith("_error"):
            counts["errors"] += 1

    total_files = len(files_seen)
    total_backup_pairs = len(backup)
    total_restore = sum(len(v) for v in restore.values())
    total_restore_ok = sum(1 for v in restore.values() for x in v.values() if x["ok"])

    per_file = []
    for f, meta in files_seen.items():
        algos = sorted({a for (ff, a) in backup.keys() if ff == f})
        ratios = {a: backup.get((f, a), {}).get("ratio") for a in algos}
        durs = {a: backup.get((f, a), {}).get("dur") for a in algos}
        r_dict = restore.get(f, {})
        r_list = list(r_dict.values())
        ok_count = sum(1 for x in r_list if x["ok"])
        per_file.append({
            "file": f,
            "size": meta["size"],
            "sha": meta["sha"],
            "algos": algos,
            "ratios": ratios,
            "durations": durs,
            "restore": r_list,
            "restore_ok": ok_count,
            "restore_total": len(r_list),
            "restore_ok_pct": (ok_count / len(r_list) * 100.0) if r_list else None
        })

    return jsonify({
        "global": {
            "total_files": total_files,
            "total_backup_pairs": total_backup_pairs,
            "total_restore": total_restore,
            "total_restore_ok": total_restore_ok,
            "errors": counts["errors"],
        },
        "files": per_file
    })


# ---------- /api/events ----------
@bp.route("/events")
def api_events():
    return jsonify(list(_iter_events() or [])[-200:])


# ---------- /api/ransom_status ----------
@bp.route("/ransom_status")
def api_ransom_status():
    total_files = 0
    encrypted = 0
    decrypted = 0
    running = False
    last_event = None

    for ev in _iter_events() or []:
        typ = ev.get("event")
        data = ev.get("data") or {}

        if typ == "ransom_scan_start":
            total_files = data.get("total", 0)
            running = True

        elif typ in ("simulate_ransomware_file", "ransom_encrypt_done", "encrypt_end"):
            encrypted += 1
            running = True

        elif typ in ("ransom_simulation_end", "simulate_ransomware_done"):
            encrypted = data.get("count", encrypted)
            running = False


        elif typ in ("ransom_decrypt_done", "decrypt_end"):
            decrypted += 1
        
        last_event = typ

    return jsonify({
    "total": total_files,
    "encrypted": encrypted,
    "decrypted": decrypted,
    "running": running,
    "last_event": last_event

})



# ---------- /api/header_status ----------
@bp.route("/header_status")
def api_header_status():
    status = "No report found"
    total_success = 0
    total_fail = 0
    mode = "unknown"

    for ev in _iter_events() or []:
        typ = ev.get("event") or ev.get("type")
        data = ev.get("data") or {}

        if typ in ("header_reset","system_start", "start_normal_mode"):
            total_success = 0
            total_fail = 0
            continue

        if typ == "hdr_corrupt_start":
            status = "Running"
        elif typ == "hdr_done":
            total_success = data.get("success", 0)
            total_fail = data.get("fail", 0)
            mode = data.get("mode", "unknown")
            status = "Done"
        elif typ == "hdr_corrupt_error":
            status = "Error"

    return jsonify({
        "status": status,
        "total_success": total_success,
        "total_fail": total_fail,
        "mode": mode
    })

# ---------- /api/corrupt_status ----------
@bp.route("/corrupt_status")
def api_corrupt_status():
    """
    Endpoint untuk membaca status simulasi corrupt dari log event progress.
    Akan menampilkan status saat ini (Running, Done, Error, dll)
    beserta jumlah file berhasil/gagal dirusak.
    """
    status = "No report found"
    total_success = 0
    total_fail = 0
    folder = "unknown"
    mode = "corrupt_simulation"

    # membaca event dari log
    for ev in _iter_events() or []:
        typ = ev.get("event") or ev.get("type")
        data = ev.get("data") or {}

        # reset status bila ada event sistem normal
        if typ in ("simulate_corrupt_error", "system_start", "start_normal_mode"):
            total_success = 0
            total_fail = 0
            continue

        if typ == "simulate_corrupt_start":
            status = "Running"
            folder = data.get("folder", "unknown")

        elif typ == "simulate_corrupt_file":
            # optional: bisa dipakai untuk hitung progress per file
            pass

        elif typ == "simulate_corrupt_done":
            total_success = data.get("total_success", 0)
            total_fail = data.get("total_fail", 0)
            folder = data.get("folder", folder)  # pastikan folder tetap terisi
            status = "Done"

        elif typ == "simulate_corrupt_error":
            status = "Error"

    return jsonify({
        "status": status,
        "total_success": total_success,
        "total_fail": total_fail,
        "folder": folder,
        "mode": mode
    })


# ---------- /api/ransom_alert ----------
@bp.route("/ransom_alert")
def api_ransom_alert():
    """
    Endpoint untuk membaca log deteksi ransomware terbaru dari file CSV.
    Digunakan untuk menampilkan notifikasi pop-up di dashboard.
    """
    import csv

    csv_log_path = pathlib.Path(r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\Data\ransomware_detected_files.csv")
    alerts = []

    if csv_log_path.exists():
        with csv_log_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                alerts.append({
                    "file_path": row.get("file_path"),
                    "status": row.get("status"),
                    "timestamp": row.get("timestamp")
                })

    return jsonify({
        "count": len(alerts),
        "latest": alerts[-1] if alerts else None,
        "all": alerts
    })
