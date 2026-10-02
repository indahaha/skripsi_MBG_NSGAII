import sys
import os
import uuid
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from src.data_loader import (
    load_data, load_data_from_supabase, get_akg_by_kelompok,
    split_by_kelompok, NUTRISI_COLS
)
from src.database import SupabaseRepository
from src.optimizer import (
    run_optimization,
    run_weekly_optimization,
    evaluasi_nutrisi_mingguan,
    adjust_akg_based_on_history,
    generate_insights,
    FAKTOR_MAKAN_SIANG,
    PORSI_BATAS,
    POP_SIZE_FIXED,
    N_GEN_FIXED,
    KELOMPOK_ORDER,
    HARI_PLANNING,
    NAMA_HARI,
)

# ─────────────────────────────────────────────────────────────
# Konfigurasi halaman
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Optimasi Menu Makan Bergizi Gratis",
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🥗 Rekomendasi Menu Makan Bergizi Gratis")
st.markdown(
    "Aplikasi **optimasi multi-objektif** berbasis **NSGA-II** untuk "
    "rekomendasi menu 4 sehat pada program makan bergizi "
    "gratis anak Indonesia."
)

# ─────────────────────────────────────────────────────────────
# Konstanta tampilan
# ─────────────────────────────────────────────────────────────
FAKTOR_MAKAN = FAKTOR_MAKAN_SIANG
FAKTOR_PCT   = 35

EMOJI = {'karbohidrat': '🍚', 'lauk': '🍗', 'sayur': '🥬', 'buah': '🍌'}
WARNA = {
    'karbohidrat': '#FFF3CD',
    'lauk'       : '#FFE0E0',
    'sayur'      : '#D4EDDA',
    'buah'       : '#D1ECF1',
}
SATUAN = {
    'energi': 'kkal', 'protein': 'g', 'lemak': 'g',
    'karbohidrat': 'g', 'serat': 'g',
}
NUTRISI_DISPLAY = [
    ("Energi",      "energi",      "kkal"),
    ("Protein",     "protein",     "g"),
    ("Lemak",       "lemak",       "g"),
    ("Karbohidrat", "karbohidrat", "g"),
    ("Serat",       "serat",       "g"),
]
META = {
    'bergizi' : {
        'label'    : '🥇 Menu Paling Bergizi',
        'short'    : 'Bergizi',
        'deskripsi': 'Kombinasi bahan dengan deviasi nutrisi terendah dari target makan siang.',
        'color'    : '#e63946',
    },
    'harga'   : {
        'label'    : '🎯 Menu Paling Tepat Harga',
        'short'    : 'Tepat Harga',
        'deskripsi': 'Kombinasi bahan berbeda yang paling mendekati target harga.',
        'color'    : '#2a9d8f',
    },
    'seimbang': {
        'label'    : '⚖️ Menu Seimbang',
        'short'    : 'Seimbang',
        'deskripsi': 'Kombinasi bahan alternatif dengan profil gizi & harga paling stabil.',
        'color'    : '#f4a261',
    },
}


# Presisi tampilan untuk dokumentasi perhitungan manual.
DESIMAL_PORSI   = 6
DESIMAL_NUTRISI = 6
DESIMAL_PERSEN  = 6
DESIMAL_RUPIAH  = 2


def format_angka_id(nilai, desimal=2):
    """Format angka dengan pemisah Indonesia tanpa mengubah nilai aslinya."""
    if nilai is None:
        return "-"
    hasil = f"{float(nilai):,.{desimal}f}"
    return hasil.replace(",", "_").replace(".", ",").replace("_", ".")


def format_rupiah(nilai, desimal=DESIMAL_RUPIAH):
    return f"Rp {format_angka_id(nilai, desimal)}"

# ─────────────────────────────────────────────────────────────
# Koneksi database dan load master data
# ─────────────────────────────────────────────────────────────
@st.cache_resource
def get_database():
    return SupabaseRepository.from_config()


database = get_database()


@st.cache_data(ttl=300)
def load_semua_data(_database, database_enabled):
    """Gunakan Supabase sebagai sumber utama dan CSV sebagai fallback."""
    if database_enabled:
        try:
            bahan, akg_data = load_data_from_supabase(_database)
            return bahan, akg_data, "Supabase", None
        except Exception as exc:
            bahan, akg_data = load_data(
                path_bahan='data/bahan_mentah.csv',
                path_akg='data/akg.csv'
            )
            return bahan, akg_data, "CSV lokal", str(exc)

    bahan, akg_data = load_data(
        path_bahan='data/bahan_mentah.csv',
        path_akg='data/akg.csv'
    )
    return bahan, akg_data, "CSV lokal", database.configuration_error


df_bahan, df_akg, sumber_data, database_load_error = load_semua_data(
    database, database.enabled
)
kelompok_data_all = split_by_kelompok(df_bahan)

if sumber_data == "Supabase":
    st.caption("🟢 Sumber master data: **Supabase**")
else:
    st.caption("🟡 Sumber master data: **CSV lokal (fallback)**")
    if database.enabled and database_load_error:
        st.warning(
            "Koneksi Supabase tersedia, tetapi master data belum dapat dibaca. "
            f"Aplikasi memakai CSV lokal. Detail: {database_load_error}"
        )

