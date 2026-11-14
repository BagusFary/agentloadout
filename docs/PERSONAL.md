# Agent Loadout — instance pribadi (HomeLab Soulmate)

Katalog pribadi berisi server MCP, skill agen, dan aplikasi agen yang sedang naik daun di GitHub, lengkap dengan label risiko, contoh config, dan tombol **Pasang ke OpenBot**.

- **Alamat publik (perlu password):** https://9router.tail0b3182.ts.net:10000/  (Tailscale Funnel)
- **Alamat tailnet (perlu password):** https://9router.tail0b3182.ts.net:10443/
- **Lokal:** http://127.0.0.1:8787/

## Login
- Semua halaman dan API wajib login dengan password yang disimpan lewat formulir aman OpenBot (`KATALOG_WEB_PASSWORD`). Sesi berlaku 30 hari; tombol **Keluar** di pojok kanan atas.
- 5 kali salah dari satu IP mengunci IP itu 15 menit; 30 kali salah total mengunci semua login 15 menit.
- Ganti password: minta Noah membuka formulir `KATALOG_WEB_PASSWORD` lagi, lalu `systemctl --user restart katalog-agen`. Semua sesi lama otomatis keluar.

## Cara pakai
1. Pilih tab **MCP / Skill / Aplikasi**, cari, filter kategori, atau urutkan (Paling viral, Bintang, Baru diperbarui, Repo terbaru).
2. Klik kartu untuk detail: risiko, variabel yang dibutuhkan, dan config siap salin (Claude Code, Codex, JSON).
3. **Pasang ke OpenBot:** tombol mengirim permintaan bertanda tangan ke Noah. Noah memeriksa repo, mengirim ringkasan risiko ke chat, dan baru memasang setelah kamu membalas `setuju pasang <owner/repo>`.
   - MCP dipasang ke server OpenBot, sehingga semua agen mendapat tool-nya.
   - Skill dijadikan skill lokal lalu dipasang ke semua agen.
4. Alternatif tanpa webhook: **Salin perintah untuk Noah**, lalu tempel ke chat.
5. Item yang terpasang bisa dilepas dengan tombol **Lepas dari OpenBot**.

"Paling viral" memakai kenaikan bintang 7 hari. Selama riwayat belum 7 hari, dipakai rata-rata bintang per hari sejak repo dibuat (ditandai "rata-rata sejak dibuat").

Label risiko dibuat otomatis dari deskripsi, topik, dan README. Ini hanya penanda awal, bukan audit keamanan.

## Komponen
| Bagian | Lokasi |
|---|---|
| Kolektor data GitHub + registry MCP | `collector/collect.py` |
| Database | `data/katalog.db` (SQLite) |
| Backend web + API | `server/app.py` (127.0.0.1:8787) |
| Pengirim webhook | `server/installer.py`, URL di `data/webhook.json` |
| Tampilan | `web/` |
| Catat status pasang | `scripts/set_status.py <owner/repo> <status> "<detail>"` |
| Pembaruan data | `scripts/update.sh` (routine OpenBot harian 06:00 WIB) |

Token GitHub dan kunci webhook disimpan oleh OpenBot di `~/.config/OpenBot/agent-api-keys/…`, tidak di proyek ini.

## Perintah berguna
```bash
systemctl --user status katalog-agen      # status web
systemctl --user restart katalog-agen     # restart web
journalctl --user -u katalog-agen -n 50   # log web
~/Projects/katalog-agen/scripts/update.sh # perbarui data sekarang (±5 menit)
tailscale serve status                    # cek publikasi (10443 = tailnet only)
```

## Cadangan & pemulihan
- Setiap pembaruan harian (`scripts/update.sh`) **mencadangkan database dulu** sebelum fetch.
  Kalau cadangan gagal, fetch dibatalkan.
- Lokasi: `~/Backups/agentloadout/daily` (7 terakhir) dan `weekly` (4 terakhir), izin hanya pemilik.
  Tiap cadangan = salinan SQLite yang sudah dicek `integrity_check` + `*.state.json.gz`
  (riwayat bintang, status terpasang, terjemahan: data yang tidak bisa di-fetch ulang).
- Database memakai WAL mode; kolektor menyimpan dalam satu transaksi dan membatalkan bila gagal.

```bash
python3 scripts/backup.py                       # cadangan manual sekarang
python3 scripts/restore.py --list               # lihat cadangan
python3 scripts/restore.py ~/Backups/agentloadout/daily/katalog-YYYY-MM-DD.db   # pulihkan
```
`restore.py` menghentikan web, menyimpan database lama sebagai `katalog.db.before-restore-*`, memulihkan, lalu menyalakan web lagi.

## Mematikan
```bash
tailscale funnel --https=10000 off   # tutup akses publik
tailscale serve --https=10443 off
systemctl --user disable --now katalog-agen
```
Lalu nonaktifkan routine "Perbarui Katalog Agen harian" dan webhook "Permintaan pasang dari Katalog Agen" di OpenBot.
