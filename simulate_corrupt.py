# simulate_corrupt.py
import os, random, time
from typing import List, Optional

try:
    from progress import emit
except Exception:
    def emit(event, **data):  # fallback jika progress.py tidak ada
        pass

# ====================== HELPERS ======================
def _safe_tqdm(iterable, use_tqdm: bool, **kwargs):
    try:
        from tqdm import tqdm
        return tqdm(iterable, **kwargs) if use_tqdm else iterable
    except Exception:
        return iterable

def _iter_all_files(root_dir: str):
    for root, _, files in os.walk(root_dir):
        for fn in files:
            yield os.path.join(root, fn)

# ====================== SIMULASI CORRUPT ======================
def simulate_corrupt_safe(
    output_folder: str,
    damage_ratio: float = 0.02,  # 2% isi file ditimpa random byte
    delay: float = 0.05,
    use_tqdm: bool = True,
    include_exts: Optional[list[str]] = None,  # contoh: ["txt","csv"]
) -> List[str]:
    """
    Simulasi kerusakan file acak dengan cara menimpa sebagian byte.
    Tidak menghapus file; hanya memodifikasi sebagian kecil isi file.
    """

    if not os.path.isdir(output_folder):
        emit("simulate_corrupt_error", error="folder_not_found", folder=output_folder)
        raise FileNotFoundError(f"Output folder tidak ditemukan: {output_folder}")

    all_files = list(_iter_all_files(output_folder))
    targets = []
    for path in all_files:
        if include_exts is not None:
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            if ext not in include_exts:
                continue
        targets.append(path)

    total = len(targets)
    emit("simulate_corrupt_start", total=total, folder=output_folder)
    print(f"[SIMULATION] Merusak {total} file di {output_folder}...")

    corrupted_files = []
    fails = 0
    iterator = _safe_tqdm(targets, use_tqdm and total > 0, desc="Corrupting", unit="file")

    for idx, file_path in enumerate(iterator, start=1):
        try:
            size = os.path.getsize(file_path)
            if size == 0:
                continue

            # hitung berapa byte yang akan dirusak
            n_damage = max(1, int(size * damage_ratio))
            with open(file_path, "r+b") as fh:
                for _ in range(n_damage):
                    pos = random.randint(0, size - 1)
                    fh.seek(pos)
                    fh.write(os.urandom(1))  # tulis byte acak

            corrupted_files.append(file_path)
            emit("simulate_corrupt_file", idx=idx, total=total, file=file_path, status="ok")
        except Exception as e:
            fails += 1
            emit("simulate_corrupt_file", idx=idx, total=total, file=file_path, status="error", error=str(e))
            print(f"[ERROR] Gagal corrupt {file_path}: {e}")

        if delay and delay > 0:
            time.sleep(delay)

    emit("simulate_corrupt_done", total_success=len(corrupted_files), total_fail=fails, folder=output_folder)
    print(f"[DONE] {len(corrupted_files)} file berhasil dirusak, {fails} gagal.")
    return corrupted_files
