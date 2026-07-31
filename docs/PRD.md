# PRD — Auto YouTube Shorts Rangkuman Manhwa

**Nama:** ManhwaShorts Studio
**Versi dokumen:** 1.0
**Status:** Diimplementasikan (lihat bagian 18 untuk status aktual)
**Format keluaran:** Video vertikal 9:16 untuk YouTube Shorts

## 1. Ringkasan produk

ManhwaShorts Studio mengubah materi manhwa yang legal atau berlisensi menjadi
video rangkuman pendek. Sistem menghasilkan naskah, voice-over, visual, subtitle,
metadata, dan video final, lalu mengunggahnya ke YouTube.

```
Materi berlisensi → Analisis cerita → Naskah → Voice-over
→ Komposisi visual → Subtitle → Preview/review → Render
→ Upload YouTube → Analitik
```

Otomatisasi memakai pola **human-in-the-loop**: video tidak dipublikasikan
sebelum disetujui pengguna. Ini mengurangi risiko halusinasi, kesalahan
pengucapan, spoiler, dan pelanggaran hak cipta.

## 2. Latar belakang

Pembuatan Shorts rangkuman manhwa secara manual butuh banyak pekerjaan: membaca
dan merangkum, memilih bagian menarik, menulis hook, merekam voice-over, menyusun
panel, membuat subtitle, menambah musik, membuat metadata, mengunggah, memantau
performa.

Produk ini menargetkan pemendekan proses dari beberapa jam menjadi 10–20 menit
waktu aktif pengguna per video.

## 3. Tujuan

### Tujuan utama

1. Draft video Shorts dalam waktu kurang dari 10 menit.
2. Rangkuman tetap sesuai materi sumber.
3. Video siap ditinjau dan dipublikasikan.
4. Produksi konten konsisten dan terjadwal.
5. Performa terukur untuk memperbaiki hook, durasi, dan gaya narasi.

### Non-goals MVP

- Mengambil panel dari situs manhwa tanpa izin.
- Menghapus watermark atau proteksi hak cipta.
- Mengunggah video tanpa persetujuan pengguna.
- Video panjang untuk rangkuman satu seri penuh.
- Mengelola banyak channel dalam satu akun.
- Menjamin penggunaan materi memenuhi doktrin fair use/fair dealing.
- Terjemahan penuh yang bisa menggantikan karya asli.

## 4. Target pengguna

**Persona utama: kreator manhwa.** Satu channel YouTube, 1–3 Shorts per hari,
punya materi sendiri/berlisensi/berizin, perlu proses cepat dan konsisten, tanpa
kemampuan editing video tingkat lanjut.

**Persona sekunder: editor/channel manager.** Mengelola kalender konten,
menyunting naskah dan hasil render, perlu template visual konsisten, memantau
status produksi dan analitik.

## 5. Prinsip produk

1. **Akurat** — setiap klaim dapat dilacak ke materi sumber.
2. **Aman** — materi sumber dan izin penggunaan tercatat.
3. **Dapat diedit** — pengguna bisa mengubah setiap hasil AI.
4. **Tidak langsung terbit** — publikasi memerlukan approval.
5. **Reusable** — voice, subtitle, dan template dapat dipakai ulang.
6. **Observable** — setiap kegagalan pipeline punya log dan opsi retry.

## 6. User journey

1. Pengguna membuat proyek.
2. Mengisi judul, chapter, bahasa, tipe spoiler.
3. Mengunggah materi: ringkasan/catatan, naskah sumber, gambar legal, bukti hak.
4. Sistem mengekstrak karakter, peristiwa, urutan cerita, sumber fakta.
5. Sistem membuat beberapa opsi hook.
6. Sistem menghasilkan naskah.
7. Pengguna memeriksa dan menyunting naskah.
8. Sistem menghasilkan voice-over.
9. Sistem menyusun visual, subtitle, musik, efek.
10. Pengguna melihat preview.
11. Pengguna memperbaiki timeline bila perlu.
12. Sistem merender video final.
13. Pengguna menyetujui metadata dan jadwal.
14. Sistem mengunggah via YouTube API.
15. Sistem mengambil analitik dan menampilkannya.

