import numpy as np
import time
from pymoo.core.problem import Problem
from pymoo.core.callback import Callback
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.optimize import minimize
from pymoo.termination import get_termination
from pymoo.indicators.hv import HV

from src.data_loader import NUTRISI_COLS, hitung_nutrisi_per_porsi


# ─────────────────────────────────────────────────────────────
# Konstanta global
# ─────────────────────────────────────────────────────────────
FAKTOR_MAKAN_SIANG = 0.35

POP_SIZE_FIXED = 200
N_GEN_FIXED    = 300

PORSI_BATAS = {
    'karbohidrat': (100, 200),
    'lauk'       : (50,  100),
    'sayur'      : (50,  100),
    'buah'       : (75,  150),
}

KELOMPOK_ORDER = ['karbohidrat', 'lauk', 'sayur', 'buah']

# Jumlah hari perencanaan mingguan
HARI_PLANNING = 5
NAMA_HARI     = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat']


# ─────────────────────────────────────────────────────────────
# Evaluasi kualitas Pareto front: Hypervolume
# ─────────────────────────────────────────────────────────────
class ParetoHistoryCallback(Callback):
    """Rekam Pareto front dan jumlah evaluasi pada setiap generasi."""

    def __init__(self):
        super().__init__()
        self.data["pareto"] = []
        self.data["n_eval"] = []

    def notify(self, algorithm):
        optimum = getattr(algorithm, "opt", None)
        if optimum is None:
            return

        F = optimum.get("F")
        if F is None:
            return

        F = np.asarray(F, dtype=float)
        if F.size == 0:
            return

        F = np.atleast_2d(F)
        F = F[np.all(np.isfinite(F), axis=1)]
        if len(F) == 0:
            return


        self.data["pareto"].append(F.copy())
        self.data["n_eval"].append(int(algorithm.evaluator.n_eval))


def hitung_hypervolume(pareto_history, n_eval_history=None):
    """
    Hitung Hypervolume setiap generasi setelah F1 dan F2 dinormalisasi.

    Normalisasi menggunakan batas global dari seluruh riwayat dalam satu run,
    sehingga nilai antargenerasi dapat dibandingkan secara konsisten. Karena
    kedua objektif diminimalkan, nilai Hypervolume yang lebih besar lebih baik.
    """
    fronts = []
    evals = []

    for i, F in enumerate(pareto_history or []):
        arr = np.asarray(F, dtype=float)
        if arr.size == 0:
            continue
        arr = np.atleast_2d(arr)
        arr = arr[np.all(np.isfinite(arr), axis=1)]
        if len(arr) == 0:
            continue
        fronts.append(arr)
        evals.append(
            int(n_eval_history[i])
            if n_eval_history is not None and i < len(n_eval_history)
            else None
        )

    kosong = {
        "history": [],
        "nilai_awal": None,
        "nilai_akhir": None,
        "peningkatan": None,
        "ideal": None,
        "nadir": None,
        "reference_point": [1.1, 1.1],
    }
    if not fronts:
        return kosong

    semua_F = np.vstack(fronts)
    ideal = np.min(semua_F, axis=0)
    nadir = np.max(semua_F, axis=0)
    rentang = nadir - ideal
    rentang[rentang < 1e-12] = 1.0

    reference_point = np.array([1.1, 1.1], dtype=float)
    indikator = HV(ref_point=reference_point, norm_ref_point=False)
    history = []

    for generasi, (F, n_eval) in enumerate(zip(fronts, evals), start=1):
        F_normal = (F - ideal) / rentang
        # Batasi efek galat floating point tanpa mengubah urutan solusi.
        F_normal = np.clip(F_normal, 0.0, 1.0)
        nilai = float(indikator(F_normal))
        history.append({
            "generasi": generasi,
            "jumlah_evaluasi": n_eval,
            "hypervolume": nilai,
            "jumlah_solusi": int(len(F)),
        })

    nilai_awal = history[0]["hypervolume"]
    nilai_akhir = history[-1]["hypervolume"]
    return {
        "history": history,
        "nilai_awal": nilai_awal,
        "nilai_akhir": nilai_akhir,
        "peningkatan": nilai_akhir - nilai_awal,
        "ideal": ideal.tolist(),
        "nadir": nadir.tolist(),
        "reference_point": reference_point.tolist(),
    }


