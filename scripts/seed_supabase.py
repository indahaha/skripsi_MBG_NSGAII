"""Unggah bahan_mentah.csv dan akg.csv ke Supabase satu kali."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.database import json_safe  # noqa: E402


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} belum diisi.")
    return value


def records_from_csv(path: Path):
    frame = pd.read_csv(path)
    return [json_safe(row) for row in frame.to_dict(orient="records")]


def main() -> None:
    url = require_env("SUPABASE_URL")
    service_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    client = create_client(url, service_key)

    bahan_rows = records_from_csv(ROOT / "data" / "bahan_mentah.csv")
    for row in bahan_rows:
        row["aktif"] = True

    akg_rows = records_from_csv(ROOT / "data" / "akg.csv")
    for index, row in enumerate(akg_rows, start=1):
        row["urutan"] = index

    print(f"Mengunggah {len(bahan_rows)} baris bahan mentah...")
    client.table("bahan_mentah").upsert(
        bahan_rows, on_conflict="kode_bahan"
    ).execute()

    print(f"Mengunggah {len(akg_rows)} baris AKG...")
    client.table("akg").upsert(
        akg_rows, on_conflict="kelompok_umur"
    ).execute()

    print("Seed selesai. Master data Supabase sudah siap.")


if __name__ == "__main__":
    main()
