# TODO Katalog Agen

Urutan prioritas (disepakati 13 Nov 2026):

1. [x] Ganti token GitHub dengan token tanpa scope (selesai 13 Nov 2026, scope kosong terverifikasi). Sisa: user menghapus token lama di github.com/settings/tokens.
2. [x] Commit pertama ke Git + cadangan database. Kode di github.com/BagusFary/agentloadout (privat, deploy key). Cadangan: WAL + transaksi, `scripts/backup.py` (7 harian + 4 mingguan, sebelum tiap fetch), `scripts/restore.py`, uji pemulihan lulus (13 Nov). Opsional nanti: salinan ke luar laptop (PC via Tailscale / repo privat).
9. [ ] Rapikan untuk rilis publik: pindahkan path/ID agen/alamat ke config (.env), README bahasa Inggris, LICENSE, fitur OpenBot opsional.
3. [x] Sinkronisasi status "Terpasang" dengan MCP/skill yang benar-benar terpasang di OpenBot.
4. [x] Tombol Update dan Nyala/Mati (contoh: MCP `rea`, 255 tool).
5. [x] Uji alur yang belum dicoba: pasang skill, pasang aplikasi, tombol Lepas.
6. [x] Login lebih kuat: verifikasi dua langkah, atau Pasang/Lepas hanya lewat Tailscale.
7. [x] Kualitas data: label risiko & kategori, terjemahan deskripsi ke Indonesia, pecah repo multi-skill per skill, repo tanpa topik GitHub.
8. [x] Bagian "Baru minggu ini" (setelah 7 hari data bintang terkumpul).

Catatan: web hanya hidup selama laptop HomeLab Soulmate menyala.

## Catatan 13 Nov 2026 (malam)
- 3: `scripts/sync_state.py` + routine harian membaca daftar MCP/skill OpenBot.
- 4: `scripts/check_updates.py` (npm/PyPI/commit git); tombol Update, Nyala/Mati (MCP & skill), Lepas.
- 5: skill frontend-design dipasang ke 6 agen lalu dilepas; jalur aplikasi npm butuh `--prefix ~/.local`.
- 6: aksi hanya dari tailnet (header identitas Tailscale; Funnel ditolak 403). Publik hanya melihat.
- 7: deskripsi diterjemahkan (ekspor/impor oleh Noah), daftar skill per folder + pasang per skill,
  kategori berbasis skor, pencarian repo tanpa topik dengan filter relevansi.
- 8: "Baru minggu ini" (repo ≤ 21 hari); "Naik minggu ini" muncul otomatis setelah ada data 7 hari.
- Deskripsi per skill (≈3.700) belum diterjemahkan; hanya deskripsi repo.

## Agent Loadout sebagai MCP + CLI (14 Nov 2026)
Selesai: paket Python `agentloadout` (uvx), adapter Claude Code/Codex/Cursor/Gemini/OpenCode/OpenBot,
MCP server 10 tool, rencana + persetujuan manusia (web/terminal), inspect_repo, setup/uninstall sekali
perintah, web on-demand, skill panduan, README Inggris, LICENSE MIT. Uji: e2e MCP + instalasi bersih lolos.

Sisa:
- [ ] Rilis ke PyPI supaya `uvx agentloadout setup` jalan untuk umum (butuh akun PyPI + token, langkah user).
- [ ] Uji di macOS dan Windows (lokasi config & `which` di Windows).
- [ ] Tampilan web berbahasa Inggris / dua bahasa untuk publik.
- [ ] Bersihkan path pribadi di docs/PLAYBOOK.md dan docs/PERSONAL.md sebelum repo dibuat publik.
- [ ] Pasang aplikasi (kind=app) otomatis — sekarang hanya mode salin.
