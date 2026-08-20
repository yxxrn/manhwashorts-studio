# CURRENT ORACLE PHASE 1 CHECKPOINT - 2026-08-20

- Authority: /home/ubuntu/manhwashorts, branch main, published base
  27f0d95fd894aba8c6ee8fe34add32ef5f6ec7b9. The Oracle tracking ref is
  stale at b6f72cd; publish through the Windows exact-object transport.
- Protected untracked paths: data symlink, ms_env.sh, DB/WAL, provider
  state, caches, logs, and media. Never print or commit ms_env.sh.
- Phase 0 durable visual proof remains: the 701-panel cache/checkpoint copy
  under /data/data/p0-aws-acceptance/cloud-stage-cache is byte-identical,
  9 files, 7,855,981 bytes. No visual provider rerun is needed for the next
  stage unless cache identity invalidates.
- Phase 1 implementation is uncommitted only in
  app/services/cloud_multimodal.py and
  tests/test_cloud_multimodal_mass_production.py.
- RED was collection-clean with three intended failures. GREEN is 38 cloud
  regression tests plus the expanded matrix at 239 passed and 1 existing
  fixture skip. The full non-slow run is 1068 collected, 1062 passed, 4
  skipped, 2 environment-invalid Linux failures in the Windows cmd.exe
  launcher tests.
- Implementation contract: story-map and narration use four workers and
  180-panel chunks, deterministic ordered merge, per-chunk cache keys,
  bounded retry, and resume without repeat provider calls. No provider raw
  payload, hash, credential, DB, or media is persisted by this slice.
- Next command after publication is the normal story-map/narration service
  run against the durable visual cache. Then attempt the regular silent MP4
  path and inspect FFprobe/blackdetect/contact-sheet/QC. Voice/TTS remains
  deferred until the silent preview is proven.

# P0 Manhwa Shorts — Chapter 129–133 (703 panel)

Handoff lengkap untuk agent berikutnya. Status = **VISUAL STAGE SELESAI**, menunggu lanjut story map → narasi → render + voice.

---

## 1. Infrastruktur & Lokasi (HOST UTAMA = Oracle)

| Item | Nilai |
|---|---|
| Host | Oracle Cloud (`instance-20260816-2016`) |
| SSH alias | `oracle` (`~/.ssh/config` di orkestrator) |
| Repo | `/home/ubuntu/manhwashorts` (worktree modified, **BELUM commit**) |
| venv | `/home/ubuntu/manhwashorts/.venv` (Python 3.12) |
| FFmpeg | `/usr/local/bin/ffmpeg` (7.0.2 static) |
| Data besar | `/data/data` (disk 100GB) — `~/manhwashorts/data` → **symlink** ke `/data/data` |

**AWS tidak dipakai lagi** untuk proyek ini (sudah dipindah penuh ke Oracle).

### Env & kredensial
- File env utama: `/tmp/ms_env.sh` (juga disalin di root repo => `ms_env.sh`)
- Baca sebelum run: `source /tmp/ms_env.sh`
- Masuk **DB credential** (bukan env) untuk model vision: `grok-4.3` (id `bbc025efbef24e60aad3f6387f78d547`). Env `MS_LLM_MODEL` boleh beda; resolver ambil dari DB.

### Path penting (semua di `/data/data/p0-aws-acceptance/` via symlink)
- DB: `sample.db` (+ WAL 422MB — jangan commit, besar)
- Stage cache: `/tmp/ms-stage-cache/` (7 file; visual cache 703 dicek di sana)
- Cloud jobs: `cloud-jobs/22876a6014a842f48bfca58c10a592b5.json` (state `NEEDS_REVIEW` dari run visual terakhir yang crash — akan overwrite pada run sukses)
- Output: `output/22876a6014a842f48bfca58c10a592b5/`
- Segmentation review: `segmentation-review/`
- Reference panels (crop panel): `tmp/22876a6014a842f48bfca58c10a592b5/reference-review-panels/scene-*.png`

---

## 2. Status Pipeline (Saat Ini)

- Project: `22876a6014a842f48bfca58c10a592b5`, **703 panel** (113 halaman long-strip `900×16000`, 5 chapter 129-133, 646 asset)
- **Segmentation: ✅** (cache `segmentation` di stage_results)
- **Visual evidence: ✅ 701/703** (2 panel di-skip — lihat §5)
  - cache visual barusan ditulis ke `/tmp/ms-stage-cache/` (identity grok `aa2fc9cd...`)
- **Story map: ⏳ belum** (perlu dijalankan ulang — lihat §7)
- **Narasi: ⏳ belum**
- **Render + voice: ⏳ belum**

Dua panel skip (grok tolak konsisten, bukan 429):
- `region-5e2b11044fc68097804c`
- `region-ec97e3dd8c5b8941b68c`
→ logis: 701 panel yang dipakai, 2 hilang (±0.3%).

---

## 3. Porter / Model / Voice