# ─────────────────────────────────────────────────────────────
# Inisialisasi session_state
# ─────────────────────────────────────────────────────────────
def _init_session():
    defaults = {
        # — Hasil optimasi harian (tab lama) —
        'tiga_menu'      : None,
        'sol1_list'      : None,
        'F1'             : None,
        'evaluasi_pareto': None,
        'akg'            : None,
        'target_makan'   : None,
        'harga_target'   : None,
        'kelompok_umur'  : None,
        # — Rencana mingguan —
        'rencana_minggu' : None,   # list[dict] satu entry per hari
        'pilihan_harian' : [None] * HARI_PLANNING,  # pilihan user per hari
        # — Adaptasi AKG —
        'akg_adaptif'    : None,   # AKG yang sudah diadaptasi
        'log_adaptasi'   : None,
        # — Parameter terakhir —
        'param_kel_umur' : None,
        'param_harga'    : None,
        # — Identitas dan relasi database —
        'client_session_id': str(uuid.uuid4()),
        'db_daily_run_id'  : None,
        'db_weekly_run_id' : None,
        'db_last_error'    : None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()

# ─────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Parameter Optimasi")

    kelompok_umur = st.selectbox(
        "Kelompok umur anak",
        options=df_akg['kelompok_umur'].tolist(),
        index=0,
    )

    st.markdown("---")
    st.subheader("👨‍🎓 Jumlah Siswa")
    jumlah_siswa = st.number_input(
        "Jumlah siswa",
        min_value=1, max_value=10000, value=30, step=1,
        help="Rincian 1 porsi tetap ditampilkan. "
             "Total biaya akan dikalikan jumlah siswa ini."
    )

    st.markdown("---")
    st.subheader("💰 Target Harga per Porsi")
    harga_target = st.slider(
        "Target harga 1 porsi (Rp)",
        min_value=5_000, max_value=20_000,
        value=10_000, step=500, format="Rp %d",
    )
    st.caption(f"🎯 Target: **Rp {harga_target:,}** per porsi")

    st.markdown("---")

    # Tombol optimasi harian (fitur lama)
    run_btn = st.button(
        "🚀 Optimasi 1 Hari",
        type="primary",
        use_container_width=True,
        help="Hasilkan 3 menu terbaik untuk satu hari."
    )

    # Tombol optimasi mingguan (fitur baru)
    run_weekly_btn = st.button(
        "📅 Optimasi 5 Hari",
        type="secondary",
        use_container_width=True,
        help="Hasilkan rencana menu 5 hari dengan variasi bahan otomatis."
    )

    # Tombol adaptasi AKG
    if st.session_state['rencana_minggu'] is not None:
        st.markdown("---")
        adapt_btn = st.button(
            "🧠 Adaptasi AKG Minggu Depan",
            use_container_width=True,
            help="Sesuaikan target nutrisi berdasarkan evaluasi minggu ini."
        )
    else:
        adapt_btn = False

    st.markdown("---")
    st.caption(f"Pop={POP_SIZE_FIXED} | Gen={N_GEN_FIXED} | Makan siang {FAKTOR_PCT}%")
    if database.enabled and sumber_data == "Supabase":
        st.success("🟢 Database Supabase aktif")
    elif database.enabled:
        st.warning("🟡 Supabase aktif, data memakai fallback CSV")
    else:
        st.info("⚪ Supabase belum dikonfigurasi")

# ─────────────────────────────────────────────────────────────
# AKG — tampilkan target
# ─────────────────────────────────────────────────────────────
# Gunakan AKG adaptif jika ada, AKG asli jika belum ada adaptasi
akg_asli = get_akg_by_kelompok(df_akg, kelompok_umur)
akg = st.session_state.get('akg_adaptif') if st.session_state.get('akg_adaptif') is not None else akg_asli

st.subheader(f"🎯 Target Gizi — {kelompok_umur}")

col_info1, col_info2 = st.columns(2)
with col_info1:
    if st.session_state.get('akg_adaptif') is not None:
        st.warning(
            "🧠 **AKG Adaptif Aktif** — Target nutrisi sudah disesuaikan "
            "berdasarkan evaluasi minggu lalu. Lihat tab **Evaluasi Mingguan** "
            "untuk detail perubahan."
        )
    else:
        st.info(
            f"**AKG Harian** = kebutuhan gizi 1 hari penuh. "
            f"Target makan siang MBG = **{FAKTOR_PCT}% × AKG harian**.\n\n"
            f"Nutrisi yang dioptimasi: **Energi, Protein, Lemak, Karbohidrat, Serat**"
        )
with col_info2:
    st.success(
        "Porsi realistis: Karbohidrat 100–200g · Lauk 50–100g · "
        "Sayur 50–100g · Buah 75–150g"
    )

tab_akg1, tab_akg2 = st.tabs([
    f"🍽️ Target Makan Siang ({FAKTOR_PCT}% AKG)",
    "📋 AKG Harian Penuh"
])
with tab_akg1:
    st.caption(f"Target optimasi NSGA-II ({FAKTOR_PCT}% dari AKG harian) — 5 nutrisi")
    cols = st.columns(5)
    for col, (label, key, sat) in zip(cols, NUTRISI_DISPLAY):
        col.metric(label, f"{format_angka_id(akg[key] * FAKTOR_MAKAN, DESIMAL_NUTRISI)} {sat}",
                   delta=f"dari {akg[key]} {sat}/hari", delta_color="off")
with tab_akg2:
    st.caption("Angka Kecukupan Gizi harian penuh (sumber: Permenkes RI)")
    cols = st.columns(5)
    for col, (label, key, sat) in zip(cols, NUTRISI_DISPLAY):
        col.metric(label, f"{format_angka_id(akg[key], DESIMAL_NUTRISI)} {sat}")

kelompok_aktif = kelompok_data_all

# ─────────────────────────────────────────────────────────────
# Aksi: Optimasi 1 Hari (fitur lama)
# ─────────────────────────────────────────────────────────────
if run_btn:
    with st.spinner(
        f"Menjalankan NSGA-II (3 run) — target harga Rp {harga_target:,} | "
        f"pop={POP_SIZE_FIXED}, gen={N_GEN_FIXED} ⏳"
    ):
        (
            tiga_menu, sol1_list, F1, target_makan, ht, evaluasi_pareto
        ) = run_optimization(
            kelompok_aktif, akg_asli,
            pop_size=POP_SIZE_FIXED,
            n_gen=N_GEN_FIXED,
            seed=None,
            faktor_makan=FAKTOR_MAKAN,
            harga_target=harga_target,
        )

    st.session_state.update({
        'tiga_menu'    : tiga_menu,
        'sol1_list'    : sol1_list,
        'F1'           : F1,
        'evaluasi_pareto': evaluasi_pareto,
        'akg'          : akg,
        'target_makan' : target_makan,
        'harga_target' : ht,
        'kelompok_umur': kelompok_umur,
    })

    if database.enabled and sumber_data == "Supabase":
        try:
            daily_run_id = database.save_daily_optimization(
                session_id=st.session_state['client_session_id'],
                kelompok_umur=kelompok_umur,
                jumlah_siswa=jumlah_siswa,
                harga_target=ht,
                faktor_makan=FAKTOR_MAKAN,
                pop_size=POP_SIZE_FIXED,
                n_gen=N_GEN_FIXED,
                akg_digunakan=akg_asli,
                target_makan=target_makan,
                menus=tiga_menu,
                pareto_front=F1,
            )
            st.session_state['db_daily_run_id'] = daily_run_id
            st.session_state['db_last_error'] = None
            st.toast("Hasil optimasi harian tersimpan di Supabase.", icon="💾")
        except Exception as exc:
            st.session_state['db_last_error'] = str(exc)
            st.warning(f"Optimasi selesai, tetapi penyimpanan Supabase gagal: {exc}")

    st.success(
        f"✅ Selesai! 3 menu berhasil ditemukan. "
        f"({len(sol1_list)} solusi Pareto di run pertama)"
    )

# ─────────────────────────────────────────────────────────────
# Aksi: Optimasi 5 Hari (fitur baru)
# ─────────────────────────────────────────────────────────────
if run_weekly_btn:
    progress_bar  = st.progress(0, text="Memulai optimasi mingguan...")
    status_text   = st.empty()

    def update_progress(hari_ke, total):
        pct  = int(hari_ke / total * 100)
        nama = NAMA_HARI[hari_ke - 1] if hari_ke - 1 < len(NAMA_HARI) else f'Hari {hari_ke}'
        progress_bar.progress(pct, text=f"✅ Selesai hari {hari_ke}/{total} ({nama})")
        status_text.info(f"🔄 Mengoptimasi hari {hari_ke+1}/{total}..." if hari_ke < total else "")

    rencana = run_weekly_optimization(
        kelompok_aktif, akg,
        pop_size=POP_SIZE_FIXED,
        n_gen=N_GEN_FIXED,
        seed=None,
        faktor_makan=FAKTOR_MAKAN,
        harga_target=harga_target,
        n_hari=HARI_PLANNING,
        progress_callback=update_progress,
    )

    progress_bar.progress(100, text="✅ Optimasi 5 hari selesai!")
    status_text.empty()

    st.session_state.update({
        'rencana_minggu': rencana,
        'pilihan_harian': [None] * HARI_PLANNING,
        'param_kel_umur': kelompok_umur,
        'param_harga'   : harga_target,
        'target_makan'  : rencana[0]['target_makan'],
        'akg'           : akg,
        'kelompok_umur' : kelompok_umur,
        'harga_target'  : harga_target,
    })

    if database.enabled and sumber_data == "Supabase":
        try:
            weekly_run_id = database.save_weekly_optimization(
                session_id=st.session_state['client_session_id'],
                kelompok_umur=kelompok_umur,
                jumlah_siswa=jumlah_siswa,
                harga_target=harga_target,
                faktor_makan=FAKTOR_MAKAN,
                pop_size=POP_SIZE_FIXED,
                n_gen=N_GEN_FIXED,
                akg_digunakan=akg,
                weekly_plan=rencana,
            )
            st.session_state['db_weekly_run_id'] = weekly_run_id
            st.session_state['db_last_error'] = None
            st.toast("Rencana lima hari tersimpan di Supabase.", icon="💾")
        except Exception as exc:
            st.session_state['db_last_error'] = str(exc)
            st.warning(f"Optimasi selesai, tetapi penyimpanan Supabase gagal: {exc}")

    st.success(
        f"✅ Rencana 5 hari berhasil dibuat! "
        f"Setiap hari memiliki 3 menu dengan bahan bervariasi. "
        f"Buka tab **📅 Rencana 5 Hari** untuk memilih menu."
    )

# ─────────────────────────────────────────────────────────────
# Aksi: Adaptasi AKG
# ─────────────────────────────────────────────────────────────
if adapt_btn:
    pilihan = st.session_state.get('pilihan_harian', [])
    pilihan_valid = [p for p in pilihan if p is not None]

    if len(pilihan_valid) < 3:
        st.warning(
            "⚠️ Pilih minimal 3 hari menu terlebih dahulu di tab "
            "**📅 Rencana 5 Hari** sebelum mengadaptasi AKG."
        )
    else:
        target_makan = st.session_state.get('target_makan', {})
        akg_baru, log = adjust_akg_based_on_history(
            akg_asli       = akg_asli,
            riwayat_pilihan= pilihan_valid,
            faktor_makan   = FAKTOR_MAKAN,
        )
        st.session_state['akg_adaptif'] = akg_baru
        st.session_state['log_adaptasi'] = log

        weekly_run_id = st.session_state.get('db_weekly_run_id')
        if database.enabled and weekly_run_id:
            try:
                evaluasi_adaptasi = evaluasi_nutrisi_mingguan(
                    pilihan_valid, target_makan, n_hari=len(pilihan_valid)
                )
                insights_adaptasi = generate_insights(
                    evaluasi_adaptasi, target_makan
                )
                database.save_weekly_evaluation(
                    run_id=weekly_run_id,
                    selected_days=len(pilihan_valid),
                    evaluation=evaluasi_adaptasi,
                    insights=insights_adaptasi,
                    adapted_akg=akg_baru,
                    adaptation_log=log,
                )
                st.toast("Adaptasi AKG tersimpan di Supabase.", icon="💾")
            except Exception as exc:
                st.warning(f"AKG berhasil diadaptasi, tetapi gagal disimpan: {exc}")

        st.success(
            "🧠 AKG berhasil diadaptasi! Jalankan kembali optimasi "
            "untuk menggunakan target nutrisi yang telah disesuaikan."
        )

# ═════════════════════════════════════════════════════════════
# TABS UTAMA
# ═════════════════════════════════════════════════════════════
tab_pareto, tab_menu, tab_gizi, tab_semua, tab_minggu, tab_evaluasi, tab_riwayat = st.tabs([
    "📈 Pareto Front",
    "🍽️ 3 Rekomendasi Menu",
    "📊 Analisis Gizi",
    "📋 Semua Solusi",
    "📅 Rencana 5 Hari",
    "📊 Evaluasi Mingguan",
    "🗄️ Riwayat Supabase",
])

# ─────────────────────────────────────────────────────────────
# Helper: render kartu bahan
# ─────────────────────────────────────────────────────────────
def _render_kartu_bahan(kat, info):
    bahan      = info['bahan']
    porsi      = info['porsi']
    biaya_item = (porsi / 100.0) * float(bahan['harga_per_100g'])
    e_item     = float(bahan['energi'])  * porsi / 100
    p_item     = float(bahan['protein']) * porsi / 100
    return f"""<div style="
        background:{WARNA.get(kat,'#f8f9fa')};
        border-radius:10px;padding:12px 14px;
        border:1px solid #dee2e6;min-height:165px;
    ">
    <div style="font-size:17px;margin-bottom:5px">
        {EMOJI.get(kat,'🍴')} <b>{kat.title()}</b>
    </div>
    <b style="font-size:13px">{bahan['nama_bahan']}</b><br>
    <span style="color:#555;font-size:12px;line-height:1.7">
        Kategori: {bahan['kategori']}<br>
        Porsi: <b>{format_angka_id(porsi, DESIMAL_PORSI)} g</b><br>
        Energi: {format_angka_id(e_item, DESIMAL_NUTRISI)} kkal &nbsp;|&nbsp; Protein: {format_angka_id(p_item, DESIMAL_NUTRISI)} g<br>
        Harga/porsi: <b>{format_rupiah(biaya_item)}</b>
    </span>
    </div>"""


def _hitung_deviasi(menu, target_makan):
    vals = [
        abs(menu['nutrisi'][n] - target_makan[n]) / target_makan[n]
        for n in NUTRISI_COLS if target_makan.get(n, 0) > 0
    ]
    return (sum(vals) / len(vals)) * 100 if vals else 0.0


# ─────────────────────────────────────────────────────────────
# TAB 1 — Pareto Front (fitur lama)
# ─────────────────────────────────────────────────────────────
with tab_pareto:
    if st.session_state['tiga_menu'] is None and st.session_state['rencana_minggu'] is None:
        st.info("Jalankan **Optimasi 1 Hari** terlebih dahulu untuk melihat Pareto Front.")
    else:
        # Ambil data dari optimasi 1 hari jika ada, else dari hari pertama rencana mingguan
        if st.session_state['tiga_menu'] is not None:
            tiga_menu    = st.session_state['tiga_menu']
            sol1_list    = st.session_state['sol1_list']
            F1           = st.session_state['F1']
            ht           = st.session_state['harga_target']
            kel_umur_s   = st.session_state.get('kelompok_umur', kelompok_umur)
            evaluasi_pareto = st.session_state.get('evaluasi_pareto')
        else:
            hari0        = st.session_state['rencana_minggu'][0]
            tiga_menu    = hari0['tiga_menu']
            sol1_list    = hari0['sol_list']
            F1           = hari0['F']
            ht           = st.session_state['harga_target']
            kel_umur_s   = st.session_state.get('kelompok_umur', kelompok_umur)
            evaluasi_pareto = hari0.get('evaluasi_pareto')

        st.subheader("Pareto Front: Trade-off Deviasi Gizi vs Selisih Harga")
        st.caption(
            "Grafik ini menampilkan solusi Pareto optimal dari **Run 1** (Menu Bergizi). "
            "Run 2 dan Run 3 menggunakan blacklist bahan sehingga memiliki Pareto front sendiri."
        )

        fig, ax = plt.subplots(figsize=(9, 5))
        sc = ax.scatter(
            F1[:, 1], F1[:, 0] * 100,
            c=F1[:, 0] * 100, cmap='RdYlGn_r',
            s=55, alpha=0.65, edgecolors='white', linewidth=0.4, zorder=2
        )
        plt.colorbar(sc, ax=ax, label='Deviasi gizi dari target makan siang (%)')

        menu_bergizi = next((m for m in tiga_menu if m['_label'] == 'bergizi'), None)
        if menu_bergizi:
            for i, s in enumerate(sol1_list):
                if s['bahan_fingerprint'] == menu_bergizi['bahan_fingerprint']:
                    ax.scatter(F1[i, 1], F1[i, 0] * 100,
                               marker='*', s=280, color='#e63946', zorder=5,
                               edgecolors='black', linewidth=0.8,
                               label='🥇 Menu Paling Bergizi')
                    break

        ax.axvline(0, color='green', linestyle='--', linewidth=1.5,
                   alpha=0.8, label=f'Target tepat (Rp {ht:,})')
        ax.set_xlabel(f"|Biaya Aktual − Target Rp {ht:,}| (Rp)", fontsize=11)
        ax.set_ylabel("Deviasi Gizi dari Target Makan Siang (%)", fontsize=11)
        ax.set_title(
            f"Pareto Front NSGA-II — {kel_umur_s} | Target Harga Rp {ht:,}",
            fontsize=12, fontweight='bold'
        )
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.1f}%'))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'Rp {x:,.0f}'))
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(True, alpha=0.25)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close()

        # Evaluasi kualitas Pareto front menggunakan Hypervolume
        st.divider()
        st.subheader("Evaluasi Pareto Front dengan Hypervolume")
        st.caption(
            "Hypervolume menilai kualitas kumpulan solusi pada setiap run. "
            "Nilai yang lebih besar menunjukkan cakupan solusi yang lebih baik "
            "setelah deviasi gizi dan selisih harga dinormalisasi."
        )

        if evaluasi_pareto:
            opsi_run = {
                "Run 1 — Menu Paling Bergizi": "run_1_bergizi",
                "Run 2 — Menu Paling Tepat Harga": "run_2_harga",
                "Run 3 — Menu Seimbang": "run_3_seimbang",
            }
            nama_run = st.selectbox(
                "Pilih proses optimasi",
                options=list(opsi_run.keys()),
                key="pilih_run_hypervolume",
            )
            data_hv = evaluasi_pareto.get(opsi_run[nama_run], {})
            hv_history = data_hv.get("history", [])

            if hv_history:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Hypervolume Awal", f"{data_hv['nilai_awal']:.4f}")
                c2.metric("Hypervolume Akhir", f"{data_hv['nilai_akhir']:.4f}")
                c3.metric("Perubahan", f"{data_hv['peningkatan']:+.4f}")
                c4.metric("Generasi Terekam", f"{len(hv_history)}")

                generasi_hv = [item["generasi"] for item in hv_history]
                nilai_hv = [item["hypervolume"] for item in hv_history]

                fig_hv, ax_hv = plt.subplots(figsize=(9, 4))
                ax_hv.plot(generasi_hv, nilai_hv, linewidth=2)
                ax_hv.scatter(
                    [generasi_hv[-1]], [nilai_hv[-1]],
                    s=65, zorder=3, label="Nilai akhir"
                )
                ax_hv.set_xlabel("Generasi")
                ax_hv.set_ylabel("Hypervolume")
                ax_hv.set_title(f"Perkembangan Hypervolume — {nama_run}")
                ax_hv.grid(True, alpha=0.25)
                ax_hv.legend(fontsize=8)
                plt.tight_layout()
                st.pyplot(fig_hv)
                plt.close(fig_hv)

                with st.expander("Lihat data Hypervolume per generasi"):
                    df_hv = pd.DataFrame(hv_history).rename(columns={
                        "generasi": "Generasi",
                        "jumlah_evaluasi": "Jumlah Evaluasi",
                        "hypervolume": "Hypervolume",
                        "jumlah_solusi": "Jumlah Solusi Pareto",
                    })
                    st.dataframe(
                        df_hv, use_container_width=True, hide_index=True
                    )
            else:
                st.warning("Riwayat Hypervolume belum tersedia untuk run ini.")
        else:
            st.info(
                "Jalankan optimasi kembali untuk menghasilkan evaluasi Hypervolume."
            )