## 7. Kebutuhan fungsional

### FR-01 — Manajemen proyek

Buat, gandakan, arsipkan, hapus proyek. Judul manhwa dan nomor chapter. Format:
ringkasan chapter, profil karakter, fakta menarik, teori, cliffhanger. Bahasa,
target durasi (default 60s), tingkat spoiler (minimal/sedang/penuh), penanda seri.

**Acceptance:** status `Draft`/`Generating`/`Review`/`Rendering`/`Ready`/
`Scheduled`/`Published`/`Failed`; draft tersimpan otomatis; proyek dapat
diduplikasi beserta template.

### FR-02 — Input materi sumber

Terima teks langsung, TXT/MD/DOCX/PDF, JPG/PNG/WebP, metadata chapter manual,
catatan pelafalan nama.

Setiap aset punya: nama sumber, pemilik hak, jenis lisensi/izin, URL referensi
opsional, tanggal izin, batas penggunaan, status verifikasi.

**Acceptance:** format tak didukung ditolak; aset tanpa deklarasi hak tidak bisa
masuk tahap publikasi; relasi bagian naskah ke sumber faktanya tersimpan;
pengguna dapat menghapus seluruh materi sumber.

### FR-03 — Analisis cerita

Ekstrak tokoh dan alias, peran, lokasi, urutan kejadian, konflik utama, twist dan
cliffhanger, kata yang butuh panduan pelafalan, informasi belum pasti atau
bertentangan.

**Acceptance:** hasil dapat diedit; fakta penting punya referensi ke materi
sumber; informasi yang tidak ditemukan tidak dibuat seolah fakta; klaim dengan
keyakinan rendah ditandai.

### FR-04 — Generator naskah

Struktur default: Hook (0–3s), Setup (3–12s), Konflik (12–40s), Twist/cliffhanger
(40–55s), CTA (55–60s).

Konfigurasi: gaya narasi (dramatis/santai/misterius/cepat/informatif), sudut
pandang, target jumlah kata, tingkat spoiler, CTA, kata dilarang, penyebutan judul
dan chapter, jumlah alternatif hook.

**Acceptance:** minimal tiga opsi hook; naskah tidak menambah fakta di luar
sumber; estimasi durasi tampil sebelum voice-over; pengguna dapat mengedit,
regenerate per bagian, dan mengunci bagian; bagian terkunci tidak berubah saat
bagian lain dihasilkan ulang; ada pemeriksaan pengulangan, sensitivitas, dan
kesesuaian sumber.

### FR-05 — Voice-over

Pilih voice; atur kecepatan, pitch, energi; tambah jeda; atur pelafalan nama;
regenerate satu kalimat; unggah voice-over sendiri.

**Acceptance:** audio dapat dipreview per segmen; timeline diperbarui saat durasi
audio berubah; versi audio sebelumnya tersimpan; voice cloning hanya dengan bukti
persetujuan pemilik suara; penggunaan suara sintetis diungkapkan bila diwajibkan.

### FR-06 — Komposisi visual

Timeline dari voice-over dengan panel yang diunggah, crop otomatis 9:16, pan/zoom/
blur/transisi, highlight, overlay teks, intro/outro opsional, musik dan SFX
berlisensi.

Pengguna dapat mengganti gambar per scene, mengubah urutan, mengatur focal point,
mengubah durasi, menonaktifkan efek, memakai template.

**Acceptance:** sistem tidak mengambil gambar dari internet secara otomatis;
gambar tidak terdistorsi; wajah/fokus utama tidak tertutup subtitle secara
default; timeline tetap sinkron setelah perubahan durasi audio; sumber dan lisensi
musik tercatat.

### FR-07 — Subtitle

Sinkronisasi berbasis kata/frasa, highlight kata aktif, preset gaya, safe area,
koreksi manual, ekspor SRT.

