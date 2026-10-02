-- =============================================================
-- SKEMA SUPABASE UNTUK SISTEM OPTIMASI MENU MBG
-- Jalankan seluruh file ini melalui Supabase Dashboard > SQL Editor.
-- =============================================================

create extension if not exists pgcrypto;

-- 1. MASTER DATA BAHAN
create table if not exists public.bahan_mentah (
    kode_bahan text primary key,
    nama_bahan text not null,
    energi numeric not null default 0,
    protein numeric not null default 0,
    lemak numeric not null default 0,
    karbohidrat numeric not null default 0,
    serat numeric not null default 0,
    harga_kg numeric not null default 0,
    harga_per_100g numeric not null default 0,
    kategori text not null,
    kelompok text not null check (kelompok in ('karbohidrat', 'lauk', 'sayur', 'buah')),
    aktif boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_bahan_kelompok on public.bahan_mentah(kelompok);
create index if not exists idx_bahan_aktif on public.bahan_mentah(aktif);

-- 2. MASTER DATA AKG
create table if not exists public.akg (
    kelompok_umur text primary key,
    urutan integer not null default 0,
    energi numeric not null default 0,
    protein numeric not null default 0,
    lemak numeric not null default 0,
    karbohidrat numeric not null default 0,
    serat numeric not null default 0,
    kalsium numeric not null default 0,
    besi numeric not null default 0,
    seng numeric not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- 3. SATU EKSEKUSI OPTIMASI
create table if not exists public.optimasi_run (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null,
    mode text not null check (mode in ('daily', 'weekly')),
    kelompok_umur text not null references public.akg(kelompok_umur),
    jumlah_siswa integer not null check (jumlah_siswa > 0),
    harga_target numeric not null check (harga_target > 0),
    faktor_makan numeric not null default 0.35,
    pop_size integer not null,
    n_gen integer not null,
    akg_digunakan jsonb not null default '{}'::jsonb,
    status text not null default 'selesai',
    created_at timestamptz not null default now()
);

create index if not exists idx_optimasi_run_created on public.optimasi_run(created_at desc);
create index if not exists idx_optimasi_run_mode on public.optimasi_run(mode);
create index if not exists idx_optimasi_run_session on public.optimasi_run(session_id);

-- 4. DATA PER HARI DALAM SATU RUN
create table if not exists public.optimasi_hari (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.optimasi_run(id) on delete cascade,
    hari_ke integer not null check (hari_ke > 0),
    nama_hari text not null,
    target_makan jsonb not null default '{}'::jsonb,
    pareto_front jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    unique(run_id, hari_ke)
);

create index if not exists idx_optimasi_hari_run on public.optimasi_hari(run_id);

-- 5. TIGA MENU REKOMENDASI PER HARI
create table if not exists public.optimasi_menu (
    id uuid primary key default gen_random_uuid(),
    hari_id uuid not null references public.optimasi_hari(id) on delete cascade,
    label text not null check (label in ('bergizi', 'harga', 'seimbang')),
    biaya numeric not null default 0,
    selisih_harga numeric not null default 0,
    deviasi_gizi numeric not null default 0,
    nutrisi jsonb not null default '{}'::jsonb,
    fingerprint text not null,
    terpilih boolean not null default false,
    created_at timestamptz not null default now(),
    unique(hari_id, label)
);

create index if not exists idx_optimasi_menu_hari on public.optimasi_menu(hari_id);
create index if not exists idx_optimasi_menu_terpilih on public.optimasi_menu(terpilih);

-- 6. DETAIL BAHAN SETIAP MENU
create table if not exists public.optimasi_item (
    id uuid primary key default gen_random_uuid(),
    menu_id uuid not null references public.optimasi_menu(id) on delete cascade,
    kelompok text not null check (kelompok in ('karbohidrat', 'lauk', 'sayur', 'buah')),
    kode_bahan text not null references public.bahan_mentah(kode_bahan),
    nama_bahan text not null,
    porsi_gram numeric not null check (porsi_gram > 0),
    biaya_item numeric not null default 0,
    nutrisi_item jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique(menu_id, kelompok)
);

create index if not exists idx_optimasi_item_menu on public.optimasi_item(menu_id);

-- 7. HASIL EVALUASI DAN ADAPTASI AKG
create table if not exists public.evaluasi_mingguan (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null unique references public.optimasi_run(id) on delete cascade,
    jumlah_hari_dipilih integer not null default 0,
    evaluasi jsonb not null default '{}'::jsonb,
    insights jsonb not null default '[]'::jsonb,
    akg_adaptif jsonb,
    log_adaptasi jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- =============================================================
-- ROW LEVEL SECURITY
-- Sistem saat ini belum memakai login. Kebijakan berikut memberi akses
-- baca dan simpan kepada anon key. Jangan gunakan kebijakan terbuka ini
-- untuk data pribadi atau aplikasi produksi tanpa autentikasi.
-- =============================================================

alter table public.bahan_mentah enable row level security;
alter table public.akg enable row level security;
alter table public.optimasi_run enable row level security;
alter table public.optimasi_hari enable row level security;
alter table public.optimasi_menu enable row level security;
alter table public.optimasi_item enable row level security;
alter table public.evaluasi_mingguan enable row level security;

-- Hapus policy lama agar file aman dijalankan ulang.
drop policy if exists "master bahan dapat dibaca" on public.bahan_mentah;
drop policy if exists "master akg dapat dibaca" on public.akg;
drop policy if exists "run dapat dibaca" on public.optimasi_run;
drop policy if exists "run dapat ditambah" on public.optimasi_run;
drop policy if exists "hari dapat dibaca" on public.optimasi_hari;
drop policy if exists "hari dapat ditambah" on public.optimasi_hari;
drop policy if exists "menu dapat dibaca" on public.optimasi_menu;
drop policy if exists "menu dapat ditambah" on public.optimasi_menu;
drop policy if exists "menu dapat diperbarui" on public.optimasi_menu;
drop policy if exists "item dapat dibaca" on public.optimasi_item;
drop policy if exists "item dapat ditambah" on public.optimasi_item;
drop policy if exists "evaluasi dapat dibaca" on public.evaluasi_mingguan;
drop policy if exists "evaluasi dapat ditambah" on public.evaluasi_mingguan;
drop policy if exists "evaluasi dapat diperbarui" on public.evaluasi_mingguan;

create policy "master bahan dapat dibaca"
on public.bahan_mentah for select
to anon, authenticated
using (true);

create policy "master akg dapat dibaca"
on public.akg for select
to anon, authenticated
using (true);

create policy "run dapat dibaca"
on public.optimasi_run for select
to anon, authenticated
using (true);

create policy "run dapat ditambah"
on public.optimasi_run for insert
to anon, authenticated
with check (true);

create policy "hari dapat dibaca"
on public.optimasi_hari for select
to anon, authenticated
using (true);

create policy "hari dapat ditambah"
on public.optimasi_hari for insert
to anon, authenticated
with check (true);

create policy "menu dapat dibaca"
on public.optimasi_menu for select
to anon, authenticated
using (true);

create policy "menu dapat ditambah"
on public.optimasi_menu for insert
to anon, authenticated
with check (true);

create policy "menu dapat diperbarui"
on public.optimasi_menu for update
to anon, authenticated
using (true)
with check (true);

create policy "item dapat dibaca"
on public.optimasi_item for select
to anon, authenticated
using (true);

create policy "item dapat ditambah"
on public.optimasi_item for insert
to anon, authenticated
with check (true);

create policy "evaluasi dapat dibaca"
on public.evaluasi_mingguan for select
to anon, authenticated
using (true);

create policy "evaluasi dapat ditambah"
on public.evaluasi_mingguan for insert
to anon, authenticated
with check (true);

create policy "evaluasi dapat diperbarui"
on public.evaluasi_mingguan for update
to anon, authenticated
using (true)
with check (true);