# ─────────────────────────────────────────────────────────────
# Problem NSGA-II
# ─────────────────────────────────────────────────────────────
class MenuOptimizationProblem(Problem):
    """
    Problem NSGA-II dengan opsi blacklist bahan per kategori.
    Blacklist digunakan saat run ke-2 dan ke-3 agar bahan
    yang sudah muncul di menu sebelumnya tidak dipilih lagi.
    Blacklist antar-hari juga menggunakan mekanisme yang sama.
    """

    def __init__(self, kelompok_data, akg_target,
                 faktor_makan=FAKTOR_MAKAN_SIANG,
                 harga_target=10_000.0,
                 blacklist=None):
        """
        Parameters
        ----------
        kelompok_data : dict {kategori: DataFrame}
        akg_target    : Series — AKG harian (bisa sudah diadaptasi)
        faktor_makan  : float  — proporsi makan siang (default 35%)
        harga_target  : float  — target harga per porsi (Rp)
        blacklist     : dict {kategori: set(nama_bahan)}
                        Bahan dalam blacklist dikenai penalti besar
                        sehingga hampir tidak mungkin terpilih.
        """
        self.kelompok_data = kelompok_data
        self.faktor_makan  = faktor_makan
        self.nutrisi_cols  = NUTRISI_COLS
        self.harga_target  = float(harga_target)
        self.blacklist     = blacklist or {}

        self.akg_target   = akg_target
        self.target_makan = {
            n: float(akg_target[n]) * faktor_makan
            for n in NUTRISI_COLS
        }

        self.aktif = [k for k in KELOMPOK_ORDER if k in kelompok_data]

        n_var    = len(self.aktif) * 2
        xl, xu   = [], []

        for kat in self.aktif:
            n_item        = len(kelompok_data[kat])
            p_min, p_max  = PORSI_BATAS.get(kat, (50, 200))
            xl += [0, p_min]
            xu += [n_item - 1e-9, p_max]

        super().__init__(
            n_var=n_var,
            n_obj=2,
            n_ieq_constr=0,
            xl=np.array(xl, dtype=float),
            xu=np.array(xu, dtype=float),
        )

    def decode(self, x):
        """Ubah vektor keputusan menjadi dict {kategori: {bahan, porsi, idx}}."""
        hasil = {}
        for i, kat in enumerate(self.aktif):
            n_item = len(self.kelompok_data[kat])
            idx    = int(np.clip(np.round(x[i * 2]), 0, n_item - 1))
            porsi  = float(x[i * 2 + 1])
            bahan  = self.kelompok_data[kat].iloc[idx]
            hasil[kat] = {'bahan': bahan, 'porsi': porsi, 'idx': idx}
        return hasil

    def hitung_total_nutrisi(self, decoded):
        """Jumlahkan nutrisi semua bahan dalam satu menu."""
        total = {n: 0.0 for n in self.nutrisi_cols}
        for info in decoded.values():
            nutrisi = hitung_nutrisi_per_porsi(info['bahan'], info['porsi'])
            for n in self.nutrisi_cols:
                total[n] += nutrisi[n]
        return total

    def hitung_biaya(self, decoded):
        """Hitung total biaya satu porsi menu (Rp)."""
        return sum(
            (info['porsi'] / 100.0) * float(info['bahan']['harga_per_100g'])
            for info in decoded.values()
        )

    def _evaluate(self, X, out, *args, **kwargs):
        f1_list, f2_list = [], []

        for x in X:
            decoded       = self.decode(x)
            total_nutrisi = self.hitung_total_nutrisi(decoded)

            # ── OBJECTIVE 1: deviasi gizi ──────────────────────
            deviasi = [
                abs(total_nutrisi[n] - self.target_makan[n]) / self.target_makan[n]
                for n in self.nutrisi_cols if self.target_makan[n] > 0
            ]
            f1 = float(np.mean(deviasi))

            # ── OBJECTIVE 2: selisih harga ─────────────────────
            biaya = self.hitung_biaya(decoded)
            f2    = abs(biaya - self.harga_target)

            # ── PENALTI BLACKLIST ──────────────────────────────
            # Bahan yang sudah dipakai di run / hari sebelumnya
            # dikenai penalti sangat besar agar hampir tidak mungkin muncul kembali.
            for kat, info in decoded.items():
                nama = info['bahan']['nama_bahan']
                if nama in self.blacklist.get(kat, set()):
                    f1 += 2.0
                    f2 += 8000.0

            f1_list.append(f1)
            f2_list.append(f2)

        out["F"] = np.column_stack([f1_list, f2_list])


