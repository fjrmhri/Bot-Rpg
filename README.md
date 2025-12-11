<p align="center">
  <img src="https://img.shields.io/github/stars/fjrmhri/Bot-Rpg?style=for-the-badge&logo=github&color=8b5cf6" alt="Stars"/>
  <img src="https://img.shields.io/github/license/fjrmhri/Bot-Rpg?style=for-the-badge&color=10b981" alt="License"/>
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python" alt="Python"/>
  <img src="https://img.shields.io/badge/python--telegram--bot-20.7-26A5E4?style=for-the-badge&logo=telegram" alt="python-telegram-bot"/>
  <img src="https://img.shields.io/badge/Asyncio-ready-0ea5e9?style=for-the-badge&logo=fastapi" alt="Asyncio"/>
</p>

# Legends of Aruna: Journey to Kampar

## Deskripsi Singkat
Game bot Telegram berbasis teks dengan sistem turn-based RPG, berlatar petualangan Aruna menuju Kampar. Seluruh logika masih berada di satu berkas Python, namun sudah dipisah secara konseptual menjadi beberapa agent (story, battle, world, inventory, progression) untuk memudahkan pemeliharaan.

## Fitur Utama
- Cerita naratif bercabang yang diambil dari berkas JSON `data/scenes_main.json`.
- Sistem battle turn-based lengkap dengan skill, buff/debuff, dan drop item.
- Kota, dungeon, dan area berburu dengan persyaratan level serta event cerita.
- Inventory, equipment, dan quest tracker (termasuk auto-hunt dan autosave bos).
- Penyimpanan progres pemain ke berkas JSON per pengguna dengan mekanisme atomic.

## Cara Instalasi & Menjalankan Proyek
1. Pastikan Python 3.11+ tersedia.
2. Instal dependensi utama (termasuk pembaca `.env`):
   ```bash
   pip install python-telegram-bot==20.7 python-dotenv
   ```
3. Buat berkas `.env` di root repository (sejajar dengan README) dengan isi minimal berikut:
   ```env
   TELEGRAM_BOT_TOKEN=isi_token_dari_BotFather
   # Pisahkan dengan koma bila lebih dari satu admin
   ADMIN_USER_IDS=123456789,987654321
   ```
4. Jalankan bot:
   ```bash
   python LEGENDS_OF_ARUNA_JOURNEY_TO_KAMPAR.py
   ```
5. Mulai percakapan dengan bot di Telegram dan kirim perintah `/start`.

## Konfigurasi
- **Token Bot**: Wajib diisi pada variabel environment `TELEGRAM_BOT_TOKEN` (bisa lewat `.env`).
- **Admin User IDs**: Opsional, isi `ADMIN_USER_IDS` di `.env` dengan daftar ID yang dipisah koma.
- **Direktori Save**: Folder `saves/` akan dibuat otomatis untuk menyimpan progres per pengguna.
- **Autosave**: Diatur melalui konstanta `AUTOSAVE_ENABLED` dan `AUTOSAVE_BOSS_KEYS`.
- **Log**: Berkas log akan disimpan di folder `logs/` bila dapat dibuat.

## Lisensi
Lisensi belum ditentukan secara eksplisit di repositori ini. Harap hubungi pemilik proyek sebelum penggunaan ulang atau distribusi.

## Informasi Tambahan
- Struktur agent dijelaskan di `AGENTS.md` dan ringkasan implementasi di `IMPLEMENTATION_SUMMARY.md`.
- Data cerita utama berada di `data/scenes_main.json`; menambah konten cukup dengan memperluas berkas tersebut.
