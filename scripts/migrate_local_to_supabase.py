import json
import os
from pathlib import Path

import pandas as pd

try:
    from supabase import create_client
except Exception as exc:
    raise SystemExit("Install dependency first: pip install supabase") from exc


ROOT = Path(__file__).resolve().parent.parent
SKU_CP_FILE = ROOT / "sku_cost_prices.json"
MONTHLY_COSTS_FILE = ROOT / "monthly_costs.json"
UPLOADED_ORDERS_FILE = ROOT / "uploaded_orders.csv"


SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("Missing SUPABASE_URL or SUPABASE_KEY environment variable.")


client = create_client(SUPABASE_URL, SUPABASE_KEY)


def migrate_sku_cp():
    if not SKU_CP_FILE.exists():
        print("[SKU] file not found, skipping")
        return

    with open(SKU_CP_FILE, "r") as f:
        data = json.load(f)

    payload = []
    for sku, cp in data.items():
        payload.append(
            {
                "sku": str(sku),
                "cost_price": float(cp or 0),
            }
        )

    if payload:
        client.table("sku_cost_prices").upsert(payload, on_conflict="sku").execute()
    print(f"[SKU] migrated {len(payload)} rows")


def migrate_monthly_costs():
    if not MONTHLY_COSTS_FILE.exists():
        print("[COSTS] file not found, skipping")
        return

    with open(MONTHLY_COSTS_FILE, "r") as f:
        data = json.load(f)

    payload = []
    for month, vals in data.items():
        payload.append(
            {
                "month": str(month),
                "logistic_cost": float(vals.get("logistic_cost", 0) or 0),
                "ops_cost": float(vals.get("ops_cost", 0) or 0),
                "misc_cost": float(vals.get("misc_cost", 0) or 0),
                "updated_at": vals.get("updated_at"),
            }
        )

    if payload:
        client.table("monthly_costs").upsert(payload, on_conflict="month").execute()
    print(f"[COSTS] migrated {len(payload)} rows")


def migrate_uploaded_orders():
    if not UPLOADED_ORDERS_FILE.exists():
        print("[ORDERS] file not found, skipping")
        return

    df = pd.read_csv(UPLOADED_ORDERS_FILE)
    if df.empty:
        print("[ORDERS] file empty, skipping")
        return

    expected_cols = [
        "order_date",
        "sku",
        "article_type",
        "state",
        "status",
        "final_amount",
        "gt_charges",
        "sp",
        "cp",
        "revenue",
        "profit",
        "upload_token",
    ]

    for col in expected_cols:
        if col not in df.columns:
            df[col] = None

    df = df[expected_cols].copy()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    for col in ["final_amount", "gt_charges", "sp", "cp", "revenue", "profit"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    payload = df.to_dict(orient="records")

    # Insert in chunks to avoid payload limits.
    chunk_size = 1000
    inserted = 0
    for i in range(0, len(payload), chunk_size):
        chunk = payload[i : i + chunk_size]
        client.table("uploaded_orders").insert(chunk).execute()
        inserted += len(chunk)

    print(f"[ORDERS] migrated {inserted} rows")


def main():
    migrate_sku_cp()
    migrate_monthly_costs()
    migrate_uploaded_orders()
    print("Migration complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        if "row-level security policy" in msg.lower() or "42501" in msg:
            raise SystemExit(
                "Migration failed due to Supabase permissions (RLS). "
                "Use SUPABASE_SERVICE_ROLE_KEY for migration/app backend, or configure INSERT policies for your key."
            )
        raise