# ─────────────────────────────────────────────────────────────
# TAB 2 — 3 Rekomendasi Menu (fitur lama)
# ─────────────────────────────────────────────────────────────
with tab_menu:
    if st.session_state['tiga_menu'] is None:
        st.info("Jalankan **Optimasi 1 Hari** untuk melihat rekomendasi menu harian.")
    else:
        tiga_menu    = st.session_state['tiga_menu']
        target_makan = st.session_state['target_makan']
        ht           = st.session_state['harga_target']

        st.subheader("🍽️ 3 Rekomendasi Menu (4 Sehat)")
        st.info(
            f"🎯 Target harga: **Rp {ht:,}** per porsi  "
            f"&nbsp;|&nbsp; 👨‍🎓 Jumlah siswa: **{jumlah_siswa:,} orang**"
        )

        if len(tiga_menu) == 3:
            st.caption("Setiap menu menggunakan bahan yang **berbeda** karena dioptimasi secara terpisah:")
            cols_sum = st.columns(3)
            for col, menu in zip(cols_sum, tiga_menu):
                meta      = META.get(menu['_label'], {})
                bahan_str = " · ".join(
                    info['bahan']['nama_bahan'] for info in menu['menu'].values()
                )
                col.markdown(f"**{meta.get('label','')}**  \n{bahan_str}")

        st.divider()

        for no, menu in enumerate(tiga_menu):
            meta    = META.get(menu['_label'], {'label': menu['_label'], 'deskripsi': ''})
            b1      = menu['biaya']
            b_total = b1 * jumlah_siswa
            selisih = menu['selisih_harga']
            deviasi = _hitung_deviasi(menu, target_makan)

            if selisih <= 200:
                selisih_label = f"<span style='color:#1a7a1a;font-weight:bold'>{format_rupiah(b1)} (selisih {format_rupiah(selisih)} ✅)</span>"
            elif selisih <= 1000:
                selisih_label = f"<span style='color:#856404;font-weight:bold'>{format_rupiah(b1)} (selisih {format_rupiah(selisih)})</span>"
            else:
                selisih_label = f"<span style='color:#721c24;font-weight:bold'>{format_rupiah(b1)} (selisih {format_rupiah(selisih)})</span>"

            st.markdown(f"### {meta['label']}")
            st.caption(meta['deskripsi'])

            cols_bahan = st.columns(len(menu['menu']))
            for col, (kat, info) in zip(cols_bahan, menu['menu'].items()):
                with col:
                    st.markdown(_render_kartu_bahan(kat, info), unsafe_allow_html=True)

            st.markdown(
                f"""<div style="margin:10px 0 8px 0;padding:14px 20px;
                background:#f0faf0;border-radius:10px;border-left:5px solid #28a745;">
                <table style="width:100%;border-collapse:collapse"><tr>
                  <td style="padding:4px 16px 4px 0">
                    📊 Deviasi gizi: <b>{format_angka_id(deviasi, DESIMAL_PERSEN)}%</b>
                  </td>
                  <td style="padding:4px 16px">
                    🍱 Harga <b>1 porsi</b>: {selisih_label}
                  </td>
                  <td style="padding:4px 0">
                    👨‍🎓 Total <b>{jumlah_siswa:,} siswa</b>:
                    <b style="font-size:15px;color:#1a7a1a">{format_rupiah(b_total)}</b>
                  </td>
                </tr></table></div>""",
                unsafe_allow_html=True
            )

            if no < 2:
                st.divider()

        # Tabel perbandingan
        st.subheader("📋 Perbandingan Ringkas 3 Menu")
        rows_cmp = []
        for menu in tiga_menu:
            meta    = META.get(menu['_label'], {})
            selisih = menu['selisih_harga']
            deviasi = _hitung_deviasi(menu, target_makan)
            rows_cmp.append({
                'Menu'                            : meta.get('label', menu['_label']),
                'Deviasi Gizi'                    : f"{format_angka_id(deviasi, DESIMAL_PERSEN)}%",
                'Harga 1 Porsi'                   : format_rupiah(menu['biaya']),
                f'Selisih dari Target Rp {ht:,}'  : format_rupiah(selisih),
                f'Total {jumlah_siswa:,} Siswa'   : format_rupiah(menu['biaya'] * jumlah_siswa),
                **{kat.title(): f"{info['bahan']['nama_bahan']} ({format_angka_id(info['porsi'], DESIMAL_PORSI)} g)"
                   for kat, info in menu['menu'].items()},
            })
        st.dataframe(pd.DataFrame(rows_cmp), use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 3 — Analisis Gizi (fitur lama)
# ─────────────────────────────────────────────────────────────
with tab_gizi:
    if st.session_state['tiga_menu'] is None:
        st.info("Jalankan **Optimasi 1 Hari** untuk melihat analisis gizi.")
    else:
        tiga_menu    = st.session_state['tiga_menu']
        target_makan = st.session_state['target_makan']

        st.subheader("📊 Analisis Gizi 3 Menu vs Target Makan Siang")
        st.caption(
            f"Target = {FAKTOR_PCT}% dari AKG harian (5 nutrisi). "
            "✅ 80–120% = baik | ⚠️ < 80% = kurang | 🔴 > 120% = berlebih"
        )

        label_options = [META.get(m['_label'], {}).get('label', m['_label']) for m in tiga_menu]
        menu_pilih    = st.radio(
            "Tampilkan detail untuk:", options=label_options, horizontal=True
        )
        idx_sel       = label_options.index(menu_pilih)
        menu_sel      = tiga_menu[idx_sel]
        nutrisi_hasil = menu_sel['nutrisi']

        rows_n = []
        for n in NUTRISI_COLS:
            t_hari  = float(akg[n])
            t_siang = target_makan[n]
            aktual  = nutrisi_hasil[n]
            pct     = (aktual / t_siang * 100) if t_siang > 0 else 0
            rows_n.append({
                'Nutrisi'                                 : n.title(),
                'Satuan'                                  : SATUAN.get(n, ''),
                'Hasil Menu'                              : format_angka_id(aktual, DESIMAL_NUTRISI),
                f'Target Makan Siang ({FAKTOR_PCT}% AKG)' : format_angka_id(t_siang, DESIMAL_NUTRISI),
                'AKG Harian'                              : format_angka_id(t_hari, DESIMAL_NUTRISI),
                'Pemenuhan (%)'                           : format_angka_id(pct, DESIMAL_PERSEN),
                'Status'                                  : '✅' if 80 <= pct <= 120
                                                            else ('⚠️' if pct < 80 else '🔴'),
            })
        st.dataframe(pd.DataFrame(rows_n), use_container_width=True, hide_index=True)

        fig2, ax2 = plt.subplots(figsize=(10, 4))
        x          = np.arange(len(NUTRISI_COLS))
        width      = 0.22
        colors_bar = ['#e63946', '#2a9d8f', '#f4a261']

        for i_bar, menu in enumerate(tiga_menu):
            meta_m = META.get(menu['_label'], {})
            vals   = [
                (menu['nutrisi'][n] / target_makan[n] * 100)
                if target_makan.get(n, 0) > 0 else 0
                for n in NUTRISI_COLS
            ]
            ax2.bar(x + (i_bar - 1) * width, vals, width,
                    label=meta_m.get('label', menu['_label']),
                    color=colors_bar[i_bar], alpha=0.82)

        ax2.fill_between([-0.5, len(NUTRISI_COLS) - 0.5], 80, 120,
                         color='green', alpha=0.07, label='Zona baik (80–120%)')
        ax2.axhline(100, color='red',    linestyle='--', linewidth=1.2, label='100% target')
        ax2.axhline(80,  color='orange', linestyle=':',  linewidth=0.9)
        ax2.axhline(120, color='orange', linestyle=':',  linewidth=0.9)
        ax2.set_xticks(x)
        ax2.set_xticklabels([n.title() for n in NUTRISI_COLS])
        ax2.set_ylabel("Pemenuhan (% target makan siang)")
        ax2.set_title("Perbandingan Pemenuhan Gizi — 3 Menu (5 Nutrisi)")
        ax2.legend(fontsize=8, loc='upper right')
        ax2.grid(True, alpha=0.25, axis='y')
        plt.tight_layout()
        st.pyplot(fig2)
        plt.close()

# ─────────────────────────────────────────────────────────────
# TAB 4 — Semua Solusi (fitur lama)
# ─────────────────────────────────────────────────────────────
with tab_semua:
    if st.session_state['sol1_list'] is None:
        st.info("Jalankan **Optimasi 1 Hari** untuk melihat semua solusi Pareto.")
    else:
        sol1_list    = st.session_state['sol1_list']
        F1           = st.session_state['F1']
        target_makan = st.session_state['target_makan']
        ht           = st.session_state['harga_target']
        kel_umur_s   = st.session_state.get('kelompok_umur', kelompok_umur)

        st.subheader("📋 Semua Solusi di Pareto Front (Run 1)")
        st.caption(
            f"Total **{len(sol1_list)} solusi** Pareto optimal dari Run 1. "
            f"Target harga: **Rp {ht:,}** per porsi."
        )

        FILTER_OPTIONS = {
            "🥗 Deviasi gizi paling kecil"           : "gizi",
            f"💰 Harga paling mendekati Rp {ht:,}"   : "harga",
            "⚖️ Keseimbangan optimal (gizi + harga)" : "seimbang",
        }
        filter_label = st.radio(
            "**Urutkan solusi berdasarkan:**",
            options=list(FILTER_OPTIONS.keys()),
            horizontal=True,
        )
        filter_mode  = FILTER_OPTIONS[filter_label]

        MODE_INFO = {
            "gizi"    : "Diurutkan dari **deviasi gizi terkecil**.",
            "harga"   : f"Diurutkan dari **selisih harga terkecil** terhadap target Rp {ht:,}.",
            "seimbang": "Diurutkan berdasarkan **skor keseimbangan** normalisasi kedua objektif.",
        }
        st.info(MODE_INFO[filter_mode])

        f1_arr        = F1[:, 0]
        f2_arr        = np.array([s['selisih_harga'] for s in sol1_list])
        f1_n          = (f1_arr - f1_arr.min()) / (f1_arr.max() - f1_arr.min() + 1e-9)
        f2_n          = (f2_arr - f2_arr.min()) / (f2_arr.max() - f2_arr.min() + 1e-9)
        skor_seimbang = f1_n + f2_n

        rows_all = []
        for i, s in enumerate(sol1_list):
            row = {
                '_sort_gizi'    : f1_arr[i],
                '_sort_harga'   : f2_arr[i],
                '_sort_seimbang': skor_seimbang[i],
                'Deviasi Gizi (%)'                : round(f1_arr[i] * 100, DESIMAL_PERSEN),
                'Harga 1 Porsi (Rp)'              : round(s['biaya'], DESIMAL_RUPIAH),
                f'Selisih dari Rp {ht:,} (Rp)'   : round(s['selisih_harga'], DESIMAL_RUPIAH),
                'Skor Keseimbangan'               : round(skor_seimbang[i], 4),
                f'Total {jumlah_siswa} Siswa (Rp)': round(s['biaya'] * jumlah_siswa, DESIMAL_RUPIAH),
            }
            for kat, info in s['menu'].items():
                row[kat.title()] = f"{info['bahan']['nama_bahan']} ({format_angka_id(info['porsi'], DESIMAL_PORSI)} g)"
            rows_all.append(row)

        df_all    = pd.DataFrame(rows_all)
        SORT_COL  = {'gizi': '_sort_gizi', 'harga': '_sort_harga', 'seimbang': '_sort_seimbang'}
        df_sorted = df_all.sort_values(SORT_COL[filter_mode]).reset_index(drop=True)
        df_disp   = df_sorted.drop(columns=['_sort_gizi', '_sort_harga', '_sort_seimbang']).copy()
        df_disp.insert(0, 'Rank', range(1, len(df_disp) + 1))

        BADGE = {
            "gizi"    : "🥇 Solusi terbaik: deviasi gizi paling kecil",
            "harga"   : "🎯 Solusi terbaik: harga paling mendekati target",
            "seimbang": "⚖️ Solusi terbaik: keseimbangan gizi & harga optimal",
        }
        best = df_disp.iloc[0]
        st.markdown(f"#### {BADGE[filter_mode]}")
        col_b1, col_b2, col_b3, col_b4 = st.columns(4)
        col_b1.metric("Deviasi Gizi",    f"{format_angka_id(best['Deviasi Gizi (%)'], DESIMAL_PERSEN)}%")
        col_b2.metric("Harga 1 Porsi",   format_rupiah(best['Harga 1 Porsi (Rp)']))
        col_b3.metric(f"Selisih dari Rp {ht:,}", format_rupiah(best[f'Selisih dari Rp {ht:,} (Rp)']))
        col_b4.metric("Skor Keseimbangan", f"{best['Skor Keseimbangan']:.4f}")

        bahan_cols_names = [k.title() for k in kelompok_aktif.keys()]
        bahan_detail     = " · ".join(
            f"**{k}**: {best[k]}" for k in bahan_cols_names if k in best.index
        )
        st.markdown(
            f"<div style='background:#e8f5e9;border-left:4px solid #28a745;"
            f"padding:10px 16px;border-radius:6px;font-size:13px;'>"
            f"🍱 {bahan_detail}</div>",
            unsafe_allow_html=True
        )
        st.markdown("---")

        def highlight_best(row):
            if row['Rank'] == 1:
                return ['background-color: #d4edda; font-weight: bold'] * len(row)
            return [''] * len(row)

        st.dataframe(df_disp.style.apply(highlight_best, axis=1),
                     use_container_width=True, hide_index=True)

        csv_b  = df_disp.to_csv(index=False).encode('utf-8')
        SUFFIX = {"gizi": "gizi", "harga": "harga", "seimbang": "seimbang"}
        st.download_button(
            f"⬇️ Download Solusi — {filter_label} (CSV)",
            data=csv_b,
            file_name=(
                f"pareto_{kel_umur_s.replace(' ','_')}_"
                f"{jumlah_siswa}siswa_target{ht:.0f}_"
                f"{SUFFIX[filter_mode]}.csv"
            ),
            mime='text/csv',
        )

# ─────────────────────────────────────────────────────────────
# TAB 5 — Rencana 5 Hari (fitur baru)
# ─────────────────────────────────────────────────────────────
with tab_minggu:
    rencana_minggu = st.session_state.get('rencana_minggu')

    if rencana_minggu is None:
        st.info(
            "Klik **📅 Optimasi 5 Hari** di sidebar untuk membuat rencana menu mingguan. "
            "Sistem akan mengoptimasi menu untuk setiap hari dengan variasi bahan otomatis."
        )
    else:
        ht           = st.session_state.get('harga_target', harga_target)
        target_makan = st.session_state.get('target_makan', {})

        st.subheader("📅 Rencana Menu 5 Hari (Senin–Jumat)")
        st.info(
            f"🎯 Target harga: **Rp {ht:,}**/porsi &nbsp;|&nbsp; "
            f"👨‍🎓 **{jumlah_siswa:,} siswa** &nbsp;|&nbsp; "
            f"✅ Pilih **1 menu** per hari untuk melengkapi rencana mingguan."
        )

        # Hitung berapa hari yang sudah dipilih
        pilihan = st.session_state['pilihan_harian']
        n_dipilih = sum(1 for p in pilihan if p is not None)
        st.progress(n_dipilih / HARI_PLANNING, text=f"{n_dipilih}/{HARI_PLANNING} hari sudah dipilih")

        # Iterasi per hari
        for hari_ke, data_hari in enumerate(rencana_minggu):
            nama_hari  = data_hari['hari']
            tiga_menu  = data_hari['tiga_menu']
            pilihan_hari = pilihan[hari_ke]

            with st.expander(
                f"{'✅' if pilihan_hari else '⬜'} **{nama_hari}** "
                f"{'— ' + META.get(pilihan_hari['_label'],{}).get('label','') if pilihan_hari else '— Belum dipilih'}",
                expanded=(pilihan_hari is None)
            ):
                # Tampilkan 3 pilihan menu per hari
                cols3 = st.columns(3)
                for col, menu in zip(cols3, tiga_menu):
                    meta_m  = META.get(menu['_label'], {})
                    b1      = menu['biaya']
                    selisih = menu['selisih_harga']
                    deviasi = _hitung_deviasi(menu, target_makan)

                    # Status harga
                    if selisih <= 200:
                        harga_badge = f"✅ {format_rupiah(b1)}"
                        harga_color = "#1a7a1a"
                    elif selisih <= 1000:
                        harga_badge = f"⚠️ {format_rupiah(b1)}"
                        harga_color = "#856404"
                    else:
                        harga_badge = f"❌ {format_rupiah(b1)}"
                        harga_color = "#721c24"

                    with col:
                        st.markdown(
                            f"""<div style="border:2px solid {meta_m.get('color','#aaa')};
                            border-radius:10px;padding:10px 12px;margin-bottom:8px;
                            background:#fafafa;">
                            <b style="font-size:14px;color:{meta_m.get('color','#333')}">
                                {meta_m.get('label','')}
                            </b><br>
                            <span style="font-size:12px;color:#555">
                                📊 Deviasi gizi: <b>{format_angka_id(deviasi, DESIMAL_PERSEN)}%</b><br>
                                💰 <span style="color:{harga_color}">{harga_badge}</span><br>
                                👨‍🎓 Total: {format_rupiah(b1 * jumlah_siswa)}
                            </span>
                            </div>""",
                            unsafe_allow_html=True
                        )

                        # Tampilkan bahan-bahan
                        for kat, info in menu['menu'].items():
                            st.markdown(
                                f"{EMOJI.get(kat,'')} **{kat.title()}**: "
                                f"{info['bahan']['nama_bahan']} ({format_angka_id(info['porsi'], DESIMAL_PORSI)} g)"
                            )

                        # Tombol pilih
                        is_dipilih = (pilihan_hari is not None and
                                      pilihan_hari.get('bahan_fingerprint') == menu.get('bahan_fingerprint'))
                        btn_label  = "✅ Dipilih" if is_dipilih else "Pilih Menu Ini"
                        btn_type   = "primary" if is_dipilih else "secondary"

                        if st.button(
                            btn_label,
                            key=f"pilih_{hari_ke}_{menu['_label']}",
                            type=btn_type,
                            use_container_width=True,
                        ):
                            new_pilihan = list(st.session_state['pilihan_harian'])
                            new_pilihan[hari_ke] = menu
                            st.session_state['pilihan_harian'] = new_pilihan

                            day_id = menu.get('_db_day_id')
                            menu_id = menu.get('_db_menu_id')
                            if database.enabled and day_id and menu_id:
                                try:
                                    database.mark_menu_selected(day_id, menu_id)
                                    st.session_state['db_last_error'] = None
                                except Exception as exc:
                                    st.session_state['db_last_error'] = str(exc)
                                    st.warning(
                                        f"Pilihan tersimpan di layar, tetapi gagal "
                                        f"diperbarui di Supabase: {exc}"
                                    )
                            st.rerun()

        # Ringkasan pilihan
        st.divider()
        st.subheader("📋 Ringkasan Pilihan Menu Mingguan")

        if n_dipilih == 0:
            st.warning("Belum ada menu yang dipilih. Pilih menu untuk setiap hari di atas.")
        else:
            rows_mgg = []
            total_biaya_minggu = 0.0

            for hari_ke, menu in enumerate(pilihan):
                nama_h = NAMA_HARI[hari_ke] if hari_ke < len(NAMA_HARI) else f'Hari {hari_ke+1}'
                if menu is None:
                    rows_mgg.append({
                        'Hari'    : nama_h,
                        'Menu'    : '— Belum dipilih —',
                        'Deviasi' : '-', 'Harga/Porsi': '-',
                        **{k.title(): '-' for k in KELOMPOK_ORDER}
                    })
                else:
                    meta_m  = META.get(menu['_label'], {})
                    deviasi = _hitung_deviasi(menu, target_makan)
                    total_biaya_minggu += menu['biaya']
                    rows_mgg.append({
                        'Hari'      : nama_h,
                        'Menu'      : meta_m.get('short', menu['_label']),
                        'Deviasi'   : f"{format_angka_id(deviasi, DESIMAL_PERSEN)}%",
                        'Harga/Porsi': format_rupiah(menu['biaya']),
                        **{kat.title(): f"{info['bahan']['nama_bahan']} ({format_angka_id(info['porsi'], DESIMAL_PORSI)} g)"
                           for kat, info in menu['menu'].items()},
                    })

            st.dataframe(pd.DataFrame(rows_mgg), use_container_width=True, hide_index=True)

            col_r1, col_r2, col_r3 = st.columns(3)
            col_r1.metric("Hari Dipilih", f"{n_dipilih}/{HARI_PLANNING}")
            col_r2.metric("Total Biaya (1 siswa)", format_rupiah(total_biaya_minggu))
            col_r3.metric(f"Total ({jumlah_siswa} siswa)", format_rupiah(total_biaya_minggu * jumlah_siswa))

            if n_dipilih == HARI_PLANNING:
                st.success(
                    "🎉 Semua hari sudah dipilih! Buka tab **📊 Evaluasi Mingguan** "
                    "untuk melihat analisis nutrisi dan insight otomatis."
                )

# ─────────────────────────────────────────────────────────────
# TAB 6 — Evaluasi Mingguan (fitur baru)
# ─────────────────────────────────────────────────────────────
with tab_evaluasi:
    pilihan_harian = st.session_state.get('pilihan_harian', [None] * HARI_PLANNING)
    target_makan   = st.session_state.get('target_makan')
    n_dipilih      = sum(1 for p in pilihan_harian if p is not None)

    if target_makan is None or n_dipilih == 0:
        st.info(
            "Jalankan **Optimasi 5 Hari** dan pilih menu di tab **📅 Rencana 5 Hari** "
            "terlebih dahulu untuk melihat evaluasi nutrisi mingguan."
        )
    else:
        st.subheader("📊 Evaluasi Nutrisi Mingguan")
        st.caption(
            f"Berdasarkan **{n_dipilih} hari** menu yang dipilih. "
            f"Target = {FAKTOR_PCT}% AKG × {n_dipilih} hari. "
            "✅ 80–120% = cukup | ⚠️ < 80% = kurang | 🔴 > 120% = berlebih"
        )

        # Hitung evaluasi
        evaluasi = evaluasi_nutrisi_mingguan(
            pilihan_harian, target_makan, n_hari=n_dipilih
        )

        # ── Metrik ringkasan ──────────────────────────────────
        cols_ev = st.columns(5)
        for col, n in zip(cols_ev, NUTRISI_COLS):
            ev   = evaluasi[n]
            pct  = ev['pct']
            icon = '✅' if 80 <= pct <= 120 else ('⚠️' if pct < 80 else '🔴')
            col.metric(
                label=f"{icon} {n.title()}",
                value=f"{format_angka_id(ev['total'], DESIMAL_NUTRISI)} {SATUAN.get(n,'')}",
                delta=f"{format_angka_id(pct, DESIMAL_PERSEN)}% dari target",
                delta_color="normal" if 80 <= pct <= 120 else "inverse",
            )

        st.divider()

        # ── Tabel evaluasi detail ─────────────────────────────
        rows_ev = []
        for n in NUTRISI_COLS:
            ev = evaluasi[n]
            rows_ev.append({
                'Nutrisi'             : n.title(),
                'Satuan'              : SATUAN.get(n, ''),
                'Total Aktual'        : format_angka_id(ev['total'], DESIMAL_NUTRISI),
                f'Target {n_dipilih} Hari': format_angka_id(ev['target_total'], DESIMAL_NUTRISI),
                'Rata-rata/Hari'      : format_angka_id(ev['rata'], DESIMAL_NUTRISI),
                'Target/Hari'         : format_angka_id(ev['target_harian'], DESIMAL_NUTRISI),
                'Pemenuhan (%)'       : format_angka_id(ev['pct'], DESIMAL_PERSEN),
                'Status'              : {'kurang': '⚠️ Kurang', 'berlebih': '🔴 Berlebih',
                                         'cukup': '✅ Cukup'}.get(ev['status'], ev['status']),
            })
        st.dataframe(pd.DataFrame(rows_ev), use_container_width=True, hide_index=True)

        # ── Grafik perbandingan aktual vs target ──────────────
        fig3, axes = plt.subplots(1, 2, figsize=(12, 4))

        # Subplot kiri: bar chart pemenuhan (%)
        ax_l   = axes[0]
        labels = [n.title() for n in NUTRISI_COLS]
        vals   = [evaluasi[n]['pct'] for n in NUTRISI_COLS]
        colors = ['#28a745' if 80 <= v <= 120 else ('#ffc107' if v < 80 else '#dc3545')
                  for v in vals]
        bars   = ax_l.bar(labels, vals, color=colors, alpha=0.85, edgecolor='white')
        ax_l.axhline(100, color='red',    linestyle='--', linewidth=1.5, label='100% target')
        ax_l.axhline(80,  color='orange', linestyle=':',  linewidth=1.0, label='Batas kurang (80%)')
        ax_l.axhline(120, color='orange', linestyle=':',  linewidth=1.0, label='Batas lebih (120%)')
        ax_l.fill_between([-0.5, 4.5], 80, 120, color='green', alpha=0.07)
        for bar, val in zip(bars, vals):
            ax_l.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                      f'{val:.4f}%', ha='center', va='bottom', fontsize=9)
        ax_l.set_ylabel("Pemenuhan (%)")
        ax_l.set_title(f"Pemenuhan Nutrisi {n_dipilih} Hari (%)")
        ax_l.legend(fontsize=7)
        ax_l.grid(True, alpha=0.25, axis='y')

        # Subplot kanan: radar-style — aktual vs target per nutrisi
        ax_r = axes[1]
        x2   = np.arange(len(NUTRISI_COLS))
        w2   = 0.35
        aktual_vals = [evaluasi[n]['total']        for n in NUTRISI_COLS]
        target_vals = [evaluasi[n]['target_total'] for n in NUTRISI_COLS]
        ax_r.bar(x2 - w2/2, aktual_vals, w2, label='Aktual', color='#2196F3', alpha=0.82)
        ax_r.bar(x2 + w2/2, target_vals, w2, label='Target', color='#FF9800', alpha=0.82)
        ax_r.set_xticks(x2)
        ax_r.set_xticklabels(labels)
        ax_r.set_ylabel("Nilai Nutrisi")
        ax_r.set_title(f"Aktual vs Target {n_dipilih} Hari")
        ax_r.legend(fontsize=8)
        ax_r.grid(True, alpha=0.25, axis='y')

        plt.tight_layout()
        st.pyplot(fig3)
        plt.close()

        st.divider()

        # ── Insights otomatis (rule-based AI) ─────────────────
        st.subheader("🧠 Insight & Saran Otomatis")
        st.caption(
            "Sistem secara otomatis menganalisis pola nutrisi minggu ini "
            "dan memberikan rekomendasi berbasis aturan gizi."
        )

        insights = generate_insights(evaluasi, target_makan)

        for ins in insights:
            level = ins['level']
            if level == 'kurang':
                bg    = '#fff3cd'
                border= '#ffc107'
            elif level == 'lebih':
                bg    = '#f8d7da'
                border= '#dc3545'
            else:
                bg    = '#d4edda'
                border= '#28a745'

            st.markdown(
                f"""<div style="background:{bg};border-left:5px solid {border};
                border-radius:8px;padding:12px 16px;margin-bottom:10px;">
                <b style="font-size:14px">{ins['emoji']} {ins['nutrisi']} — {ins['pct']}%</b><br>
                <span style="font-size:13px">{ins['pesan']}</span><br>
                <span style="font-size:12px;color:#555">💡 <i>{ins['saran']}</i></span>
                </div>""",
                unsafe_allow_html=True
            )

        st.divider()

        weekly_run_id = st.session_state.get('db_weekly_run_id')
        if database.enabled and weekly_run_id:
            if st.button(
                "💾 Simpan/Perbarui Evaluasi di Supabase",
                use_container_width=True,
                key="save_weekly_evaluation",
            ):
                try:
                    database.save_weekly_evaluation(
                        run_id=weekly_run_id,
                        selected_days=n_dipilih,
                        evaluation=evaluasi,
                        insights=insights,
                        adapted_akg=st.session_state.get('akg_adaptif'),
                        adaptation_log=st.session_state.get('log_adaptasi') or [],
                    )
                    st.success("Evaluasi mingguan tersimpan di Supabase.")
                except Exception as exc:
                    st.error(f"Evaluasi gagal disimpan ke Supabase: {exc}")
        elif not database.enabled:
            st.caption("Aktifkan Supabase untuk menyimpan evaluasi mingguan.")

        st.divider()

        # ── Adaptasi AKG untuk minggu depan ───────────────────
        st.subheader("🔁 Adaptasi AKG untuk Minggu Depan")

        log_adaptasi = st.session_state.get('log_adaptasi')

        if log_adaptasi is None:
            st.info(
                "Klik tombol **🧠 Adaptasi AKG Minggu Depan** di sidebar untuk "
                "menyesuaikan target nutrisi berdasarkan evaluasi minggu ini. "
                "Sistem akan menaikkan target nutrisi yang kurang dan menurunkan "
                "yang berlebih (±3–7%)."
            )
        else:
            st.success("✅ AKG telah diadaptasi untuk minggu depan.")
            rows_log = []
            for l in log_adaptasi:
                perubahan_str = (
                    f"+{l['perubahan_pct']}% ↑" if l['perubahan_pct'] > 0
                    else (f"{l['perubahan_pct']}% ↓" if l['perubahan_pct'] < 0 else "Tetap")
                )
                rows_log.append({
                    'Nutrisi'       : l['nutrisi'].title(),
                    'Pemenuhan Minggu Ini': f"{l['pemenuhan_pct']}%",
                    'Status'        : {'normal': '✅ Normal', 'kurang': '⚠️ Kurang',
                                       'berlebih': '🔴 Berlebih'}.get(l['status'], l['status']),
                    'AKG Lama'      : f"{l['nilai_lama']} {SATUAN.get(l['nutrisi'].lower(),'')}",
                    'AKG Baru'      : f"{format_angka_id(l['nilai_baru'], DESIMAL_NUTRISI)} {SATUAN.get(l['nutrisi'].lower(),'')}",
                    'Perubahan'     : perubahan_str,
                })
            st.dataframe(pd.DataFrame(rows_log), use_container_width=True, hide_index=True)
            st.caption(
                "Jalankan **Optimasi 5 Hari** kembali untuk menggunakan "
                "target nutrisi yang telah disesuaikan ini."
            )

        # ── Download laporan CSV ───────────────────────────────
        st.divider()
        rows_dl = []
        for hari_ke, menu in enumerate(pilihan_harian):
            nama_h = NAMA_HARI[hari_ke] if hari_ke < len(NAMA_HARI) else f'Hari {hari_ke+1}'
            if menu is None:
                rows_dl.append({'Hari': nama_h, 'Menu': '-', **{n.title(): '-' for n in NUTRISI_COLS}})
            else:
                meta_m = META.get(menu['_label'], {})
                row    = {
                    'Hari'       : nama_h,
                    'Menu'       : meta_m.get('short', menu['_label']),
                    'Biaya (Rp)' : round(menu['biaya'], DESIMAL_RUPIAH),
                }
                for kat, info in menu['menu'].items():
                    row[kat.title()] = f"{info['bahan']['nama_bahan']} ({format_angka_id(info['porsi'], DESIMAL_PORSI)} g)"
                for n in NUTRISI_COLS:
                    row[n.title()] = round(menu['nutrisi'].get(n, 0), DESIMAL_NUTRISI)
                rows_dl.append(row)

        df_dl = pd.DataFrame(rows_dl)
        st.download_button(
            "⬇️ Download Laporan Mingguan (CSV)",
            data=df_dl.to_csv(index=False).encode('utf-8'),
            file_name=f"laporan_mingguan_{kelompok_umur.replace(' ','_')}.csv",
            mime='text/csv',
        )

