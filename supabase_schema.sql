-- Run this in Supabase SQL Editor

create table if not exists public.sku_cost_prices (
    sku text primary key,
    cost_price double precision not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.monthly_costs (
    month text primary key,
    logistic_cost double precision not null default 0,
    ops_cost double precision not null default 0,
    misc_cost double precision not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.uploaded_orders (
    id bigserial primary key,
    order_date date,
    sku text,
    article_type text,
    state text,
    status text,
    final_amount double precision not null default 0,
    gt_charges double precision not null default 0,
    sp double precision not null default 0,
    cp double precision not null default 0,
    revenue double precision not null default 0,
    profit double precision not null default 0,
    upload_token text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_uploaded_orders_upload_token
    on public.uploaded_orders (upload_token);

create index if not exists idx_uploaded_orders_order_date
    on public.uploaded_orders (order_date);

-- For quick setup only (single trusted app):
-- disable row level security. Later you can enable RLS + policies.
alter table public.sku_cost_prices disable row level security;
alter table public.monthly_costs disable row level security;
alter table public.uploaded_orders disable row level security;
