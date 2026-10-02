"""Lapisan akses database Supabase untuk aplikasi optimasi menu MBG."""

from __future__ import annotations

import math
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

try:
    from supabase import Client, create_client
except ImportError:  # Aplikasi tetap dapat berjalan dengan CSV jika paket belum dipasang.
    Client = Any  # type: ignore
    create_client = None  # type: ignore


def _read_secret(name: str) -> Optional[str]:
    """Baca konfigurasi dari environment atau Streamlit secrets."""
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        if name in st.secrets:
            secret_value = st.secrets[name]
            return str(secret_value) if secret_value else None
    except Exception:
        pass

    return None


def json_safe(value: Any) -> Any:
    """Ubah objek pandas/numpy/Decimal menjadi tipe JSON standar."""
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass

    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict())
        except Exception:
            pass

    if hasattr(value, "tolist"):
        try:
            return json_safe(value.tolist())
        except Exception:
            pass

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]

    return str(value)


def _calculate_nutritional_deviation(menu: Dict[str, Any], target: Dict[str, float]) -> float:
    deviations: List[float] = []
    nutrition = menu.get("nutrisi", {})
    for nutrient, target_value in target.items():
        target_float = float(target_value)
        if target_float > 0:
            actual = float(nutrition.get(nutrient, 0.0))
            deviations.append(abs(actual - target_float) / target_float)
    return float(sum(deviations) / len(deviations) * 100.0) if deviations else 0.0