- **Vision/LLM**: `grok-4.3` @ `http://43.156.164.238:8000/v1` (key di env/DB)
  - Context ±950K-1M token (terverifikasi 950K = 200 OK)
  - **Compute concurrency cap ~4-5 request besar bersamaan** (bukan rate-limit HTTP; semua 200, cuma antri lama)
- **TTS / Voice**: `grok-voice-latest` @ `.../v1/tts`, protocol `grok`, language `en`, **voice `the-explainer-american`**, resp `mp3`
  - Test berhasil (±1.97s audio)

---

## 4. Konfigurasi Terakhir (di `app/services/cloud_multimodal.py`)

Preview-only relaxations (DISETUJUI user utk batch 703, **prod tetap ketat**):

| Parameter | Nilai | Alasan |
|---|---|---|
| `VISUAL_REQUEST_MAX_PANELS` | **8** | sweet spot; 4=0.54s/panel; 16+ output > max_tokens => invalid |
| `VISUAL_PARALLEL_WORKERS` | **8** | ~4-5 efektif (compute cap); 16 memicu antrian |
| `VISION_REQUEST_TIMEOUT` | **600.0** | krusial! 30s = timeout palsu pada request 22-70s |
| thumbnail `_visual_provider_payload` | **384×576** | +30% throughput vs 512×768; kualitas OCR tetap ok |
| `max_tokens` di 3 request body | **65536** | tanpa ini response besar ke-truncate => invalid |
| `MAX_ESTIMATED_BYTES` | 3_500_000 | utk chunk besar (perp dimuat dari local cache) |
| Binary reduction | aktif | chunk gagal => dipecah sampai 1 panel; hanya panel beracun yang skip |
| Checkpoint per-chunk | aktif | `/tmp/visual_checkpoints.jsonl` — resume instan kalau run di-kill |
| Subset build | aktif | panel skip tidak bikin KeyError; hasil = subset chrome order |

Durasi narasi preview: **40-180s** (`chunk_step=600`, `allow_dialogue_copy=True`, `cadence_adapted=True`).

RENCANA story/narasi (belum implement): paralel **4 worker**, `chunk_step=180` (≈1 bab/chunk, kualitas aman).

---

## 5. Temuan Lengkap (dari awal → sekarang) — PENYEBAB & FIX

### A. Batching & format provider (narasi/story)
1. **Cache model mismatch**: semua cache lama identity `1da7354e` (gemini-3.7), runner grok identity `aa2fc9cd` → cache selalu miss → re-run penuh. Fix: buat cache ulang dengan runner grok.
2. **Format key berubah-ubah tiap run** (LLM non-deterministik): `inference_text`, `type`/`statement`, `factual`→`fact`, `entities` key baru → kontrak parse diperluas + **fallback generik** (unknown key → scan string value langsung di `Mapping`).
3. **`expected_panel_ids` ≠ visual.panel_ids** (karena 3 balloon-unknown dulu di-skip) → validate pakai `expected_panel_ids` dari observations yang benar diproses.
4. **`source_index` tidak deterministik** → reindex kontigu per-chunk.
5. **Coverage manifest** harus ikut jumlah obs yang diproses (tidak harus full 703 awal).
6. **balloon-unknown jangan di-skip di `_narration_observations`** — obs-nya lengkap; skip bikin count mismatch (277≠280 → kontrak "exactly once").

### B. Kontrak gate
7. **`script passage copies source dialogue`** (collision 4-gram dgn dialogue) → relax `allow_dialogue_copy=True` (preview; prod strict).
8. **Durasi narasi** di luar jangkauan (batch 703 wajar panjang) → preview 40-180s (prod 50-60s).

### C. Kecepatan / konfigurasi (AWALNYA 9 JAM → sekarang ±15-25 min visual)
9. **1 panel/request = 703 request** (~jam). Root: `VISUAL_REQUEST_MAX_PANELS=1`.
10. **Unlimited rate limit di dashboard tetap lambat** → ternyata bukan HTTP rate-limit (semua 200, no 429), tapi **compute-concurrency cap** model grok: ~4-5 request besar bersamaan, sisanya antri (TTFB panjang).
11. **`VISION_REQUEST_TIMEOUT=30` → 600**: penyebab "error invalid" beruntun — request 22-70s kena timeout palsu di concurren tinggi.
12. **Thumbnail 512×768 → 384×576**: +30%.
13. **Worker 8 vs 16**: 16 → antrian lama; 8 efektif.

### D. Resume / anti-gagal
14. **Checkpoint per-chunk** (`/tmp/visual_checkpoints.jsonl`): tiap chunk sukses langsung ditulis; re-run = seed + hanya sisa. Menyelesaikan masalah "27 menit kerja hilang saat kill".
15. **Subset build pada skip**: sebelumnya `reconciled=[reconciled_by_id[...] for ordered]` → KeyError begitu ada panel skip. Fix: filter `if pid in reconciled_by_id`, pertahankan order.

