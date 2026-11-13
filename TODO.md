# TODO Katalog Agen

Urutan prioritas (disepakati 13 Nov 2026):

1. [x] Ganti token GitHub dengan token tanpa scope (selesai 13 Nov 2026, scope kosong terverifikasi). Sisa: user menghapus token lama di github.com/settings/tokens.
2. [~] Commit pertama ke Git + cadangan database (`data/katalog.db`). Kode sudah di-push ke github.com/BagusFary/agentloadout (privat, deploy key). Sisa: cadangan database harian.
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