# ─────────────────────────────────────────────────────────────
# TAB 7 — Riwayat Supabase
# ─────────────────────────────────────────────────────────────
with tab_riwayat:
    st.subheader("🗄️ Riwayat Eksekusi Optimasi")
    st.caption(
        "Setiap penekanan tombol Optimasi 1 Hari atau Optimasi 5 Hari "
        "membuat satu baris baru pada tabel optimasi_run."
    )

    if not database.enabled:
        st.info(
            "Supabase belum aktif. Isi SUPABASE_URL dan SUPABASE_ANON_KEY "
            "di .streamlit/secrets.toml."
        )
    else:
        try:
            recent_runs = database.fetch_recent_runs(limit=50)
            if not recent_runs:
                st.info("Belum ada riwayat optimasi di database.")
            else:
                df_history = pd.DataFrame(recent_runs)
                df_history['created_at'] = pd.to_datetime(
                    df_history['created_at'], errors='coerce'
                ).dt.tz_convert('Asia/Jakarta')
                df_history['created_at'] = df_history['created_at'].dt.strftime(
                    '%d-%m-%Y %H:%M:%S WIB'
                )
                df_history['id'] = df_history['id'].astype(str).str[:8]
                df_history['mode'] = df_history['mode'].map({
                    'daily': '1 Hari', 'weekly': '5 Hari'
                }).fillna(df_history['mode'])
                df_history['harga_target'] = df_history['harga_target'].apply(
                    lambda value: f"Rp {float(value):,.0f}"
                )
                df_history = df_history.rename(columns={
                    'id': 'ID Run',
                    'created_at': 'Waktu',
                    'mode': 'Mode',
                    'kelompok_umur': 'Kelompok Umur',
                    'jumlah_siswa': 'Jumlah Siswa',
                    'harga_target': 'Target Harga',
                    'faktor_makan': 'Faktor Makan',
                    'pop_size': 'Populasi',
                    'n_gen': 'Generasi',
                    'status': 'Status',
                })
                st.dataframe(
                    df_history, use_container_width=True, hide_index=True
                )
        except Exception as exc:
            st.error(f"Riwayat Supabase gagal dimuat: {exc}")

# ─────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "Skripsi: Aplikasi Optimasi Multi-Objektif pada Rekomendasi Menu "
    "Program Makan Bergizi Gratis | NSGA-II via pymoo | "
    "Fitur: Perencanaan Mingguan · Evaluasi Nutrisi · AI Adaptif AKG"
)