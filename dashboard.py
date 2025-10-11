# dashboard.py
import os, json, pathlib, re, datetime as dt
from flask import Flask, jsonify, render_template_string

LOG_PATH = pathlib.Path(os.getenv("PROGRESS_LOG_PATH", "progress_events.jsonl"))  ##mengambil file log dari environment variable
app = Flask(__name__)

## membaca file log JSONL baris demi baris dan mengembalikan data JSON satu-persatu
def _iter_events():
    if not LOG_PATH.exists():
        return
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line) ##ubah teks JSON jadi objek python (dictionary)
            except Exception:
                continue

@app.route("/api/summary") ##fungsi flask, endpoint API flask yang bisa diakses lewat URL
def api_summary():
    files_seen = {} ##menyimpan info semua file yang pernah di-hash (file asli)
    backup = {} ## menyimpan hasil backup tiap file dan algoritma kompresinya
    restore = {} ## menyimpan hasil restore tiap file dan algoritma
    counts = {"errors": 0} ## menyimpan total error yang terjadi
    algo_names = {"lz4", "zstd", "gzip", "brotli", "snappy"} ## Daftar nama algoritma kompresi yang dikenali

    for ev in _iter_events() or []: ## ev dictionary hasil parsing JSON dari log
        typ = (ev.get("event") or ev.get("type") or "").strip() ##jenis event 
        data = ev.get("data") or {} ## isi data detail dari event
        f = data.get("file") or data.get("filepath") or data.get("name") or data.get("rel_path")

        if typ == "hash_original":
            if not f:
                continue
            files_seen[f] = {
                "size": int(data.get("size", 0) or 0),
                "sha": str(data.get("sha256", "") or "")
            }

        elif typ == "backup_result":
            algo = data.get("algo")
            if not f or not algo:
                continue
            backup[(f, str(algo))] = {
                "ratio": float(data.get("ratio")) if data.get("ratio") is not None else None,
                "dur": float(data.get("duration_ms")) if data.get("duration_ms") is not None else None,
            }

        elif typ == "restore_validated":
            if not f:
                continue
            parts = re.split(r"[\\/]+", f)  ##melakukan pemecahan agar nama algoritma dan file nya terpisah
            if len(parts) >= 2 and parts[0].lower() in algo_names:
                f = parts[1]
            algo = str(data.get("algo") or (parts[0] if parts else ""))
            if not algo:
                continue

            bucket = restore.setdefault(f, {})
            bucket[algo] = {  ## didalam file f, simpan hasil restore untuk nama algoritma
                ## isinya
                "algo": algo,
                "ok": bool(data.get("ok")),
                "sha_in": data.get("sha_in"),
                "sha_out": data.get("sha_out"),
            }

        elif typ.endswith("_error"):
            counts["errors"] += 1

    ##summary 
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
        per_file.append({ ##semua info simpan disini
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


@app.route("/api/events") ##endpoint API yang berfungsi menampilkan daftar terakhir dari log
def api_events():
    return jsonify(list(_iter_events() or [])[-200:])


# ---------- HALAMAN INTERAKTIF FRONTEND ----------
PAGE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Mini Monitor (Interaktif)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
  <style>
    :root { --card:#fff; --border:#eee; --ink:#222; --muted:#666; --bg:#fafafa; }
    html { font-size: 14px; }
    body{font-family:system-ui, Arial, sans-serif; margin:16px; color:var(--ink); background:var(--bg);}
    .container { max-width: 1100px; margin: 0 auto; }
    h1{margin:6px 0 12px; font-size:1.6rem;}
    .grid{display:grid; grid-template-columns: repeat(auto-fit, minmax(260px,1fr)); gap:10px;}
    .card{background:var(--card); border:1px solid var(--border); border-radius:10px; padding:10px; box-shadow:0 1px 3px rgba(0,0,0,.05);}
    .muted{color:var(--muted);}
    table{width:100%; border-collapse:collapse; margin-top:6px; font-size:0.9rem;}
    th,td{border-bottom:1px solid var(--border); padding:6px 8px; text-align:left;}
    th{background:#f8f8f8;}
    code{background:#f3f3f3; padding:1px 4px; border-radius:4px;}
    .ok{color:#0a7; font-weight:600;}
    .bad{color:#c33; font-weight:600;}
    .row{display:grid; grid-template-columns: 1fr 1fr; gap:10px;}
    @media (max-width:900px){ .row{grid-template-columns: 1fr;} }
    select{padding:6px 8px; border-radius:8px; border:1px solid var(--border); background:#fff;}
    .hint{font-size:0.8rem; color:var(--muted);}
    .chart-sm { height: 200px !important; max-height: 200px !important; }
    .card canvas{ display:block; }
  </style>
</head>
<body>
  <div class="container">
    <h1>DASHBOARD</h1>

    <div class="grid">
      <div class="card">
        <h3>Ringkasan</h3>
        <div id="summary">memuat…</div>
        <div class="hint">Auto refresh setiap 5 detik.</div>
      </div>

      <div class="card">
        <h3>Simulasi Ransomware</h3>
        <canvas id="ransomChart" class="chart-sm"></canvas>
        <div id="ransomStat" class="muted" style="margin-top:6px;">memuat…</div>
      </div>

      <div class="card">
        <h3>Pilih File</h3>
        <select id="fileSel"></select>
        <div id="fileMeta" class="muted" style="margin-top:6px;"></div>
      </div>

      <div class="card">
        <h3>Ringkasan Validasi</h3>
        <canvas id="restorePie" class="chart-sm"></canvas>
      </div>
    </div>

    <div class="row" style="margin-top:12px;">
      <div class="card">
        <h3>Rasio Kompresi (hasil/asli)</h3>
        <canvas id="ratioBar" class="chart-sm"></canvas>
      </div>
      <div class="card">
        <h3>Waktu Kompresi (ms)</h3>
        <canvas id="durBar" class="chart-sm"></canvas>
      </div>
    </div>

    <div class="row" style="margin-top:12px;">
      <div class="card">
        <h3>File Baru</h3>
        <div id="filesBox">memuat…</div>
      </div>
      <div class="card">
        <h3>Status Restore</h3>
        <div id="restoreBox">memuat…</div>
      </div>
    </div>
  </div>

## JAVASCRIPT
<script>
let state = null;
let ratioChart, durChart, pieChart;

function fmtMB(b){ return (b/1024/1024).toFixed(2)+' MB'; }

function renderSummary(g){
  const el = document.getElementById('summary');
  el.innerHTML = `
    <b>Total file:</b> ${g.total_files} &nbsp; | &nbsp;
    <b>Backup pairs:</b> ${g.total_backup_pairs} &nbsp; | &nbsp;
    <b>Restore OK:</b> ${g.total_restore_ok}/${g.total_restore} &nbsp; | &nbsp;
    <b>Error:</b> ${g.errors}
  `;
}

/* Jangan rebuild <select> kalau isinya sama → cegah layout shift */
function fillFileSelector(files){
  const sel = document.getElementById('fileSel');
  const current = Array.from(sel.options).map(o=>o.value).join('|');
  const incoming = files.map(f=>f.file).join('|');
  if(current === incoming) return;

  sel.innerHTML = '';
  if(files.length === 0){
    const opt = document.createElement('option');
    opt.textContent = 'Belum ada file';
    sel.appendChild(opt);
    return;
  }
  for(const f of files){
    const o = document.createElement('option');
    o.value = f.file;
    o.textContent = f.file;
    sel.appendChild(o);
  }
}

function renderFileMeta(f){
  const m = document.getElementById('fileMeta');
  if(!f){ m.textContent=''; return; }
  m.innerHTML = `<b>Ukuran:</b> ${fmtMB(f.size)} &nbsp; | &nbsp; <b>SHA256</b> <code>${(f.sha||'').slice(0,16)}…</code>`;
}

function ensureCharts(){
  const commonOpts = {
    responsive:true,
    maintainAspectRatio:false,
    animation:false,            // stabil, tidak “lari”
    resizeDelay:0,
    plugins:{ legend:{ display:false } },
    scales:{
      x:{ ticks:{ autoSkip:true, maxRotation:0 } },
      y:{ beginAtZero:true }
    }
  };

  if(!ratioChart){
    ratioChart = new Chart(document.getElementById('ratioBar'), {
      type:'bar',
      data:{labels:[], datasets:[{label:'ratio', data:[]}]},
      options:{ ...commonOpts, scales:{
        x: commonOpts.scales.x,
        y:{ beginAtZero:true, suggestedMax:1 }
      }}
    });
  }
  if(!durChart){
    durChart = new Chart(document.getElementById('durBar'), {
      type:'bar',
      data:{labels:[], datasets:[{label:'duration_ms', data:[]}]},
      options: commonOpts
    });
  }
  if(!pieChart){
    pieChart = new Chart(document.getElementById('restorePie'), {
      type:'doughnut',
      data:{labels:['OK','FAIL'], datasets:[{data:[0,0]}]},
      options: { ...commonOpts }
    });
  }
}

function renderChartsForFile(f){
  ensureCharts();
  if(!f){
    ratioChart.data.labels = [];
    ratioChart.data.datasets[0].data = [];
    ratioChart.update();

    durChart.data.labels = [];
    durChart.data.datasets[0].data = [];
    durChart.update();

    pieChart.data.datasets[0].data = [0,0];
    pieChart.update();
    return;
  }

  const labels = f.algos || [];
  const ratios = labels.map(a => f.ratios[a] ?? null);
  const durs   = labels.map(a => f.durations[a] ?? null);

  ratioChart.data.labels = labels;
  ratioChart.data.datasets[0].data = ratios;
  ratioChart.update();

  durChart.data.labels = labels;
  durChart.data.datasets[0].data = durs;
  durChart.update();

  const ok = f.restore_ok || 0;
  const tot = f.restore_total || 0;
  pieChart.data.datasets[0].data = [ok, Math.max(0, tot-ok)];
  pieChart.update();
}

function renderFilesTable(files){
  const box = document.getElementById('filesBox');
  if(files.length===0){ box.textContent = 'Belum ada.'; return; }
  let html = '<table><tr><th>File</th><th>Ukuran</th><th>SHA256</th></tr>';
  for(const f of files){
    html += `<tr><td>${f.file}</td><td>${fmtMB(f.size)}</td><td><code>${(f.sha||'').slice(0,16)}…</code></td></tr>`;
  }
  html += '</table>';
  box.innerHTML = html;
}

function renderRestoreTable(files){
  const box = document.getElementById('restoreBox');
  const rows = [];
  for(const f of files){
    for(const r of (f.restore||[])){
      rows.push(`<tr>
        <td>${f.file}</td>
        <td>${r.algo||'-'}</td>
        <td class="${r.ok?'ok':'bad'}">${r.ok?'OK':'FAIL'}</td>
      </tr>`);
    }
  }
  if(rows.length===0){ box.textContent = 'Belum ada.'; return; }
  box.innerHTML = `<table><tr><th>File</th><th>Algo</th><th>OK?</th></tr>${rows.join('')}</table>`;
}

async function refresh(){
  const prevY = window.scrollY;  // simpan posisi scroll agar tidak “loncat” saat refresh

  const r = await fetch('/api/summary');
  state = await r.json();

  renderSummary(state.global);
  fillFileSelector(state.files);

  const sel = document.getElementById('fileSel');
  const f = state.files.find(x=> x.file === sel.value) || state.files[0];
  if(f){ sel.value = f.file; }
  renderFileMeta(f);
  renderChartsForFile(f);
  renderFilesTable(state.files);
  renderRestoreTable(state.files);

  // kembalikan posisi scroll
  window.scrollTo({ top: prevY, left: 0, behavior: 'instant' });
}

let ransomChart;

async function refreshRansom(){
  const r = await fetch('/api/ransom_status');
  const data = await r.json();

  const el = document.getElementById('ransomStat');
  if(!data.total){
    el.textContent = "Belum ada aktivitas ransomware.";
    if(ransomChart){ ransomChart.destroy(); ransomChart = null; }
    return;
  }

  el.innerHTML = `
    <b>Total File:</b> ${data.total} &nbsp; | &nbsp;
    <b>Terenkripsi:</b> ${data.encrypted} &nbsp; | &nbsp;
    <b>Didekripsi:</b> ${data.decrypted} &nbsp; | &nbsp;
    <b>Status:</b> ${data.running ? '<span class="bad">Berjalan</span>' : '<span class="ok">Selesai</span>'}
  `;

  const donePct = data.total ? (data.encrypted / data.total * 100).toFixed(1) : 0;
  const decPct  = data.total ? (data.decrypted / data.total * 100).toFixed(1) : 0;

  if(!ransomChart){
    ransomChart = new Chart(document.getElementById('ransomChart'), {
      type:'doughnut',
      data:{
        labels:['Encrypted','Decrypted','Remaining'],
        datasets:[{data:[
          data.encrypted,
          data.decrypted,
          Math.max(0, data.total - data.encrypted - data.decrypted)
        ]}]
      },
      options:{
        responsive:true,
        maintainAspectRatio:false,
        plugins:{ legend:{ display:true } }
      }
    });
  } else {
    ransomChart.data.datasets[0].data = [
      data.encrypted,
      data.decrypted,
      Math.max(0, data.total - data.encrypted - data.decrypted)
    ];
    ransomChart.update();
  }
}

setInterval(refreshRansom, 3000);
refreshRansom();


document.addEventListener('change', (ev)=>{
  if(ev.target && ev.target.id === 'fileSel' && state){
    const f = state.files.find(x=> x.file === ev.target.value);
    renderFileMeta(f);
    renderChartsForFile(f);
  }
});

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""

@app.route("/") ## Menampilkan tampilan dashboard interaktif
def index():
    return render_template_string(PAGE)

@app.route("/status") ## endpoint cuma buat cek apakah server flasknya berjalan
def status():
    return "OK. Buka / (halaman interaktif) atau /api/summary"

@app.route("/api/ransom_status") ##API menyediakan data status simulasi ransomware untuk ditampilkan di dashboard
def api_ransom_status():
    total_files = 0
    encrypted = 0
    decrypted = 0
    running = False
    last_event = None
    
    for ev in _iter_events() or []:
        typ = ev.get("event") or ev.get("type")
        data = ev.get("data") or {}

    # --- RESET jika ada event ransom_reset ---
        if typ in ("ransom_reset", "system_start", "start_normal_mode"):
            total_files = 0
            encrypted = 0
            decrypted = 0
            running = False
            continue

        # --- existing (boleh biarkan)
        if typ == "ransom_scan_start":
            total_files = int(data.get("total", 0))
            running = True
        elif typ == "ransom_encrypt_start":
            running = True
        elif typ == "ransom_encrypt_done":
            encrypted += 1
            total_files = max(total_files, encrypted)
        elif typ == "ransom_decrypt_done":
            decrypted += 1
        elif typ == "ransom_simulation_end":
            running = False

        # --- tambahan agar WannaCry simulasi terbaca ---
        elif typ == "encrypt_start":
            running = True
        elif typ == "encrypt_end":
            encrypted += 1
            total_files = max(total_files, encrypted)
        elif typ == "decrypt_end":
            decrypted += 1
        elif typ in ("encryption_complete", "simulation_end"):
            running = False

        last_event = typ

    return jsonify({
        "total": total_files,
        "encrypted": encrypted,
        "decrypted": decrypted,
        "running": running,
        "last_event": last_event
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100)