# ─────────────────────────────────────────────────────────────
# Helper: satu sesi NSGA-II
# ─────────────────────────────────────────────────────────────
def _run_single(kelompok_data, akg_target, pop_size, n_gen,
                seed, faktor_makan, harga_target, blacklist=None):
    """
    Jalankan satu sesi NSGA-II dan kembalikan solusi + metadata.

    Returns
    -------
    solusi      : list of dict (menu, nutrisi, biaya, ...)
    result.F    : ndarray (n_pareto, 2) — nilai objektif Pareto
    target_makan: dict {nutrisi: nilai target makan siang}
    evaluasi_hv : dict — riwayat dan nilai Hypervolume
    """
    problem = MenuOptimizationProblem(
        kelompok_data, akg_target,
        faktor_makan=faktor_makan,
        harga_target=harga_target,
        blacklist=blacklist,
    )

    algorithm = NSGA2(
        pop_size=pop_size,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=10),
        mutation=PM(prob=0.4, eta=8),
        eliminate_duplicates=True,
    )

    callback = ParetoHistoryCallback()

    result = minimize(
        problem,
        algorithm,
        get_termination("n_gen", n_gen),
        seed=seed,
        callback=callback,
        verbose=False,
    )

    evaluasi_hv = hitung_hypervolume(
        callback.data["pareto"],
        callback.data["n_eval"],
    )

    solusi = []
    if result.X is not None:
        for x in result.X:
            decoded     = problem.decode(x)
            nutrisi     = problem.hitung_total_nutrisi(decoded)
            biaya       = problem.hitung_biaya(decoded)
            fingerprint = "|".join(
                f"{k}:{decoded[k]['bahan']['nama_bahan']}"
                for k in KELOMPOK_ORDER if k in decoded
            )
            solusi.append({
                'menu'             : decoded,
                'nutrisi'          : nutrisi,
                'biaya'            : biaya,
                'selisih_harga'    : abs(biaya - harga_target),
                'bahan_fingerprint': fingerprint,
            })

    return solusi, result.F, problem.target_makan, evaluasi_hv


# ─────────────────────────────────────────────────────────────
# Helper: pilihan dari Pareto front
# ─────────────────────────────────────────────────────────────
def _pilih_terbaik_gizi(solusi_list, pareto_F):
    """Pilih solusi dengan deviasi gizi terkecil."""
    if not solusi_list:
        return None, -1
    idx = int(np.argmin(pareto_F[:, 0]))
    return solusi_list[idx], idx


def _pilih_terbaik_harga(solusi_list, pareto_F):
    """Pilih solusi dengan selisih harga terkecil."""
    if not solusi_list:
        return None, -1
    idx = int(np.argmin(pareto_F[:, 1]))
    return solusi_list[idx], idx


def _pilih_seimbang(solusi_list, pareto_F):
    """Pilih solusi dengan skor keseimbangan normalisasi terkecil."""
    if not solusi_list:
        return None, -1
    f1_n = (pareto_F[:, 0] - pareto_F[:, 0].min()) / (pareto_F[:, 0].max() - pareto_F[:, 0].min() + 1e-9)
    f2_n = (pareto_F[:, 1] - pareto_F[:, 1].min()) / (pareto_F[:, 1].max() - pareto_F[:, 1].min() + 1e-9)
    idx  = int(np.argmin(f1_n + f2_n))
    return solusi_list[idx], idx