**Acceptance:** teks dan timing dapat diedit; terbaca di layar ponsel; maksimal
baris dan karakter per baris dapat dikonfigurasi; peringatan bila keluar safe
area; subtitle tertanam pada video final dan tersedia sebagai file terpisah.

### FR-08 — Preview dan quality check

Periksa rasio 9:16, resolusi minimal, audio clipping, musik terlalu keras, scene
kosong, aset tanpa izin, subtitle keluar safe area, durasi melebihi batas, klaim
tanpa sumber, kemungkinan konten sensitif, duplikasi naskah.

**Acceptance:** error wajib diselesaikan sebelum publikasi; warning dapat dilewati
dengan alasan tercatat; approval eksplisit diperlukan; preview cukup dekat dengan
render final.

### FR-09 — Rendering

MP4, codec kompatibel YouTube, default 1080×1920, frame rate dikonfigurasi,
thumbnail opsional, SRT dan naskah final.

**Acceptance:** render dapat diulang setelah gagal; progress terlihat; kegagalan
menampilkan pesan yang dapat ditindaklanjuti; output punya checksum dan versi;
file final tidak dipublikasikan sebelum lulus quality check.

### FR-10 — Integrasi YouTube

OAuth channel; privasi private/unlisted/public; judul, deskripsi, hashtag,
playlist, jadwal; unggah sebagai private untuk pemeriksaan akhir; status
pemrosesan YouTube. Metadata otomatis tapi dapat diedit.

**Acceptance:** token OAuth terenkripsi; scope minimum; kegagalan upload dapat
di-retry tanpa render ulang; publikasi public memerlukan konfirmasi eksplisit;
quota limit ditangani dengan waktu retry; koneksi dapat dicabut.

### FR-11 — Kalender konten

Tampilan daftar dan kalender, penjadwalan, hindari bentrok, tandai chapter yang
sudah dibahas, buat seri konten. MVP: satu channel dan satu zona waktu.

### FR-12 — Analitik

Views, likes, komentar, average view duration, average percentage viewed,
retention, subscribers gained, performa per hook/durasi/template/tipe konten.

**Acceptance:** data mencantumkan waktu sinkronisasi terakhir; sistem tidak
mengarang metrik yang tidak tersedia dari API; performa video dapat dibandingkan;
data dapat diekspor CSV.

## 8. Hak cipta dan keamanan konten

### Kebijakan wajib

1. Hanya materi yang dimiliki, dilisensikan, atau diizinkan.
2. Tidak ada fitur scraping situs manhwa.
3. Tidak menghapus watermark.
4. Provenance setiap aset tersimpan.
5. "Fair use" bukan izin otomatis.
6. Pengguna bertanggung jawab atas validitas hak penggunaan.
7. Video harus transformatif, bukan pengganti karya asli.
8. Materi sumber tidak dipakai melatih model tanpa persetujuan eksplisit.

### Mekanisme mitigasi

Checkbox deklarasi hak, daftar aset belum terverifikasi, content similarity
warning, audit log approval, takedown workflow, tombol hapus proyek dan aset, opsi
kredit/atribusi, pembatasan panel berurutan, peringatan bila narasi hanya
membacakan teks sumber.

> Penilaian fair use/fair dealing bergantung yurisdiksi dan konteks. Produk
> membantu mencatat izin dan provenance, bukan memberi nasihat hukum.

## 9. Persyaratan nonfungsional

**Performa:** draft naskah <60s; preview <5 menit untuk video 60s; render final
<10 menit; respons dashboard non-render p95 <2s.

**Reliabilitas:** render dan upload idempotent; job dapat dilanjutkan setelah
worker restart; autosave editor maksimal 10s; availability MVP 99,5%.

**Keamanan:** OAuth token dan API key terenkripsi; aset di private storage; signed
URL masa berlaku pendek; role Owner dan Editor; audit log upload/approval/render/
publish; rate limiting autentikasi dan generation; validasi MIME dan malware scan;
secret tidak masuk log.