### E. Infra Oracle
16. Disk baru 100GB di `/dev/sdb` → `/data` (fstab). Data pindah symlink. Boot disk lega.
17. venv rebuild, ffmpeg static install.
18. Env & run scripts disalin dari AWS (md5 identik).

---

## 6. Skrip run/debug di `/tmp` (Oracle)

- `/tmp/run_tb2.py` — runner FULL pipeline (service.run_project), model grok, max_attempts=3
- `/tmp/run_visual_only.py` — jalankan VISUAL saja; pakai `pickle cache` panels (prepare 2-5min → 0s). **checkpoint pulih**.
- `/tmp/run_visual_50.py` — visual subset 50 utk debug
- `/tmp/timing_test.py` — ukur 1 request observe asli (durasi/rows)
- `/tmp/conc_test.py` — uji concurrency (4 vs 5 request 8-panel paralel)
- `/tmp/grok_bench.py` — benchmark batch size / worker (echo payload)
- `/tmp/panels_cache.pkl` (**529MB**) — panels hasil prepare (pickle); prepare jadi instan
- `/tmp/ms_env.sh` — env runtime

Cara run visual-only:
```bash
cd ~/manhwashorts && source /tmp/ms_env.sh
nohup .venv/bin/python -u /tmp/run_visual_only.py > /tmp/run_visual.log 2>&1 &
# pantau:
grep -c VISUAL_CHUNK_OK /tmp/run_visual.log
grep VISUAL_DONE /tmp/run_visual.log
```

---

## 7. NEXT STEPS (urutan yang disarankan)

1. **Story map (703)** — gunakan runner grok; sudah ada cache `story_map`/`narration` lama identity gemini → miss → jangan keliru. Rancangan: **paralel 4 worker, chunk_step=180** (≈1 bab). Est ±10-15 min.
2. **Narasi (703)** — chunk_step sama, tergantung story map. Est ±15-20 min.
3. **Render silent MP4 703 panel** (motion rules sudah ada di repo; referensi v8 08pbie di output lama).
4. **Voice (TTS grok-voice-latest)** — protocol `grok`, voice `the-explainer-american`.
5. **QC + konfirmasi render** sebelum klaim selesai (hard gate; jangan klaim tanpa MP4+QC).

Catatan batas waktu per run dari user: **stop + debug kembali** kalau melewati jatah (~22-30 min utk visual; pipeline penuh ~70 min).

---

## 8. Env (disalin ke repo root)

`ms_env.sh` — lihat file di root repo. JANGAN commit (berisi API key) kecuali repo bersifat private & user minta.
## Oracle interruption-safe checkpoint 2026-08-20

- Authority is /home/ubuntu/manhwashorts on Oracle, branch main.
- The checkpoint commit is 00b82b069a8ac3bf6910c1b2903e0847f66129e1.
  GitHub main is verified at the same SHA through the isolated Windows exact-
  history transport. Oracle's origin/main tracking ref is stale at b6f72cd
  because VPS HTTPS authentication is unavailable. The tracked worktree intentionally remains dirty; no reset, checkout,
  force push, or unrelated cleanup is authorized.
- Phase 0 focused verification is green: 169 passed with 35 existing Pillow
  deprecation warnings. The two checkpoint/cache regressions and the two
  narration/persistence regressions are green (2 passed). No provider call was
  needed for this verification.
- FileStageCache and scoped visual checkpoint persistence now use atomic local
  JSON writes and an instance-scoped checkpoint ledger. Existing runtime cache
  content was copied without deletion from /tmp/ms-stage-cache and
  /tmp/visual_checkpoints.jsonl to
  /data/data/p0-aws-acceptance/cloud-stage-cache. The copy is byte-identical:
  8 JSON stage files plus one checkpoint ledger, 9 files and 7,855,981 bytes.
  The visual cache entry records 701 panels; the source checkpoint ledger has
  736 lines. Runtime files remain outside Git.
- The source/tests/docs diff still includes prior preserved P0 work across the
  cloud, analyzer, visual, segmentation, render, operator-adjacent, and test
  paths. It must be reviewed and staged by allowlist; data, database/WAL,
  media/output, provider state, and ms_env.sh must not be staged.
- Story map, narration, timeline/render, silent MP4, voice/TTS, and final QC
  are not complete. The next bounded implementation is four-worker,
  approximately 180-panel story-map/narration chunking with deterministic
  ordered merge, durable per-chunk cache/checkpoint, bounded retries, and
  resume proof. Voice remains after a verified silent preview.
- Resume from a fresh shell with:
  cd /home/ubuntu/manhwashorts
  source /tmp/ms_env.sh
  .venv/bin/python -m pytest tests/test_cloud_multimodal_mass_production.py
  tests/test_analyzer_contract.py tests/test_vision_adapter.py
  tests/test_vision_pipeline.py tests/test_story_evidence.py
  tests/test_strip_segmentation.py tests/test_strips.py -ra
  Do not print the environment file or its values.