# ─────────────────────────────────────────────────────────────
# Fungsi utama: optimasi 1 hari (3-run blacklist bertingkat)
# ─────────────────────────────────────────────────────────────
def run_optimization(kelompok_data, akg_target,
                     pop_size=POP_SIZE_FIXED,
                     n_gen=N_GEN_FIXED,
                     seed=None,
                     faktor_makan=FAKTOR_MAKAN_SIANG,
                     harga_target=10_000.0,
                     blacklist_harian=None):
    """
    Strategi 3-run dengan blacklist bertingkat untuk memastikan
    bahan tiap menu BERBEDA di semua kategori pada hari yang sama.

    Run 1 (tanpa blacklist tambahan) → Menu Paling Bergizi
    Run 2 (blacklist run 1)          → Menu Paling Tepat Harga
    Run 3 (blacklist run 1+2)        → Menu Seimbang

    Parameters
    ----------
    blacklist_harian : dict {kategori: set(nama_bahan)}
        Bahan dari hari-hari sebelumnya yang harus dihindari
        agar ada variasi antar-hari.

    Returns
    -------
    tiga_menu    : list of dict (3 menu terpilih)
    sol1_list    : list of dict (semua solusi Pareto run 1)
    F1           : ndarray — nilai objektif Pareto run 1
    target_makan : dict {nutrisi: nilai target}
    harga_target : float
    evaluasi_pareto : dict — Hypervolume untuk ketiga run
    """
    base_seed      = seed if seed is not None else int(time.time() * 1000) % (2 ** 31)
    bl_dasar       = blacklist_harian or {}

    # ── RUN 1: cari menu paling bergizi ──────────────────────
    sol1_list, F1, target_makan, hv_run1 = _run_single(
        kelompok_data, akg_target, pop_size, n_gen,
        seed=base_seed,
        faktor_makan=faktor_makan,
        harga_target=harga_target,
        blacklist=bl_dasar,
    )
    menu1, _ = _pilih_terbaik_gizi(sol1_list, F1)

    # Bangun blacklist dari bahan run 1 + blacklist harian
    blacklist_r2 = {k: set(v) for k, v in bl_dasar.items()}
    if menu1:
        for kat, info in menu1['menu'].items():
            blacklist_r2.setdefault(kat, set()).add(info['bahan']['nama_bahan'])

    # ── RUN 2: blacklist run 1, cari menu tepat harga ────────
    sol2_list, F2, _, hv_run2 = _run_single(
        kelompok_data, akg_target, pop_size, n_gen,
        seed=base_seed + 1,
        faktor_makan=faktor_makan,
        harga_target=harga_target,
        blacklist=blacklist_r2,
    )
    menu2, _ = _pilih_terbaik_harga(sol2_list, F2)

    # Bangun blacklist dari bahan run 1 + run 2 + blacklist harian
    blacklist_r3 = {k: set(v) for k, v in blacklist_r2.items()}
    if menu2:
        for kat, info in menu2['menu'].items():
            blacklist_r3.setdefault(kat, set()).add(info['bahan']['nama_bahan'])

    # ── RUN 3: blacklist run 1+2, cari menu seimbang ─────────
    sol3_list, F3, _, hv_run3 = _run_single(
        kelompok_data, akg_target, pop_size, n_gen,
        seed=base_seed + 2,
        faktor_makan=faktor_makan,
        harga_target=harga_target,
        blacklist=blacklist_r3,
    )
    menu3, _ = _pilih_seimbang(sol3_list, F3)

    # ── Kumpulkan 3 menu terpilih ─────────────────────────────
    tiga_menu = []
    for menu, label in [(menu1, 'bergizi'), (menu2, 'harga'), (menu3, 'seimbang')]:
        if menu is not None:
            tiga_menu.append({**menu, '_label': label})

    evaluasi_pareto = {
        "run_1_bergizi": hv_run1,
        "run_2_harga": hv_run2,
        "run_3_seimbang": hv_run3,
    }

    return (
        tiga_menu, sol1_list, F1, target_makan,
        harga_target, evaluasi_pareto
    )