class SupabaseRepository:
    """Repository sederhana untuk membaca master data dan menyimpan hasil optimasi."""

    def __init__(self, url: Optional[str], anon_key: Optional[str]):
        self.url = url
        self.anon_key = anon_key
        self.client: Optional[Client] = None
        self.configuration_error: Optional[str] = None

        if not url or not anon_key:
            self.configuration_error = (
                "SUPABASE_URL atau SUPABASE_ANON_KEY belum dikonfigurasi."
            )
            return

        if create_client is None:
            self.configuration_error = (
                "Paket 'supabase' belum terpasang. Jalankan pip install -r requirements.txt."
            )
            return

        try:
            self.client = create_client(url, anon_key)
        except Exception as exc:
            self.configuration_error = f"Klien Supabase gagal dibuat: {exc}"

    @classmethod
    def from_config(cls) -> "SupabaseRepository":
        return cls(
            url=_read_secret("SUPABASE_URL"),
            anon_key=_read_secret("SUPABASE_ANON_KEY"),
        )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _require_client(self) -> Client:
        if self.client is None:
            raise RuntimeError(self.configuration_error or "Supabase belum aktif.")
        return self.client

    @staticmethod
    def _first(response: Any, table_name: str) -> Dict[str, Any]:
        data = getattr(response, "data", None) or []
        if not data:
            raise RuntimeError(f"Insert ke tabel '{table_name}' tidak mengembalikan data.")
        return data[0]

    def health_check(self) -> bool:
        client = self._require_client()
        client.table("akg").select("kelompok_umur").limit(1).execute()
        return True

    # ------------------------------------------------------------------
    # Master data
    # ------------------------------------------------------------------
    def fetch_bahan_mentah(self) -> List[Dict[str, Any]]:
        response = (
            self._require_client()
            .table("bahan_mentah")
            .select("*")
            .eq("aktif", True)
            .order("kode_bahan")
            .execute()
        )
        return list(response.data or [])

    def fetch_akg(self) -> List[Dict[str, Any]]:
        response = (
            self._require_client()
            .table("akg")
            .select("*")
            .order("urutan")
            .execute()
        )
        return list(response.data or [])

    # ------------------------------------------------------------------
    # Penyimpanan hasil optimasi
    # ------------------------------------------------------------------
    def _insert_run(
        self,
        *,
        session_id: str,
        mode: str,
        kelompok_umur: str,
        jumlah_siswa: int,
        harga_target: float,
        faktor_makan: float,
        pop_size: int,
        n_gen: int,
        akg_digunakan: Any,
    ) -> str:
        payload = {
            "session_id": session_id,
            "mode": mode,
            "kelompok_umur": kelompok_umur,
            "jumlah_siswa": int(jumlah_siswa),
            "harga_target": float(harga_target),
            "faktor_makan": float(faktor_makan),
            "pop_size": int(pop_size),
            "n_gen": int(n_gen),
            "akg_digunakan": json_safe(akg_digunakan),
            "status": "selesai",
        }
        response = self._require_client().table("optimasi_run").insert(payload).execute()
        return str(self._first(response, "optimasi_run")["id"])

    def _insert_day(
        self,
        *,
        run_id: str,
        day_index: int,
        day_name: str,
        target_makan: Dict[str, float],
        pareto_front: Any,
    ) -> str:
        payload = {
            "run_id": run_id,
            "hari_ke": int(day_index),
            "nama_hari": day_name,
            "target_makan": json_safe(target_makan),
            "pareto_front": json_safe(pareto_front),
        }
        response = self._require_client().table("optimasi_hari").insert(payload).execute()
        return str(self._first(response, "optimasi_hari")["id"])

    def _insert_menu(
        self,
        *,
        day_id: str,
        menu: Dict[str, Any],
        target_makan: Dict[str, float],
    ) -> str:
        payload = {
            "hari_id": day_id,
            "label": str(menu.get("_label", "seimbang")),
            "biaya": float(menu.get("biaya", 0.0)),
            "selisih_harga": float(menu.get("selisih_harga", 0.0)),
            "deviasi_gizi": _calculate_nutritional_deviation(menu, target_makan),
            "nutrisi": json_safe(menu.get("nutrisi", {})),
            "fingerprint": str(menu.get("bahan_fingerprint", "")),
            "terpilih": False,
        }
        response = self._require_client().table("optimasi_menu").insert(payload).execute()
        menu_id = str(self._first(response, "optimasi_menu")["id"])

        item_rows = []
        for kelompok, info in menu.get("menu", {}).items():
            bahan = info["bahan"]
            porsi = float(info["porsi"])
            faktor = porsi / 100.0
            nutrisi_item = {
                key: float(bahan.get(key, 0.0)) * faktor
                for key in ["energi", "protein", "lemak", "karbohidrat", "serat"]
            }
            item_rows.append(
                {
                    "menu_id": menu_id,
                    "kelompok": kelompok,
                    "kode_bahan": str(bahan.get("kode_bahan", "")),
                    "nama_bahan": str(bahan.get("nama_bahan", "")),
                    "porsi_gram": porsi,
                    "biaya_item": faktor * float(bahan.get("harga_per_100g", 0.0)),
                    "nutrisi_item": json_safe(nutrisi_item),
                }
            )

        if item_rows:
            self._require_client().table("optimasi_item").insert(item_rows).execute()

        menu["_db_menu_id"] = menu_id
        menu["_db_day_id"] = day_id
        return menu_id

    def save_daily_optimization(
        self,
        *,
        session_id: str,
        kelompok_umur: str,
        jumlah_siswa: int,
        harga_target: float,
        faktor_makan: float,
        pop_size: int,
        n_gen: int,
        akg_digunakan: Any,
        target_makan: Dict[str, float],
        menus: List[Dict[str, Any]],
        pareto_front: Any,
    ) -> str:
        run_id = self._insert_run(
            session_id=session_id,
            mode="daily",
            kelompok_umur=kelompok_umur,
            jumlah_siswa=jumlah_siswa,
            harga_target=harga_target,
            faktor_makan=faktor_makan,
            pop_size=pop_size,
            n_gen=n_gen,
            akg_digunakan=akg_digunakan,
        )
        day_id = self._insert_day(
            run_id=run_id,
            day_index=1,
            day_name="Harian",
            target_makan=target_makan,
            pareto_front=pareto_front,
        )
        for menu in menus:
            self._insert_menu(day_id=day_id, menu=menu, target_makan=target_makan)
        return run_id

    def save_weekly_optimization(
        self,
        *,
        session_id: str,
        kelompok_umur: str,
        jumlah_siswa: int,
        harga_target: float,
        faktor_makan: float,
        pop_size: int,
        n_gen: int,
        akg_digunakan: Any,
        weekly_plan: List[Dict[str, Any]],
    ) -> str:
        run_id = self._insert_run(
            session_id=session_id,
            mode="weekly",
            kelompok_umur=kelompok_umur,
            jumlah_siswa=jumlah_siswa,
            harga_target=harga_target,
            faktor_makan=faktor_makan,
            pop_size=pop_size,
            n_gen=n_gen,
            akg_digunakan=akg_digunakan,
        )

        for day_index, day in enumerate(weekly_plan, start=1):
            day_id = self._insert_day(
                run_id=run_id,
                day_index=day_index,
                day_name=str(day.get("hari", f"Hari {day_index}")),
                target_makan=day.get("target_makan", {}),
                pareto_front=day.get("F", []),
            )
            day["_db_day_id"] = day_id
            for menu in day.get("tiga_menu", []):
                self._insert_menu(
                    day_id=day_id,
                    menu=menu,
                    target_makan=day.get("target_makan", {}),
                )

        return run_id

    def mark_menu_selected(self, day_id: str, menu_id: str) -> None:
        client = self._require_client()
        client.table("optimasi_menu").update({"terpilih": False}).eq(
            "hari_id", day_id
        ).execute()
        client.table("optimasi_menu").update({"terpilih": True}).eq(
            "id", menu_id
        ).execute()

    def save_weekly_evaluation(
        self,
        *,
        run_id: str,
        selected_days: int,
        evaluation: Dict[str, Any],
        insights: List[Dict[str, Any]],
        adapted_akg: Any = None,
        adaptation_log: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        payload = {
            "run_id": run_id,
            "jumlah_hari_dipilih": int(selected_days),
            "evaluasi": json_safe(evaluation),
            "insights": json_safe(insights),
            "akg_adaptif": json_safe(adapted_akg),
            "log_adaptasi": json_safe(adaptation_log or []),
            "updated_at": datetime.utcnow().isoformat(),
        }

        client = self._require_client()
        existing = (
            client.table("evaluasi_mingguan")
            .select("id")
            .eq("run_id", run_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            client.table("evaluasi_mingguan").update(payload).eq(
                "run_id", run_id
            ).execute()
        else:
            client.table("evaluasi_mingguan").insert(payload).execute()

    def fetch_recent_runs(self, limit: int = 25) -> List[Dict[str, Any]]:
        response = (
            self._require_client()
            .table("optimasi_run")
            .select(
                "id,created_at,mode,kelompok_umur,jumlah_siswa,harga_target,"
                "faktor_makan,pop_size,n_gen,status"
            )
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute()
        )
        return list(response.data or [])
