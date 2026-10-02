# 🥗 Rekomendasi Menu Makan Bergizi Gratis (NSGA-II)

Aplikasi optimasi multi-objektif berbasis **NSGA-II** untuk rekomendasi menu Makan Bergizi Gratis anak Indonesia.

---

## 📁 Struktur Proyek

```
skripsi-makan-bergizi/
├── app/
│   └── streamlit_app.py      # Antarmuka utama Streamlit
├── src/
│   ├── __init__.py
│   ├── optimizer.py          # Logika NSGA-II (pymoo)
│   └── preprocessing.py      # Load & transformasi data
├── data/
│   ├── akg.csv               # Angka Kecukupan Gizi per kelompok umur
│   └── bahan_mentah.csv      # Database bahan pangan + harga
├── requirements.txt
└── README.md
```

---

## ⚙️ Parameter yang Ditetapkan (Fixed)

| Parameter              | Nilai  | Keterangan                              |
|------------------------|--------|-----------------------------------------|
| Ukuran populasi        | 200    | Fixed, tidak dapat diubah pengguna      |
| Jumlah generasi        | 300    | Fixed, tidak dapat diubah pengguna      |
| Proporsi makan siang   | 35%    | Sesuai Pedoman Gizi Seimbang Kemenkes   |
| Harga minimum per porsi| Rp 5.000 | Batas bawah slider biaya             |
| Harga maksimum per porsi| Rp 20.000 | Batas atas slider biaya            |

---

## 🚀 Cara Menjalankan

### 1. Prasyarat
- Python 3.9 – 3.11
- pip

### 2. Install dependensi
```bash
pip install -r requirements.txt
```

### 3. Jalankan aplikasi
```bash
streamlit run app/streamlit_app.py
```

---

## 🎲 Variasi Hasil Setiap Run

Setiap kali tombol **"Jalankan Optimasi"** ditekan, seed NSGA-II  
digenerate otomatis dari waktu sistem (`int(time.time() * 1000) % 2^31`).  
Ini memastikan solusi yang dihasilkan **selalu bervariasi**, bahkan  
dengan parameter yang identik.

---

## 📊 Tentang Deviasi Gizi (%)

**Deviasi gizi** adalah rata-rata selisih relatif antara kandungan nutrisi  
menu hasil optimasi dengan **target makan siang** (35% AKG harian):

```
deviasi_i = |aktual_i - target_i| / target_i
deviasi_total = mean(deviasi_i untuk semua nutrisi)
```

Nilai ini dikalikan 100 untuk ditampilkan dalam persen.  
**Semakin kecil nilainya, semakin mendekati kebutuhan gizi yang ditargetkan.**

---

## 📚 Referensi

- Pedoman Gizi Seimbang — Kemenkes RI
- Daftar Komposisi Bahan Makanan (DKBM) 2019
- Permenkes No. 28 Tahun 2019 tentang AKG
- Deb et al. (2002) — A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II

---

## Integrasi Supabase

Versi ini mendukung Supabase sebagai database utama dan CSV sebagai fallback. Panduan lengkap tersedia pada [`SUPABASE_SETUP.md`](SUPABASE_SETUP.md).

Urutan singkat:

```bash
# 1. Jalankan supabase/schema.sql pada SQL Editor
# 2. Set SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY
python scripts/seed_supabase.py

# 3. Buat .streamlit/secrets.toml dari file contoh
# 4. Jalankan aplikasi
streamlit run app/streamlit_app.py
```
