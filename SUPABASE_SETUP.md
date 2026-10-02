# Panduan Integrasi Supabase

Panduan ini berlaku untuk proyek `final_project_supabase` yang sudah dimodifikasi.

## 1. Arsitektur Setelah Integrasi

Alur sistem menjadi:

1. Streamlit membaca `bahan_mentah` dan `akg` dari Supabase.
2. `src/data_loader.py` mengubah hasil query menjadi DataFrame.
3. `src/optimizer.py` tetap menjalankan NSGA-II tanpa perubahan logika.
4. `src/database.py` menyimpan hasil optimasi ke tabel riwayat.
5. Jika Supabase belum aktif atau gagal dibaca, sistem memakai CSV lokal sebagai fallback.

Tabel yang digunakan:

- `bahan_mentah`: master bahan pangan.
- `akg`: master angka kecukupan gizi.
- `optimasi_run`: satu baris untuk satu eksekusi optimasi.
- `optimasi_hari`: data setiap hari dalam satu run.
- `optimasi_menu`: tiga rekomendasi menu per hari.
- `optimasi_item`: detail karbohidrat, lauk, sayur, dan buah.
- `evaluasi_mingguan`: evaluasi nutrisi dan adaptasi AKG.

## 2. Buat Proyek Supabase

1. Masuk ke Supabase Dashboard.
2. Pilih **New project**.
3. Isi nama proyek, password database, dan region.
4. Tunggu sampai proyek aktif.

## 3. Buat Seluruh Tabel

1. Buka **SQL Editor** pada dashboard Supabase.
2. Pilih **New query**.
3. Buka file `supabase/schema.sql` dari proyek ini.
4. Salin seluruh isi file.
5. Tempel ke SQL Editor.
6. Klik **Run**.
7. Buka **Table Editor** dan pastikan tujuh tabel telah terbentuk.

File SQL juga mengaktifkan Row Level Security. Aplikasi ini belum memakai login, sehingga policy dibuat untuk role `anon` dan `authenticated`. Policy tersebut cocok untuk prototipe skripsi, tetapi tidak cocok untuk data pribadi pada sistem produksi.

## 4. Instal Dependensi

Buka terminal pada folder proyek:

```bash
cd final_project_supabase
python -m venv .venv
```

Aktifkan virtual environment pada Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Instal dependensi:

```bash
python -m pip install -r requirements.txt
```

Dependensi baru yang ditambahkan adalah:

```txt
supabase>=2.0.0,<3.0.0
```

## 5. Unggah CSV ke Supabase

### 5.1 Ambil kredensial

Pada Supabase Dashboard, buka pengaturan API proyek. Salin:

- Project URL
- `service_role` key

`service_role` hanya digunakan untuk proses seed. Jangan letakkan key ini di source code, GitHub, atau file `secrets.toml` aplikasi.

### 5.2 Set environment variable pada PowerShell

```powershell
$env:SUPABASE_URL="https://PROJECT_ID.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="SERVICE_ROLE_KEY_ANDA"
```

### 5.3 Jalankan seed

```bash
python scripts/seed_supabase.py
```

Output yang benar:

```text
Mengunggah 143 baris bahan mentah...
Mengunggah 2 baris AKG...
Seed selesai. Master data Supabase sudah siap.
```

Verifikasi melalui Table Editor:

- `bahan_mentah` berisi 143 baris.
- `akg` berisi 2 baris.

Script menggunakan `upsert`, sehingga aman dijalankan ulang. Baris dengan `kode_bahan` atau `kelompok_umur` yang sama akan diperbarui.

## 6. Hubungkan Aplikasi Streamlit

### 6.1 Buat file secrets

Salin:

```text
.streamlit/secrets.toml.example
```

menjadi:

```text
.streamlit/secrets.toml
```

### 6.2 Isi anon key

Isi file `.streamlit/secrets.toml`:

```toml
SUPABASE_URL = "https://PROJECT_ID.supabase.co"
SUPABASE_ANON_KEY = "ANON_KEY_ANDA"
```

Gunakan `anon` key, bukan `service_role` key. File tersebut sudah dimasukkan ke `.gitignore`.

## 7. Jalankan Aplikasi

```bash
streamlit run app/streamlit_app.py
```

Indikator yang benar:

