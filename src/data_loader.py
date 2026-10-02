import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# PEMETAAN KATEGORI → 4 SEHAT
# ─────────────────────────────────────────────
PETA_KATEGORI = {
    'karbohidrat': ['serealia', 'umbi_berpati'],
    'lauk'       : ['daging', 'ikan', 'telur', 'kacang_biji'],
    'sayur'      : ['sayuran'],
    'buah'       : ['buah'],
}

# ─────────────────────────────────────────────
# Hanya 5 variabel nutrisi yang digunakan
# ─────────────────────────────────────────────
NUTRISI_COLS = ['energi', 'protein', 'lemak', 'karbohidrat', 'serat']


def load_data(path_bahan='data/bahan_mentah.csv',
              path_akg='data/akg.csv'):
    """
    Load dataset bahan mentah + AKG ke DataFrame.

    CATATAN: fungsi ini TIDAK melakukan pembersihan data (missing value,
    pemetaan kategori→kelompok, konversi harga). Semua itu sudah
    dilakukan sebelumnya di Google Colab. File ini hanya:
      1. Membaca CSV apa adanya.
      2. Memastikan kolom nutrisi & harga_per_100g bertipe numerik
         (jaga-jaga kalau ada nilai aneh saat CSV dibaca ulang).
    Kolom 'harga_per_100g' dan 'kelompok' diasumsikan SUDAH ADA
    dan sudah benar di dalam bahan_mentah.csv.
    """
    # ── Bahan mentah (sudah bersih dari Colab) ─────────────
    df = pd.read_csv(path_bahan)

    for col in NUTRISI_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    df['harga_per_100g'] = pd.to_numeric(
        df['harga_per_100g'], errors='coerce'
    ).fillna(0)

    # ── AKG ───────────────────────────────────────────────
    df_akg = pd.read_csv(path_akg)
    for col in NUTRISI_COLS:
        if col in df_akg.columns:
            df_akg[col] = pd.to_numeric(df_akg[col], errors='coerce').fillna(0)

    return df, df_akg


def load_data_from_supabase(repository):
    """
    Ambil master bahan dan AKG dari Supabase, lalu samakan tipe datanya
    dengan struktur DataFrame yang sebelumnya dibaca dari CSV.
    """
    bahan_rows = repository.fetch_bahan_mentah()
    akg_rows = repository.fetch_akg()

    if not bahan_rows:
        raise ValueError("Tabel bahan_mentah di Supabase masih kosong.")
    if not akg_rows:
        raise ValueError("Tabel akg di Supabase masih kosong.")

    df = pd.DataFrame(bahan_rows)
    df_akg = pd.DataFrame(akg_rows)

    # Kolom teknis database tidak diperlukan oleh algoritma.
    df = df.drop(
        columns=["aktif", "created_at", "updated_at"],
        errors="ignore"
    )
    df_akg = df_akg.drop(
        columns=["urutan", "created_at", "updated_at"],
        errors="ignore"
    )

    for col in NUTRISI_COLS + ["harga_kg", "harga_per_100g"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    for col in NUTRISI_COLS + ["kalsium", "besi", "seng"]:
        if col in df_akg.columns:
            df_akg[col] = pd.to_numeric(
                df_akg[col], errors="coerce"
            ).fillna(0)

    required_bahan = {
        "kode_bahan", "nama_bahan", "energi", "protein", "lemak",
        "karbohidrat", "serat", "harga_per_100g", "kategori", "kelompok"
    }
    missing_bahan = required_bahan.difference(df.columns)
    if missing_bahan:
        raise ValueError(
            "Kolom tabel bahan_mentah belum lengkap: "
            + ", ".join(sorted(missing_bahan))
        )

    required_akg = {"kelompok_umur", *NUTRISI_COLS}
    missing_akg = required_akg.difference(df_akg.columns)
    if missing_akg:
        raise ValueError(
            "Kolom tabel akg belum lengkap: "
            + ", ".join(sorted(missing_akg))
        )

    return df.reset_index(drop=True), df_akg.reset_index(drop=True)


def get_akg_by_kelompok(df_akg, kelompok_umur):
    """
    Ambil baris AKG berdasarkan string kelompok umur.
    """
    row = df_akg[df_akg['kelompok_umur'] == kelompok_umur]
    if row.empty:
        raise ValueError(
            f"Kelompok umur '{kelompok_umur}' tidak ditemukan. "
            f"Pilihan: {df_akg['kelompok_umur'].tolist()}"
        )
    return row.iloc[0]


def split_by_kelompok(df_bahan):
    """
    Pisahkan dataset menjadi dict berdasarkan kelompok 4 sehat.
    Hanya kembalikan kelompok yang ada datanya.
    """
    hasil = {}
    for kelompok in PETA_KATEGORI.keys():
        subset = df_bahan[df_bahan['kelompok'] == kelompok].reset_index(drop=True)
        if len(subset) > 0:
            hasil[kelompok] = subset
    return hasil


def hitung_nutrisi_per_porsi(bahan_row, porsi_gram):
    """
    Hitung kandungan nutrisi untuk porsi tertentu.
    Nilai nutrisi di dataset = per 100g bagian yang bisa dimakan.
    """
    faktor = porsi_gram / 100.0
    result = {}
    for n in NUTRISI_COLS:
        result[n] = float(bahan_row[n]) * faktor
    return result


# ─── Test sederhana ──────────────────────────────────────
if __name__ == '__main__':
    df_bahan, df_akg = load_data()

    print("=" * 50)
    print("DATASET BAHAN MENTAH")
    print(f"Total bahan: {len(df_bahan)}")
    print("\nJumlah per kelompok (4 sehat):")
    print(df_bahan['kelompok'].value_counts())

    print("\nJumlah per kategori asli:")
    print(df_bahan['kategori'].value_counts())

    print("\n" + "=" * 50)
    print("DATASET AKG")
    print(df_akg.to_string())

    print("\n" + "=" * 50)
    kelompok_dict = split_by_kelompok(df_bahan)
    print("Kelompok yang siap digunakan optimasi:")
    for k, v in kelompok_dict.items():
        print(f"  {k:12s}: {len(v)} bahan")
