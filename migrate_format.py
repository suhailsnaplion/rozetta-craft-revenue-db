#!/usr/bin/env python3
"""Migrate app.py to new Myntra data format"""

import re

with open('/Users/mohdsuhail/ecommerce_dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace COLUMN_MAP
match = re.search(r'COLUMN_MAP = \{[^}]+\}', content, re.DOTALL)
if match:
    new_map = '''COLUMN_MAP = {
    "order created on": "order_date",
    "sku code": "sku",
    "article type": "article_type",
    "cancelled on date": "cancelled_on",
    "return on date": "return_date",
    "shipping location state": "state",
    "prepaid final settled amount": "prepaid_final_amount",
    "postpaid final settled amount": "postpaid_final_amount",
}'''
    content = content[:match.start()] + new_map + content[match.end():]
    print("✓ Updated COLUMN_MAP")

# 2. Replace validate_input_file_columns
match = re.search(r'def validate_input_file_columns\(df_raw.*?\n    return True, ""', content, re.DOTALL)
if match:
    new_func = '''def validate_input_file_columns(df_raw: pd.DataFrame):
    cols = [c.strip().lower() for c in df_raw.columns]
    required = [
        "order created on",
        "sku code",
        "article type",
        "prepaid final settled amount",
        "postpaid final settled amount",
    ]
    missing = [c for c in required if c not in cols]
    if missing:
        return False, (
            "Uploaded file does not look like a valid Myntra monthly report. "
            f"Missing required columns: {', '.join(missing)}"
        )
    return True, ""'''
    content = content[:match.start()] + new_func + content[match.end():]
    print("✓ Updated validate_input_file_columns")

# 3. Replace compute_financials
match = re.search(r'def compute_financials\(df: pd\.DataFrame, sku_cp.*?\n    return df', content, re.DOTALL)
if match:
    new_func = '''def compute_financials(df: pd.DataFrame, sku_cp: dict) -> pd.DataFrame:
    """
    SP = prepaid final settled amount + postpaid final settled amount
    Revenue (sales only) = SP
    Product cost (sales only) = CP
    Contribution (sales only) = SP - CP
    """
    # Calculate final amount (SP) by summing prepaid and postpaid
    prepaid = pd.to_numeric(df.get("prepaid_final_amount", 0), errors="coerce").fillna(0)
    postpaid = pd.to_numeric(df.get("postpaid_final_amount", 0), errors="coerce").fillna(0)
    df["final_amount"] = prepaid + postpaid
    df["sp"] = df["final_amount"]

    df["cp"] = df["sku"].map(sku_cp).fillna(0)
    df["revenue"] = df.apply(lambda r: r["sp"] if r["status"] == "sale" else 0, axis=1)
    df["profit"] = df.apply(lambda r: (r["sp"] - r["cp"]) if r["status"] == "sale" else 0, axis=1)
    return df'''
    content = content[:match.start()] + new_func + content[match.end():]
    print("✓ Updated compute_financials")

with open('/Users/mohdsuhail/ecommerce_dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ Migration complete!")
print("Key changes:")
print("  • COLUMN_MAP updated for new format")
print("  • Column validation updated")
print("  • Financial calculations use prepaid+postpaid")
print("  • SKU lookup via 'sku code' column")
print("  • Geography uses 'shipping location state'")