# ─────────────────────────────────────────────────────────────
# Fungsi baru: perencanaan 5 hari (weekly planning)
# ─────────────────────────────────────────────────────────────
def run_weekly_optimization(kelompok_data, akg_target,
                             pop_size=POP_SIZE_FIXED,
                             n_gen=N_GEN_FIXED,
                             seed=None,
                             faktor_makan=FAKTOR_MAKAN_SIANG,
                             harga_target=10_000.0,
                             n_hari=HARI_PLANNING,
                             progress_callback=None):
    """
    Jalankan optimasi untuk n_hari hari ke depan.
    Setiap hari menghasilkan 3 menu (bergizi, harga, seimbang).
    Blacklist kumulatif memastikan bahan tidak berulang antar hari.

    Parameters
    ----------
    progress_callback : callable(hari_ke: int, total: int) | None
        Dipanggil setelah tiap hari selesai dioptimasi.
        Berguna untuk update progress bar di Streamlit.

    Returns
    -------
    rencana_minggu : list[dict] — satu entry per hari, berisi:
        {
          'hari'      : str  (nama hari),
          'tiga_menu' : list (3 menu),
          'sol_list'  : list (semua solusi Pareto run 1),
          'F'         : ndarray,
          'target_makan': dict,
          'evaluasi_pareto': dict (Hypervolume ketiga run),
        }
    """
    base_seed      = seed if seed is not None else int(time.time() * 1000) % (2 ** 31)
    rencana_minggu = []
    blacklist_kum  = {}   # akumulasi bahan yg sudah digunakan

    for hari_ke in range(n_hari):
        nama_hari = NAMA_HARI[hari_ke] if hari_ke < len(NAMA_HARI) else f'Hari {hari_ke+1}'

        tiga_menu, sol_list, F, target_makan, ht, evaluasi_pareto = run_optimization(
            kelompok_data, akg_target,
            pop_size=pop_size,
            n_gen=n_gen,
            seed=base_seed + hari_ke * 10,
            faktor_makan=faktor_makan,
            harga_target=harga_target,
            blacklist_harian=blacklist_kum,
        )

        # Perbarui blacklist kumulatif dengan bahan dari menu terpilih hari ini
        # (menggunakan SEMUA bahan dari ketiga menu agar variasi maksimal)
        for menu in tiga_menu:
            for kat, info in menu['menu'].items():
                blacklist_kum.setdefault(kat, set()).add(info['bahan']['nama_bahan'])

        rencana_minggu.append({
            'hari'        : nama_hari,
            'tiga_menu'   : tiga_menu,
            'sol_list'    : sol_list,
            'F'           : F,
            'target_makan': target_makan,
            'evaluasi_pareto': evaluasi_pareto,
        })

        if progress_callback:
            progress_callback(hari_ke + 1, n_hari)

    return rencana_minggu


# ─────────────────────────────────────────────────────────────
# Fungsi AI Adaptif: sesuaikan AKG berdasarkan riwayat minggu
# ─────────────────────────────────────────────────────────────
def adjust_akg_based_on_history(akg_asli, riwayat_pilihan,
                                 faktor_makan=FAKTOR_MAKAN_SIANG,
                                 persen_naik=0.07,
                                 persen_turun=0.03,
                                 ambang_kurang=0.85,
                                 ambang_lebih=1.15):
    """
    Adaptasi target AKG untuk minggu berikutnya berdasarkan
    realisasi nutrisi minggu ini.

    Logika:
    - Jika rata-rata pemenuhan nutrisi N < ambang_kurang  → naikkan AKG[N]
    - Jika rata-rata pemenuhan nutrisi N > ambang_lebih   → turunkan AKG[N]
    - Jika dalam rentang normal                           → tetap

    Parameters
    ----------
    akg_asli         : pandas Series — AKG harian asli
    riwayat_pilihan  : list[dict]   — menu yang dipilih user tiap hari
                       (elemen dari session_state['pilihan_harian'])
    faktor_makan     : float
    persen_naik      : float — kenaikan AKG jika kurang (default 7%)
    persen_turun     : float — penurunan AKG jika lebih  (default 3%)
    ambang_kurang    : float — batas bawah pemenuhan (default 85%)
    ambang_lebih     : float — batas atas pemenuhan  (default 115%)

    Returns
    -------
    akg_baru    : pandas Series — AKG yang sudah diadaptasi
    log_adaptasi: list[dict]   — catatan perubahan tiap nutrisi
    """
    import pandas as pd

    # Hitung target makan siang dari AKG asli
    target_siang = {n: float(akg_asli[n]) * faktor_makan for n in NUTRISI_COLS}

    # Akumulasi total nutrisi dari pilihan harian
    total_realisasi = {n: 0.0 for n in NUTRISI_COLS}
    n_hari_valid    = 0

    for pilihan in riwayat_pilihan:
        if pilihan is None:
            continue
        for n in NUTRISI_COLS:
            total_realisasi[n] += float(pilihan['nutrisi'].get(n, 0))
        n_hari_valid += 1

    if n_hari_valid == 0:
        # Tidak ada data → kembalikan AKG asli tanpa perubahan
        return akg_asli.copy(), []

    # Rata-rata harian
    rata_realisasi = {n: total_realisasi[n] / n_hari_valid for n in NUTRISI_COLS}

    # Buat salinan AKG untuk diadaptasi
    akg_baru     = akg_asli.copy()
    log_adaptasi = []

    for n in NUTRISI_COLS:
        t_siang = target_siang[n]
        if t_siang <= 0:
            continue

        pemenuhan = rata_realisasi[n] / t_siang   # rasio aktual / target

        if pemenuhan < ambang_kurang:
            # Kurang → naikkan AKG
            faktor_adj = 1.0 + persen_naik
            status     = 'kurang'
        elif pemenuhan > ambang_lebih:
            # Lebih → turunkan AKG sedikit
            faktor_adj = 1.0 - persen_turun
            status     = 'berlebih'
        else:
            faktor_adj = 1.0
            status     = 'normal'

        nilai_lama    = float(akg_asli[n])
        nilai_baru    = nilai_lama * faktor_adj
        akg_baru[n]   = nilai_baru

        log_adaptasi.append({
            'nutrisi'      : n,
            'nilai_lama'   : round(nilai_lama, 2),
            'nilai_baru'   : round(nilai_baru, 2),
            'pemenuhan_pct': round(pemenuhan * 100, 1),
            'status'       : status,
            'perubahan_pct': round((faktor_adj - 1) * 100, 1),
        })

    return akg_baru, log_adaptasi