- Bagian atas menampilkan `Sumber master data: Supabase`.
- Sidebar menampilkan `Database Supabase aktif`.
- Kelompok umur tetap menampilkan `7-9 tahun` dan `10-12 tahun`.

## 8. Uji Penyimpanan Harian

1. Pilih kelompok umur.
2. Isi jumlah siswa.
3. Tentukan target harga.
4. Klik **Optimasi 1 Hari**.
5. Buka Supabase Table Editor.

Hasil yang seharusnya terbentuk:

- 1 baris baru pada `optimasi_run` dengan `mode = daily`.
- 1 baris pada `optimasi_hari`.
- 3 baris pada `optimasi_menu`.
- 12 baris pada `optimasi_item`, yaitu 4 bahan untuk setiap menu.

Nilai Pareto run pertama disimpan pada kolom JSONB `optimasi_hari.pareto_front`.

## 9. Uji Penyimpanan Mingguan

1. Klik **Optimasi 5 Hari**.
2. Tunggu lima hari selesai dihitung.
3. Pilih satu menu untuk setiap hari.
4. Buka tab **Evaluasi Mingguan**.
5. Klik **Simpan/Perbarui Evaluasi di Supabase**.

Hasil yang seharusnya terbentuk:

- 1 baris `optimasi_run` dengan `mode = weekly`.
- 5 baris `optimasi_hari`.
- Maksimal 15 baris `optimasi_menu`.
- Maksimal 60 baris `optimasi_item`.
- Menu pilihan memiliki `terpilih = true`.
- 1 baris pada `evaluasi_mingguan`.

Jika tombol adaptasi AKG ditekan, `akg_adaptif` dan `log_adaptasi` juga disimpan.

## 10. File yang Diubah

### `requirements.txt`

Menambahkan paket Python Supabase.

### `src/database.py`

Menangani:

- pembacaan secrets;
- pembuatan client;
- query master data;
- serialisasi pandas dan NumPy ke JSON;
- penyimpanan run harian dan mingguan;
- penyimpanan pilihan menu;
- penyimpanan evaluasi dan adaptasi AKG;
- pembacaan riwayat.

### `src/data_loader.py`

Menambahkan fungsi:

```python
load_data_from_supabase(repository)
```

Fungsi ini menyamakan data Supabase dengan bentuk DataFrame yang dibutuhkan optimizer.

### `app/streamlit_app.py`

Menambahkan:

- koneksi Supabase yang di-cache;
- fallback ke CSV;
- penyimpanan otomatis setelah optimasi;
- update menu terpilih;
- tombol simpan evaluasi;
- tab riwayat Supabase.

### `scripts/seed_supabase.py`

Mengunggah CSV master ke Supabase menggunakan service role key.

### `supabase/schema.sql`

Membuat tabel, relasi, index, constraint, cascade delete, RLS, dan policy.

## 11. Penanganan Masalah

### Pesan `Supabase belum dikonfigurasi`

Penyebab: file `.streamlit/secrets.toml` belum dibuat atau nama variabel salah.

Periksa:

```toml
SUPABASE_URL = "..."
SUPABASE_ANON_KEY = "..."
```

### Sumber data tetap CSV lokal

Penyebab umum:

- tabel belum dibuat;
- master data belum di-seed;
- anon key salah;
- policy belum terbentuk;
- koneksi internet gagal.

Lihat detail peringatan pada bagian atas aplikasi.

### Foreign key gagal saat menyimpan run

Penyebab: nilai `kelompok_umur` pada aplikasi belum tersedia di tabel `akg`.

Solusi:

```bash
python scripts/seed_supabase.py
```

### Package `supabase` tidak ditemukan

Jalankan:

```bash
python -m pip install -r requirements.txt
```

### Perubahan data Supabase belum terlihat

Cache master data berlaku 300 detik. Restart Streamlit atau pilih menu **Clear cache** untuk memuat ulang lebih cepat.

## 12. Catatan Keamanan

- Jangan commit `.streamlit/secrets.toml`.
- Jangan gunakan service role key di aplikasi Streamlit.
- Jangan menampilkan key pada halaman.
- Untuk sistem publik, tambahkan Supabase Auth dan ubah policy berdasarkan `auth.uid()`.
- Policy terbuka pada proyek ini hanya mendukung sistem skripsi tanpa akun pengguna.