**Privasi:** ekspor dan hapus data; retensi aset terhapus maksimal 30 hari;
materi tidak dipakai training secara default; telemetri tidak menyimpan isi naskah
atau gambar tanpa consent.

**Observability:** log terstruktur; job ID dan project ID pada setiap pipeline;
metrik durasi dan kegagalan tiap tahap; alert lonjakan render/upload failure.

## 10. Arsitektur

```
Web Dashboard
     │
API / Backend
     ├── PostgreSQL
     ├── Object Storage
     ├── AI Orchestrator (analysis, script, safety, metadata)
     ├── TTS Provider
     ├── Job Queue → Render Workers (FFmpeg)
     ├── YouTube API
     └── Analytics Sync Worker
```

Provider LLM dan TTS memakai adapter agar mudah diganti.

## 11. Entitas data utama

`User`, `Workspace`, `Project`, `SourceAsset`, `ScriptVersion`, `AudioSegment`,
`TimelineScene`, `RenderJob`, `Publication`. Detail kolom ada di
[ARCHITECTURE.md](ARCHITECTURE.md#data-model).

## 12. Prioritas

**P0 (MVP):** login + satu workspace, pembuatan proyek, upload teks dan gambar,
deklarasi hak, analisis sumber, generator hook dan naskah, editor naskah, TTS,
timeline otomatis, subtitle otomatis, template 9:16, preview dan quality check,
render MP4, download, upload YouTube private/unlisted, approval manual, status job
dan retry.

**P1:** kalender dan scheduling, analitik video, multiple template, musik dan SFX
library, pronunciation dictionary, regenerate per scene, content series, ekspor
SRT, metadata generator, notifikasi render selesai.

**P2:** multi-channel, kolaborasi dan komentar, A/B testing hook, rekomendasi
berbasis retention, mobile companion, brand kit, approval bertingkat, multi-language
dubbing, batch production.

## 13. KPI

**Produk:** median proyek→draft preview <10 menit; render berhasil tanpa retry
>95%; upload berhasil >98%; naskah disetujui dengan maksimal dua revisi >70%;
minimal 50% pengguna aktif membuat video kedua dalam tujuh hari.

**Konten (bahan evaluasi, bukan jaminan):** retention 3 detik pertama, average
percentage viewed, completion rate, rewatch rate, engagement per 1.000 views,
subscriber conversion, performa tiap gaya hook dan durasi.

**Guardrail:** video terbit tanpa approval = 0; aset tanpa deklarasi hak
dipublikasikan = 0; insiden token/aset bocor = 0; klaim tanpa referensi sumber
lolos quality check <1%.

## 14. Risiko dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Pelanggaran hak cipta | Takedown / strike | Materi berlisensi, provenance, deklarasi hak, approval |
| Naskah berhalusinasi | Informasi salah | Source-grounded generation, citation, confidence warning |
| Konten terlalu mirip sumber | Dianggap tidak transformatif | Narasi asli, similarity warning, batas panel |
| Voice salah melafalkan nama | Kualitas turun | Pronunciation dictionary, preview per segmen |
| Crop panel buruk | Visual tidak jelas | Focal point manual, safe-area preview |
| Render lambat | Produksi tertunda | Queue, worker, proxy preview |
| YouTube quota limit | Upload gagal | Quota monitoring, retry, scheduling |
| Konten repetitif | Retention turun | Variasi hook, deteksi duplikasi, template berbeda |
| Biaya AI/TTS tinggi | Margin rendah | Cache, regeneration per segmen, batas penggunaan |
| Akun YouTube terganggu | Dampak operasional | Default private, least-privilege OAuth |

## 15. Acceptance criteria MVP end-to-end

Pengguna dapat: login dan membuat proyek; mengunggah teks + minimal lima gambar
berdeklarasi hak; menghasilkan tiga hook dan naskah ~60s; menyunting dan
menyetujui naskah; menghasilkan voice-over; menghasilkan scene dan subtitle
otomatis; mengubah gambar, crop, teks, timing; melihat preview; menjalankan
pemeriksaan kualitas dan hak; merender 1080×1920; mengunduh video dan subtitle;
mengunggah private/unlisted ke satu channel; melihat status upload dan retry;
melacak sumber aset, versi naskah, dan pemberi approval.

## 16. Tahapan implementasi

1. **Fondasi** — auth, project management, asset upload, rights metadata, job
   queue, database, audit log.
2. **AI pipeline** — ekstraksi cerita, source-grounded script, hook generator,
   editor, quality checks.
3. **Media pipeline** — TTS, timeline, subtitle, FFmpeg renderer, preview, retry.
4. **Distribusi** — OAuth YouTube, upload private/unlisted, metadata, scheduling,
   status sync.
5. **Optimasi** — analitik, perbandingan hook, template tambahan, rekomendasi.

## 17. Contoh output naskah

```text
[HOOK]     Semua orang mengira pemburu peringkat terlemah ini akan mati lebih dulu.
[SETUP]    Setelah masuk ke dungeon misterius, Jin terpisah dari kelompoknya dan
           menemukan sistem yang hanya bisa dilihat olehnya.
[KONFLIK]  Sistem itu memaksanya menyelesaikan latihan brutal setiap hari. Jika
           gagal, dia dipindahkan ke zona hukuman yang dipenuhi monster.
[TWIST]    Namun setiap latihan justru membuat kekuatannya meningkat tanpa batas,
           sesuatu yang mustahil bagi pemburu lain.
[CTA]      Menurutmu, kemampuan ini hadiah atau awal dari bencana yang lebih besar?
```

Contoh ini hanya menunjukkan struktur. Naskah produksi dibuat dari materi yang
pengguna miliki dan dilengkapi referensi sumber internal.

## 18. Status implementasi v1.0

Ditambahkan setelah implementasi, supaya dokumen ini jujur soal apa yang benar-benar
ada.

### Terimplementasi dan teruji

FR-01 sampai FR-10 berjalan penuh, diverifikasi lewat 94 test otomatis termasuk
render FFmpeg nyata. Seluruh guardrail bagian 8 aktif dan benar-benar memblokir:
gate hak cipta, gate transformative (≥50% verbatim ditolak), double-gate publikasi
public, verifikasi checksum sebelum upload, audit log.

Referensi acceptance criteria bagian 15: semuanya lolos, kecuali voice-over unggahan
sendiri (kolom `user_uploaded` ada, endpoint belum).

### Menyimpang dari PRD

- **SQLite, bukan PostgreSQL.** Cukup untuk satu pengguna lokal; `MS_DATABASE_URL`
  menerima Postgres tanpa perubahan kode.
- **Storage filesystem, bukan S3.** Interface `services/storage.py` sudah meniru
  object storage (`storage_key`, `put_bytes`, `path_for`), tinggal ganti backend.
- **Tanpa Redis/BullMQ.** Render jalan di background task FastAPI; ada worker
  standalone (`scripts/worker.py`) tapi tanpa queue eksternal, jadi konkurensi
  terbatas satu proses.
- **Server-rendered UI, bukan Next.js.** Jinja2 + vanilla JS, tanpa build step.

### Belum ada

- **FR-11 kalender** — API menerima `scheduled_at`, tampilan kalender belum ada.
- **FR-12 analitik** — kode Data/Analytics API ada, tapi butuh upload live untuk
  diverifikasi; ekspor CSV belum ada.
- Musik dan SFX library, intro/outro, multiple template, regenerate per scene,
  role Editor (baru Owner), rate limiting dan CSRF (mitigasi: loopback + reverse
  proxy), malware scan (baru validasi MIME + content sniffing).

### Catatan kualitas

Generator naskah rules-based bersifat kompresif, bukan menulis ulang, jadi
similarity biasanya 25–35% — lolos gate tapi memicu warning. Provider LLM
memperbaikinya. Voice espeak-ng jelas tapi robotik; layak untuk review, belum
layak untuk channel produksi.
