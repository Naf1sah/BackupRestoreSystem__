import os
import json
import hashlib
import shutil
from datetime import datetime
import subprocess
from pathlib import Path
import matplotlib.pyplot as plt

from config import EVAL_FILE

# ---------- JSON Helpers ----------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

# ---------- Hash & Normalization ----------
def get_sha256(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def shorten_hash(h):
    return h if len(h) <= 20 else h[:10] + "..." + h[-10:]

def normalize_algo(algo: str) -> str:
    a = (algo or "").lower()
    if a in ("lz4",): return "lz4"
    if a in ("zstd","zst"): return "zstd"
    if a in ("gzip","gz"): return "gzip"
    if a in ("brotli","br"): return "brotli"
    if a in ("snappy",): return "snappy"
    raise ValueError(f"Algoritma tidak dikenali: {algo}")

# ---------- FS helpers ----------
def ensure_dir(p: str):
    Path(p).mkdir(parents=True, exist_ok=True)

def sync_raw_to_source(raw_dir: str, source_dir: str, exclude_names=None):
    """Isi ulang source_dir dari raw_dir (hapus isi dulu, skip excluded)."""
    raw_dir = Path(raw_dir)
    source_dir = Path(source_dir)
    exclude = set(exclude_names or [])
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data folder tidak ditemukan: {raw_dir}")

    if source_dir.exists():
        for item in source_dir.iterdir():
            if item.name in exclude:
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink(missing_ok=True)
    else:
        source_dir.mkdir(parents=True, exist_ok=True)

    for item in raw_dir.iterdir():
        if item.name in exclude:
            continue
        dst = source_dir / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)

# ---------- Drive/VHD ----------
def is_drive_mounted(drive_letter: str) -> bool:
    return os.path.exists(f"{drive_letter}:\\") if drive_letter else False

def find_vhdx_in_folder(folder: str, prefix: str = "airgap"):
    found = []
    for root, _, files in os.walk(folder):
        for fn in files:
            if fn.lower().endswith(".vhdx") and fn.lower().startswith(prefix.lower()):
                found.append(os.path.join(root, fn))
    return found

def attempt_mount_vhdx(vhdx_path: str) -> bool:
    try:
        cmd = ["powershell", "-Command", f"Mount-VHD -Path \"{vhdx_path}\" -ErrorAction Stop"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[VHD] Berhasil mount {vhdx_path}")
            return True
        print(f"[VHD] Gagal mount {vhdx_path}. Output: {res.stdout} {res.stderr}")
        return False
    except Exception as e:
        print(f"[VHD] Exception saat mount VHDX: {e}")
        return False

# ---------- Visualization & Evaluation ----------
def show_all_hash_popup(hash_results, save_folder=None):
    """
    hash_results: list of (file_rel_with_algo_folder, algo_display, original_hash, restored_hash, match_bool)
    """
    n_rows = len(hash_results) + 1
    fig_height = max(4, min(40, 0.35 * n_rows))
    fig, ax = plt.subplots(figsize=(12, fig_height))
    ax.axis('off')
    ax.set_title("Hasil Perbandingan Hash Semua File", fontsize=14, fontweight='bold')
    columns = ["File", "Algoritma", "Hash Asli", "Hash Restore", "Status"]
    table_data = [columns]
    for fname, algo, orig, restored, match in hash_results:
        table_data.append([fname, algo, shorten_hash(orig), shorten_hash(restored), "Cocok" if match else "Tidak Cocok"])

    table = ax.table(cellText=table_data, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    font_size = max(6, min(10, int(150 / max(10, n_rows))))
    table.set_fontsize(font_size)
    table.scale(1.2, 1.0 + n_rows * 0.02)

    for j in range(len(columns)): 
        table[(0, j)].set_facecolor("#cccccc")
    for i in range(1, len(table_data)):
        table[(i, 4)].set_facecolor("#c8e6c9" if "Cocok" in table_data[i][4] else "#ffcdd2")

    plt.tight_layout()
    if save_folder:
        ensure_dir(save_folder)
        save_path = os.path.join(save_folder, "hash_match_results.png")
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Hasil hash match disimpan di: {save_path}")
    plt.show()

def _safe_name(p: str) -> str:
    return p.replace("\\", "_").replace("/", "_")

def save_per_file_plot(rel_path, algoritma_list, rasio_list, waktu_list, evaluation_folder):
    ensure_dir(evaluation_folder)
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    fig.suptitle(f"Hasil Kompresi: {rel_path}", fontsize=12, fontweight="bold")
    axs[0].bar(algoritma_list, rasio_list); axs[0].set_title("Rasio Kompresi"); axs[0].set_ylabel("Hasil/Asli")
    axs[1].bar(algoritma_list, waktu_list); axs[1].set_title("Waktu Kompresi"); axs[1].set_ylabel("Detik")
    plt.tight_layout()
    out_png = os.path.join(evaluation_folder, f"{_safe_name(rel_path)}.png")
    fig.savefig(out_png, dpi=120)
    plt.close(fig)

def evaluate_and_save(total_rasio, total_waktu, eval_folder):
    import statistics
    ensure_dir(eval_folder)
    summary = {"generated_at": datetime.now().isoformat(), "algorithms": {}}
    for alg, ratios in total_rasio.items():
        times = total_waktu.get(alg, [])
        if ratios:
            summary["algorithms"][alg] = {
                "count": len(ratios),
                "ratio_avg": statistics.mean(ratios),
                "ratio_median": statistics.median(ratios),
                "ratio_min": min(ratios),
                "ratio_max": max(ratios),
                "time_avg": statistics.mean(times) if times else None,
                "time_median": statistics.median(times) if times else None,
            }
        else:
            summary["algorithms"][alg] = {"count": 0}
    save_json(os.path.join(eval_folder, EVAL_FILE), summary)
    print(f"[EVAL] Evaluation summary tersimpan di {os.path.join(eval_folder, EVAL_FILE)}")
    return summary
