import os
import time
import gzip
import lz4.frame
import zstandard as zstd
import brotli
import snappy
import shutil
from tqdm import tqdm

from config import ALGO_DISPLAY, EXT_TO_ID
from utils import normalize_algo

# ---------- BACKUP ----------

def backup_file(path, algo, output_folder, original_hash, source_folder, chunk_size=4*1024*1024):
    algo = normalize_algo(algo)
    relative_path = os.path.relpath(path, source_folder)
    
    # Folder untuk masing-masing algoritma
    algo_folder = os.path.join(output_folder, ALGO_DISPLAY[algo])
    os.makedirs(algo_folder, exist_ok=True)
    
    comp_ext = {
        'lz4': '.lz4',
        'zstd': '.zst',
        'gzip': '.gz',
        'brotli': '.br',
        'snappy': '.snappy'
    }[algo]
    comp_path = os.path.join(algo_folder, relative_path + comp_ext)
    os.makedirs(os.path.dirname(comp_path), exist_ok=True)

    # Salin file asli juga ke folder original
    original_folder = os.path.join(output_folder, "original")
    os.makedirs(os.path.dirname(os.path.join(original_folder, relative_path)), exist_ok=True)
    shutil.copy2(path, os.path.join(original_folder, relative_path))

    file_size = os.path.getsize(path)
    print(f"[INFO] {time.strftime('%H:%M:%S')} - Mulai kompresi: {relative_path} ({file_size/1024/1024:.2f} MB) | Algo: {algo.upper()}")

    start_time = time.time()
    total_bytes = 0

    with open(path, 'rb') as fin, tqdm(total=file_size, unit='B', unit_scale=True, desc=f"[{algo.upper()}]") as pbar:
        if algo == 'lz4':
            with lz4.frame.open(comp_path, mode='wb') as fout:
                for chunk in iter(lambda: fin.read(chunk_size), b''):
                    fout.write(chunk)
                    total_bytes += len(chunk)
                    pbar.update(len(chunk))
        elif algo == 'zstd':
            cctx = zstd.ZstdCompressor()
            with open(comp_path, 'wb') as fout, cctx.stream_writer(fout) as compressor:
                for chunk in iter(lambda: fin.read(chunk_size), b''):
                    compressor.write(chunk)
                    total_bytes += len(chunk)
                    pbar.update(len(chunk))
        elif algo == 'gzip':
            with gzip.open(comp_path, 'wb') as fout:
                for chunk in iter(lambda: fin.read(chunk_size), b''):
                    fout.write(chunk)
                    total_bytes += len(chunk)
                    pbar.update(len(chunk))
        elif algo == 'brotli':
            compressor = brotli.Compressor()
            with open(comp_path, 'wb') as fout:
                for chunk in iter(lambda: fin.read(chunk_size), b''):
                    fout.write(compressor.process(chunk))
                    total_bytes += len(chunk)
                    pbar.update(len(chunk))
                fout.write(compressor.finish())
        elif algo == 'snappy':
            with open(comp_path, 'wb') as fout:
                for chunk in iter(lambda: fin.read(chunk_size), b''):
                    fout.write(snappy.compress(chunk))
                    total_bytes += len(chunk)
                    pbar.update(len(chunk))

    duration = time.time() - start_time
    print(f"[DONE] {algo.upper()} selesai! {total_bytes/1024/1024:.2f} MB dibaca. Waktu: {duration:.2f} detik.\n")

    # Simpan hash untuk file kompresi
    with open(comp_path + ".hash", "w", encoding="utf-8") as hf:
        hf.write(original_hash)

    return comp_path, duration


# ---------- TRANSFER AIRGAP (COPY) ----------

def transfer_to_airgap(output_folder, airgap_root):
    os.makedirs(airgap_root, exist_ok=True)
    transferred = []
    for root, _, files in os.walk(output_folder):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(src, output_folder)
            dst = os.path.join(airgap_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            transferred.append((src, dst))
    print(f"[AIRGAP] {len(transferred)} file dicopy ke {airgap_root}")
    return transferred

# ---------- RESTORE_FILE----------
def restore_file(comp_path, restore_folder, base_folder):
    algo_ext = os.path.splitext(comp_path)[1].lstrip(".").lower()
    if algo_ext not in EXT_TO_ID:
        raise ValueError(f"Ekstensi {algo_ext} tidak dikenali.")

    algo_id = EXT_TO_ID[algo_ext]

    # Ambil hanya nama file tanpa folder algoritma
    file_name_only = os.path.splitext(os.path.basename(comp_path))[0]

    # Simpan ke restore_folder/AlgoName/file_name_only
    target_path = os.path.join(restore_folder, ALGO_DISPLAY[algo_id], file_name_only)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    if algo_id == "lz4":
        with lz4.frame.open(comp_path, "rb") as fin, open(target_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    elif algo_id == "zstd":
        dctx = zstd.ZstdDecompressor()
        with open(comp_path, "rb") as fin, open(target_path, "wb") as fout:
            dctx.copy_stream(fin, fout)
    elif algo_id == "gzip":
        with gzip.open(comp_path, "rb") as fin, open(target_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    elif algo_id == "brotli":
        with open(comp_path, "rb") as fin, open(target_path, "wb") as fout:
            import brotli as _brotli
            fout.write(_brotli.decompress(fin.read()))
    elif algo_id == "snappy":
        with open(comp_path, "rb") as fin, open(target_path, "wb") as fout:
            import snappy as _snappy
            fout.write(_snappy.decompress(fin.read()))

    return algo_id, target_path