# ─────────────────────────────────────────────────────────────
# Fungsi AI Adaptif: insight rule-based otomatis
# ─────────────────────────────────────────────────────────────
def generate_insights(evaluasi_mingguan, target_makan_harian):
    """
    Hasilkan insight/saran gizi otomatis berbasis rule-based
    berdasarkan hasil evaluasi nutrisi mingguan.

    Parameters
    ----------
    evaluasi_mingguan   : dict {nutrisi: {'total', 'rata', 'target_total', 'pct'}}
    target_makan_harian : dict {nutrisi: float}

    Returns
    -------
    insights : list[dict] — setiap dict berisi {level, nutrisi, pesan, saran}
        level: 'kurang' | 'lebih' | 'baik'
    """
    insights = []

    # Threshold: < 80% = kurang, > 120% = berlebih
    BATAS_KURANG = 80.0
    BATAS_LEBIH  = 120.0

    # Template saran per nutrisi dan kondisi
    SARAN = {
        'energi': {
            'kurang': (
                "Asupan energi masih kurang dari target minggu ini.",
                "Pertimbangkan menambah porsi nasi/sumber karbohidrat atau "
                "memilih bahan dengan densitas energi lebih tinggi."
            ),
            'lebih': (
                "Asupan energi melebihi target minggu ini.",
                "Kurangi porsi karbohidrat atau pilih bahan rendah kalori "
                "seperti sayur dan buah segar."
            ),
        },
        'protein': {
            'kurang': (
                "Asupan protein kurang dari kebutuhan minggu ini.",
                "Tambahkan lauk tinggi protein seperti telur, tahu, tempe, "
                "ikan, atau daging pada hari berikutnya."
            ),
            'lebih': (
                "Asupan protein melebihi target minggu ini.",
                "Variasikan lauk dengan bahan yang lebih rendah protein "
                "seperti tahu atau sayuran bertepung."
            ),
        },
        'lemak': {
            'kurang': (
                "Asupan lemak masih di bawah kebutuhan.",
                "Pilih lauk yang mengandung lemak sehat seperti ikan, "
                "kacang-kacangan, atau gunakan sedikit minyak saat memasak."
            ),
            'lebih': (
                "Asupan lemak berlebih minggu ini.",
                "Kurangi gorengan dan pilih metode masak rebus/kukus. "
                "Batasi lauk berlemak tinggi."
            ),
        },
        'karbohidrat': {
            'kurang': (
                "Asupan karbohidrat belum mencukupi target.",
                "Naikkan porsi nasi, ubi, atau sumber karbohidrat lain "
                "agar energi harian terpenuhi."
            ),
            'lebih': (
                "Asupan karbohidrat melebihi target.",
                "Kurangi porsi nasi atau ganti sebagian dengan sumber serat "
                "seperti ubi atau jagung dalam porsi lebih kecil."
            ),
        },
        'serat': {
            'kurang': (
                "Asupan serat masih rendah minggu ini.",
                "Perbanyak sayur dan buah segar. Pilih sayuran hijau gelap "
                "seperti bayam, brokoli, atau kacang panjang."
            ),
            'lebih': (
                "Asupan serat sedikit berlebih.",
                "Konsumsi serat yang berlebihan umumnya tidak berbahaya, "
                "namun pastikan asupan air minum cukup."
            ),
        },
    }

    for n in NUTRISI_COLS:
        if n not in evaluasi_mingguan:
            continue

        data = evaluasi_mingguan[n]
        pct  = data.get('pct', 100.0)

        if pct < BATAS_KURANG:
            level  = 'kurang'
            emoji  = '⚠️'
        elif pct > BATAS_LEBIH:
            level  = 'lebih'
            emoji  = '🔴'
        else:
            level  = 'baik'
            emoji  = '✅'

        if level in ('kurang', 'lebih'):
            template = SARAN.get(n, {}).get(level, ("", ""))
            insights.append({
                'level'  : level,
                'nutrisi': n.title(),
                'emoji'  : emoji,
                'pesan'  : template[0],
                'saran'  : template[1],
                'pct'    : round(pct, 1),
            })
        else:
            insights.append({
                'level'  : 'baik',
                'nutrisi': n.title(),
                'emoji'  : emoji,
                'pesan'  : f"{n.title()} terpenuhi dengan baik ({pct:.1f}% dari target).",
                'saran'  : "Pertahankan kombinasi bahan ini untuk minggu berikutnya.",
                'pct'    : round(pct, 1),
            })

    # Tambahkan insight umum jika semua baik
    semua_baik = all(i['level'] == 'baik' for i in insights)
    if semua_baik:
        insights.append({
            'level'  : 'baik',
            'nutrisi': 'Keseluruhan',
            'emoji'  : '🌟',
            'pesan'  : "Semua nutrisi terpenuhi dalam rentang ideal minggu ini!",
            'saran'  : "Lanjutkan pola menu yang serupa untuk minggu berikutnya.",
            'pct'    : 100.0,
        })

    return insights


# ─────────────────────────────────────────────────────────────
# Fungsi evaluasi mingguan
# ─────────────────────────────────────────────────────────────
def evaluasi_nutrisi_mingguan(pilihan_harian, target_makan_harian, n_hari=HARI_PLANNING):
    """
    Hitung total & rata-rata nutrisi dari pilihan menu harian
    dan bandingkan dengan target 5 hari.

    Parameters
    ----------
    pilihan_harian      : list[dict|None] — menu yang dipilih user per hari
    target_makan_harian : dict {nutrisi: float} — target 1 hari
    n_hari              : int

    Returns
    -------
    evaluasi : dict {nutrisi: {total, rata, target_total, target_harian, pct, status}}
    """
    target_total = {n: target_makan_harian[n] * n_hari for n in NUTRISI_COLS}
    total_aktual = {n: 0.0 for n in NUTRISI_COLS}

    n_hari_valid = 0
    for menu in pilihan_harian:
        if menu is None:
            continue
        for n in NUTRISI_COLS:
            total_aktual[n] += float(menu['nutrisi'].get(n, 0))
        n_hari_valid += 1

    evaluasi = {}
    for n in NUTRISI_COLS:
        t_total = target_total[n]
        aktual  = total_aktual[n]
        pct     = (aktual / t_total * 100) if t_total > 0 else 0.0

        if pct < 80:
            status = 'kurang'
        elif pct > 120:
            status = 'berlebih'
        else:
            status = 'cukup'

        evaluasi[n] = {
            'total'         : round(aktual, 1),
            'rata'          : round(aktual / n_hari_valid, 1) if n_hari_valid > 0 else 0.0,
            'target_total'  : round(t_total, 1),
            'target_harian' : round(target_makan_harian[n], 1),
            'pct'           : round(pct, 1),
            'status'        : status,
        }

    return evaluasi
