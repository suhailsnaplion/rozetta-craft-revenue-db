import streamlit as st
import pandas as pd
import json
import os
import hashlib
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime


def _read_secret(name: str) -> str:
    val = os.getenv(name, "").strip()
    if val:
        return val
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""

try:
    from supabase import create_client as _create_supabase_client
except Exception:
    _create_supabase_client = None

try:
    from openai import AzureOpenAI as _AzureOpenAI
    _AZURE_API_KEY = _read_secret("AZURE_OPENAI_API_KEY")
    _AZURE_ENDPOINT = _read_secret("AZURE_OPENAI_ENDPOINT")
    _AZURE_API_VERSION = _read_secret("AZURE_OPENAI_API_VERSION") or "2025-01-01-preview"
    if _AZURE_API_KEY and _AZURE_ENDPOINT:
        _AZURE_CLIENT = _AzureOpenAI(
            api_key=_AZURE_API_KEY,
            azure_endpoint=_AZURE_ENDPOINT,
            api_version=_AZURE_API_VERSION,
        )
    else:
        _AZURE_CLIENT = None
except Exception:
    _AZURE_CLIENT = None

# ── Config ──────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Rozetta Craft Monthly Revenue Dashboard",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

SKU_CP_FILE = os.path.join(os.path.dirname(__file__), "sku_cost_prices.json")
MONTHLY_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "monthly_history.json")
MONTHLY_COSTS_FILE = os.path.join(os.path.dirname(__file__), "monthly_costs.json")
UPLOADED_ORDERS_FILE = os.path.join(os.path.dirname(__file__), "uploaded_orders.csv")
SUPABASE_URL = _read_secret("SUPABASE_URL")
SUPABASE_KEY = _read_secret("SUPABASE_KEY")

COLUMN_MAP = {
    "settlement due date": "settlement_due_date",
    "sku code": "sku",
    "article type": "article_type",
    "cancelled on date": "cancelled_on",
    "return on date": "return_date",
    "shipping location state": "state",
    "prepaid final settled amount": "prepaid_final_amount",
    "postpaid final settled amount": "postpaid_final_amount",
}

PREPAID_DEDUCTION_COLS = [
    "prepaid sjit incentive amount",
    "prepaid platform funded coupon",
    "prepaid commission",
    "prepaid commission discount",
    "prepaid tcs amount",
    "prepaid tds amount",
    "prepaid tech enablement charges",
    "prepaid pick and pack fees",
    "prepaid forward additional charges",
    "prepaid collection cost",
    "prepaid fixed cost",
    "prepaid reverse additional charges",
    "prepaid total tax on logistics",
    "prepaid shipping fee",
    "prepaid payment gateway fee",
]

POSTPAID_DEDUCTION_COLS = [
    "postpaid sjit incentive amount",
    "postpaid platform funded coupon",
    "postpaid commission",
    "postpaid commission discount",
    "postpaid tcs amount",
    "postpaid tds amount",
    "postpaid tech enablement charges",
    "postpaid pick and pack fees",
    "postpaid forward additional charges",
    "postpaid collection cost",
    "postpaid fixed cost",
    "postpaid reverse additional charges",
    "postpaid total tax on logistics",
    "postpaid shipping fee",
    "postpaid payment gateway fee",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY and _create_supabase_client is not None)


@st.cache_resource
def get_supabase_client():
    if not _supabase_enabled():
        return None
    try:
        return _create_supabase_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


def _warn_storage_fallback(msg: str):
    key = f"storage_warn::{msg}"
    if key not in st.session_state:
        st.session_state[key] = True
        st.warning(msg)


def normalize_sku(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text.upper()


def load_sku_cp():
    client = get_supabase_client()
    if client is not None:
        try:
            rows = client.table("sku_cost_prices").select("sku,cost_price").execute().data or []
            return {
                normalize_sku(r.get("sku")): float(r.get("cost_price") or 0)
                for r in rows
                if normalize_sku(r.get("sku"))
            }
        except Exception:
            _warn_storage_fallback("Supabase unavailable for SKU cost prices. Falling back to local file storage.")

    if os.path.exists(SKU_CP_FILE):
        with open(SKU_CP_FILE, "r") as f:
            raw = json.load(f)
            return {normalize_sku(k): float(v or 0) for k, v in raw.items() if normalize_sku(k)}
    return {}


def save_sku_cp(data: dict):
    normalized_data = {
        normalize_sku(k): float(v or 0)
        for k, v in data.items()
        if normalize_sku(k)
    }

    client = get_supabase_client()
    if client is not None:
        try:
            now_iso = datetime.now().isoformat()
            payload = [
                {
                    "sku": normalize_sku(k),
                    "cost_price": float(v),
                    "updated_at": now_iso,
                }
                for k, v in normalized_data.items()
            ]
            if payload:
                client.table("sku_cost_prices").upsert(payload, on_conflict="sku").execute()
            return
        except Exception:
            _warn_storage_fallback("Supabase write failed for SKU cost prices. Falling back to local file storage.")

    with open(SKU_CP_FILE, "w") as f:
        json.dump(normalized_data, f, indent=2)


def load_monthly_history() -> dict:
    if os.path.exists(MONTHLY_HISTORY_FILE):
        with open(MONTHLY_HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}


def save_monthly_history(data: dict):
    with open(MONTHLY_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_monthly_costs() -> dict:
    client = get_supabase_client()
    if client is not None:
        try:
            rows = client.table("monthly_costs").select("month,logistic_cost,ops_cost,misc_cost,updated_at").execute().data or []
            out = {}
            for r in rows:
                month = str(r.get("month") or "").strip()
                if not month:
                    continue
                out[month] = {
                    "logistic_cost": float(r.get("logistic_cost") or 0),
                    "ops_cost": float(r.get("ops_cost") or 0),
                    "misc_cost": float(r.get("misc_cost") or 0),
                    "updated_at": r.get("updated_at") or datetime.now().isoformat(),
                }
            return out
        except Exception:
            _warn_storage_fallback("Supabase unavailable for monthly costs. Falling back to local file storage.")

    if os.path.exists(MONTHLY_COSTS_FILE):
        with open(MONTHLY_COSTS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_monthly_costs(data: dict):
    client = get_supabase_client()
    if client is not None:
        try:
            payload = []
            for month, vals in data.items():
                payload.append(
                    {
                        "month": str(month),
                        "logistic_cost": float(vals.get("logistic_cost", 0) or 0),
                        "ops_cost": float(vals.get("ops_cost", 0) or 0),
                        "misc_cost": float(vals.get("misc_cost", 0) or 0),
                        "updated_at": vals.get("updated_at") or datetime.now().isoformat(),
                    }
                )
            if payload:
                client.table("monthly_costs").upsert(payload, on_conflict="month").execute()
            return
        except Exception:
            _warn_storage_fallback("Supabase write failed for monthly costs. Falling back to local file storage.")

    with open(MONTHLY_COSTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def apply_custom_theme():
    st.markdown(
        """
        <style>
            * { margin: 0; padding: 0; }
            .stApp {
                background: linear-gradient(135deg, #0f0c29 0%, #1a1a4d 25%, #16213e 50%, #0f3460 75%, #16213e 100%);
                background-attachment: fixed;
                color: #ecf0f1;
            }
            .block-container {
                padding-top: 2rem;
                max-width: 1400px;
            }
            h1 {
                background: linear-gradient(120deg, #ffffff 0%, #e0f7ff 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                font-weight: 900;
                font-size: 2.8rem !important;
                margin-bottom: 0.5rem;
                letter-spacing: -1px;
            }
            h2 {
                color: #ffffff;
                font-weight: 700;
                margin-top: 1.5rem;
                margin-bottom: 1rem;
                text-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
            }
            h3 {
                color: #ffffff;
                font-weight: 600;
            }
            .stMetricLabel,
            [data-testid="stMetricLabel"] {
                color: #ffffff !important;
                font-size: 0.85rem !important;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                font-weight: 700;
            }
            .stMetricValue,
            [data-testid="stMetricValue"] {
                color: #ffffff !important;
                font-size: 2.2rem !important;
                font-weight: 900 !important;
                text-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
            }
            [data-testid="stMetricDelta"] {
                color: #ffffff !important;
                font-weight: 700 !important;
            }
            [data-testid="metric-container"] {
                background: linear-gradient(135deg, rgba(15, 60, 96, 0.8) 0%, rgba(31, 41, 55, 0.8) 100%);
                border: 1px solid rgba(0, 212, 255, 0.3);
                border-radius: 16px;
                padding: 1.5rem;
                backdrop-filter: blur(10px);
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1);
                transition: all 0.3s ease;
            }
            [data-testid="metric-container"]:hover {
                border-color: rgba(0, 212, 255, 0.6);
                box-shadow: 0 12px 48px rgba(0, 212, 255, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1);
                transform: translateY(-2px);
            }
            .stButton > button {
                background: linear-gradient(135deg, #00d4ff 0%, #7c3aed 100%);
                color: white !important;
                border: none;
                border-radius: 12px;
                padding: 0.75rem 1.5rem !important;
                font-weight: 700;
                font-size: 0.95rem;
                letter-spacing: 0.5px;
                box-shadow: 0 8px 24px rgba(0, 212, 255, 0.3);
                transition: all 0.3s ease;
                cursor: pointer;
            }
            .stButton > button:hover {
                box-shadow: 0 12px 36px rgba(0, 212, 255, 0.5);
                transform: translateY(-2px);
            }
            .stSelectbox > div > div > div {
                background: rgba(20, 30, 50, 0.92) !important;
                border: 1px solid rgba(0, 212, 255, 0.3) !important;
                border-radius: 10px !important;
            }
            .stSelectbox label,
            .stSelectbox > div > div > div > div {
                color: #1e40af !important;
                font-weight: 600 !important;
            }
            [data-baseweb="select"] > div {
                background: rgba(20, 30, 50, 0.92) !important;
                color: #1e40af !important;
            }
            [role="listbox"] {
                background: rgba(20, 30, 50, 0.98) !important;
                border: 1px solid rgba(0, 212, 255, 0.3) !important;
            }
            [role="option"] {
                color: #1e40af !important;
                background: transparent !important;
            }
            [role="option"][aria-selected="true"] {
                background: rgba(0, 212, 255, 0.18) !important;
            }
            .stRadio > div[role="radiogroup"] > div {
                color: #ffffff !important;
            }
            .stRadio label {
                color: #ffffff !important;
                font-weight: 600 !important;
            }
            .stTextInput > div > div > input {
                background: rgba(255, 255, 255, 0.05) !important;
                border: 1px solid rgba(0, 212, 255, 0.3) !important;
                color: #ecf0f1 !important;
                border-radius: 10px !important;
                padding: 0.75rem !important;
                transition: all 0.3s ease;
            }
            .stTextInput > div > div > input:focus {
                border-color: rgba(0, 212, 255, 0.8) !important;
                box-shadow: 0 0 20px rgba(0, 212, 255, 0.3) !important;
            }
            .stNumberInput > div > div > input {
                background: rgba(255, 255, 255, 0.05) !important;
                border: 1px solid rgba(0, 212, 255, 0.3) !important;
                color: #ecf0f1 !important;
                border-radius: 10px !important;
                padding: 0.75rem !important;
                transition: all 0.3s ease;
            }
            .stNumberInput > div > div > input:focus {
                border-color: rgba(0, 212, 255, 0.8) !important;
                box-shadow: 0 0 20px rgba(0, 212, 255, 0.3) !important;
            }
            .stNumberInput > label,
            .stTextInput > label,
            .stSelectbox > label,
            .stFileUploader > label,
            label {
                color: #ffffff !important;
                font-weight: 700 !important;
                font-size: 0.95rem !important;
            }
            [data-testid="stSidebar"] .stSelectbox > label,
            [data-testid="stSidebar"] .stRadio > label,
            [data-testid="stSidebar"] .stRadio p,
            [data-testid="stSidebar"] .stMarkdown,
            [data-testid="stSidebar"] .stSubheader {
                color: #1e40af !important;
                font-weight: 800 !important;
            }
            [data-testid="stSidebar"] [data-baseweb="select"] > div {
                color: #1e40af !important;
            }
            [data-testid="stSidebar"] h1,
            [data-testid="stSidebar"] h2,
            [data-testid="stSidebar"] h3,
            [data-testid="stSidebar"] h4,
            [data-testid="stSidebar"] h5,
            [data-testid="stSidebar"] h6,
            [data-testid="stSidebar"] p,
            [data-testid="stSidebar"] span,
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] .stCaption,
            [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
            [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span {
                color: #1e40af !important;
                font-weight: 800 !important;
            }
            [data-testid="stFileUploaderDropzone"] button,
            [data-testid="stFileUploaderDropzone"] button span,
            [data-testid="stFileUploaderDropzone"] [data-testid="stBaseButton-secondary"] {
                color: #1e40af !important;
                border-color: #1e40af !important;
                font-weight: 800 !important;
            }
            [data-testid="stExpander"] {
                background: rgba(31, 41, 55, 0.7);
                border: 1px solid rgba(0, 212, 255, 0.2);
                border-radius: 14px;
                backdrop-filter: blur(10px);
            }
            [data-testid="stExpander"] > div > button {
                color: #ffffff !important;
                font-weight: 600 !important;
            }
            [data-testid="stExpander"] > div > button > div > div > div {
                color: #ffffff !important;
            }
            [data-baseweb="tab-list"] {
                gap: 0.25rem;
            }
            button[data-baseweb="tab"] {
                color: #ffffff !important;
                font-weight: 700 !important;
                background: rgba(20, 30, 50, 0.75) !important;
                border-radius: 10px 10px 0 0 !important;
                padding: 0.45rem 0.8rem !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] {
                color: #ffffff !important;
                border-bottom: 2px solid #00d4ff !important;
                background: rgba(0, 212, 255, 0.18) !important;
            }
            .stInfo, .stSuccess, .stWarning, .stError {
                border-radius: 12px !important;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(0, 212, 255, 0.3) !important;
                background-color: rgba(31, 41, 55, 0.8) !important;
            }
            .stInfo > div > p, .stSuccess > div > p, .stWarning > div > p, .stError > div > p {
                color: #ffffff !important;
            }
            .stDataFrame {
                border-radius: 14px;
                overflow: hidden;
                border: 1px solid rgba(0, 212, 255, 0.2);
            }
            .stCaption {
                color: #ffffff !important;
            }
            .stMarkdown {
                color: #ffffff;
            }
            p, li, span, div {
                color: #ffffff;
            }
        </style>
        <style>
            .plotly-container {
                border-radius: 14px !important;
                overflow: hidden !important;
                border: 1px solid rgba(0, 212, 255, 0.2) !important;
            }
            .plotly-graph-div {
                background: rgba(31, 41, 55, 0.9) !important;
            }
        </style>
        <script>
            window.addEventListener('load', function() {
                setTimeout(function() {
                    var charts = document.querySelectorAll('[data-testid="plotly.modebar"]');
                    charts.forEach(function(chart) {
                        var svg = chart.querySelector('svg');
                        if (svg) {
                            var parent = svg.closest('[data-testid="stPlotlyContainer"]');
                            if (parent && !parent.classList.contains('dark-plotly-styled')) {
                                parent.classList.add('dark-plotly-styled');
                                parent.style.background = 'rgba(31, 41, 55, 0.9)';
                                parent.style.borderRadius = '14px';
                                parent.style.border = '1px solid rgba(0, 212, 255, 0.2)';
                                parent.style.overflow = 'hidden';
                            }
                        }
                    });
                }, 500);
            });
        </script>
        """,
        unsafe_allow_html=True,
    )


def validate_input_file_columns(df_raw: pd.DataFrame):
    cols = [c.strip().lower() for c in df_raw.columns]
    required = [
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
    return True, ""


def load_uploaded_orders() -> pd.DataFrame:
    client = get_supabase_client()
    if client is not None:
        try:
            cols = "order_date,sku,article_type,state,status,final_amount,gt_charges,sp,cp,revenue,profit,upload_token"
            all_rows = []
            page_size = 1000
            offset = 0
            while True:
                batch = client.table("uploaded_orders").select(cols).range(offset, offset + page_size - 1).execute().data or []
                all_rows.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size
            df = pd.DataFrame(all_rows)
            if df.empty:
                return pd.DataFrame()
            if "order_date" in df.columns:
                df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
            for col in ["final_amount", "gt_charges", "sp", "cp", "revenue", "profit"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            return df
        except Exception:
            _warn_storage_fallback("Supabase unavailable for uploaded orders. Falling back to local file storage.")

    if not os.path.exists(UPLOADED_ORDERS_FILE):
        return pd.DataFrame()
    df = pd.read_csv(UPLOADED_ORDERS_FILE)
    if "order_date" in df.columns:
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    for col in ["final_amount", "gt_charges", "sp", "cp", "revenue", "profit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def persist_uploaded_orders(df: pd.DataFrame, upload_token: str):
    persist_cols = [
        "order_date", "sku", "article_type", "state", "status",
        "final_amount", "gt_charges", "sp", "cp", "revenue", "profit",
    ]
    to_save = df.copy()
    for c in persist_cols:
        if c not in to_save.columns:
            to_save[c] = 0
    to_save = to_save[persist_cols]
    to_save["upload_token"] = upload_token

    if "order_date" in to_save.columns:
        to_save["order_date"] = pd.to_datetime(to_save["order_date"], errors="coerce")
        to_save["order_date"] = to_save["order_date"].dt.strftime("%Y-%m-%d")

    client = get_supabase_client()
    if client is not None:
        try:
            exists = client.table("uploaded_orders").select("id").eq("upload_token", upload_token).limit(1).execute().data
            if exists:
                return

            for c in ["final_amount", "gt_charges", "sp", "cp", "revenue", "profit"]:
                if c in to_save.columns:
                    to_save[c] = pd.to_numeric(to_save[c], errors="coerce").fillna(0)

            # Supabase/PostgREST can reject very large payloads, so write in batches.
            to_save = to_save.astype(object).where(pd.notna(to_save), None)

            payload = to_save.to_dict(orient="records")
            if payload:
                batch_size = 500
                for start in range(0, len(payload), batch_size):
                    batch = payload[start:start + batch_size]
                    client.table("uploaded_orders").insert(batch).execute()
            return
        except Exception as e:
            _warn_storage_fallback(f"Supabase write failed for uploaded orders ({type(e).__name__}: {e}). Falling back to local file storage.")

    existing = load_uploaded_orders()
    if not existing.empty and "upload_token" in existing.columns:
        if upload_token in existing["upload_token"].astype(str).unique().tolist():
            return

    combined = pd.concat([existing, to_save], ignore_index=True)
    combined.to_csv(UPLOADED_ORDERS_FILE, index=False)


def set_monthly_costs_for_upload(df: pd.DataFrame, logistic_cost: float, ops_cost: float, misc_cost: float):
    costs = load_monthly_costs()
    sales_df = df[df["status"] == "sale"].copy()
    if sales_df.empty or "order_date" not in sales_df.columns:
        return

    months = sorted(sales_df["order_date"].dt.to_period("M").astype(str).dropna().unique().tolist())
    if not months:
        return

    divisor = max(len(months), 1)
    for month in months:
        costs[month] = {
            "logistic_cost": float(logistic_cost) / divisor,
            "ops_cost": float(ops_cost) / divisor,
            "misc_cost": float(misc_cost) / divisor,
            "updated_at": datetime.now().isoformat(),
        }
    save_monthly_costs(costs)


def format_inr(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        return "₹0"
    return f"₹{v:,.0f}"


def format_inr_short(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        return "₹0"
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 10000000:
        return f"{sign}₹{abs_v / 10000000:.2f} Cr"
    if abs_v >= 100000:
        return f"{sign}₹{abs_v / 100000:.2f} Lakh"
    if abs_v >= 1000:
        return f"{sign}₹{abs_v / 1000:.2f} K"
    return f"{sign}₹{abs_v:,.0f}"


def make_lakh_ticks(max_val: float, steps: int = 6):
    top = max(float(max_val), 1.0)
    vals = [top * i / steps for i in range(steps + 1)]
    labels = [format_inr_short(v) for v in vals]
    return vals, labels


def style_figure(fig):
    """Apply dark theme to Plotly figure for professional appearance."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(31, 41, 55, 0.9)",
        plot_bgcolor="rgba(50, 60, 80, 0.5)",
        font=dict(family="Arial, sans-serif", size=12, color="#ffffff"),
        title_font=dict(size=16, color="#ffffff", family="Arial, sans-serif"),
        xaxis=dict(
            title_font=dict(color="#ffffff"),
            tickfont=dict(color="#ffffff"),
            gridcolor="rgba(100, 120, 150, 0.15)",
            zeroline=False,
        ),
        yaxis=dict(
            title_font=dict(color="#ffffff"),
            tickfont=dict(color="#ffffff"),
            gridcolor="rgba(100, 120, 150, 0.15)",
            zeroline=False,
        ),
        legend=dict(
            font=dict(color="#ffffff", size=11),
            bgcolor="rgba(31, 41, 55, 0.9)",
            bordercolor="rgba(0, 212, 255, 0.4)",
            borderwidth=1,
            orientation="h",
            x=1.0,
            y=1.14,
            xanchor="right",
            yanchor="bottom",
        ),
        margin=dict(l=60, r=85, t=120, b=60),
        hovermode="x unified",
    )
    fig.update_coloraxes(
        colorbar=dict(
            tickfont=dict(color="#ffffff"),
            title=dict(font=dict(color="#ffffff")),
            bgcolor="rgba(31, 41, 55, 0.9)",
            x=1.08,
            xpad=8,
        )
    )
    return fig


def _first_matching_column(df: pd.DataFrame, candidates: list[str]):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def import_sku_cp_file(uploaded_file, existing_cp: dict):
    """Read SKU cost list from CSV/XLSX and merge it with saved CP values."""
    try:
        file_name = uploaded_file.name.lower()
        if file_name.endswith(".csv"):
            cp_df = pd.read_csv(uploaded_file)
        elif file_name.endswith(".xlsx"):
            cp_df = pd.read_excel(uploaded_file)
        else:
            return existing_cp, "Unsupported file type. Please upload CSV or XLSX.", "error"

        cp_df.columns = cp_df.columns.str.strip().str.lower()

        sku_col = _first_matching_column(cp_df, [
            "seller sku code", "sku", "seller_sku_code", "seller sku", "sku code"
        ])
        cp_col = _first_matching_column(cp_df, [
            "cost price", "cost_price", "cp", "cost", "price"
        ])

        if not sku_col or not cp_col:
            return (
                existing_cp,
                "Could not detect required columns. Include SKU and Cost Price columns (e.g. 'seller sku code' and 'cost price').",
                "error",
            )

        cp_df = cp_df[[sku_col, cp_col]].copy()
        cp_df[sku_col] = cp_df[sku_col].astype(str).str.strip()
        cp_df[cp_col] = pd.to_numeric(cp_df[cp_col], errors="coerce")
        cp_df = cp_df[cp_df[sku_col].ne("") & cp_df[cp_col].notna()]
        cp_df = cp_df[cp_df[cp_col] >= 0]

        incoming = {row[sku_col]: float(row[cp_col]) for _, row in cp_df.iterrows()}
        merged = {**existing_cp, **incoming}
        save_sku_cp(merged)
        return merged, f"Imported {len(incoming)} SKU cost prices successfully.", "success"
    except Exception as e:
        return existing_cp, f"Failed to import SKU cost file: {e}", "error"


def read_monthly_report(uploaded_file):
    file_name = uploaded_file.name.lower()
    if file_name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if file_name.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    raise ValueError("Unsupported report format. Please upload CSV or XLSX.")


def get_ai_recommendations(df: pd.DataFrame) -> str:
    if _AZURE_CLIENT is None:
        return "❌ OpenAI SDK not installed. Run: pip install openai"

    sales = df[df["status"] == "sale"].copy()
    if sales.empty:
        return "No completed sales found to generate recommendations."

    sku_perf = sales.groupby("sku").agg(
        orders=("sku", "count"),
        revenue=("revenue", "sum"),
        profit=("profit", "sum"),
    ).sort_values("profit", ascending=False).head(20).reset_index()

    geo_perf = sales.groupby("state").agg(
        orders=("sku", "count"),
        revenue=("revenue", "sum"),
        profit=("profit", "sum"),
    ).sort_values("profit", ascending=False).head(20).reset_index()

    returns = df[df["status"] == "returned"].groupby("sku").size().sort_values(ascending=False).head(10)
    cancels = df[df["status"] == "cancelled"].groupby("sku").size().sort_values(ascending=False).head(10)

    total_revenue = sales["revenue"].sum()
    total_orders  = len(df)
    return_rate   = len(df[df["status"] == "returned"]) / max(total_orders, 1) * 100
    cancel_rate   = len(df[df["status"] == "cancelled"]) / max(total_orders, 1) * 100

    prompt = f"""You are a senior ecommerce business growth consultant advising a seller in India who sells jewellery accessories and sunglasses on online marketplaces.

Business snapshot:
- Total monthly revenue: ₹{total_revenue:,.0f}
- Return rate: {return_rate:.1f}%
- Cancellation rate: {cancel_rate:.1f}%
- Categories: Jewellery Sets, Sunglasses

SKU performance (top 20 by profit):
{sku_perf.to_csv(index=False)}

Geography performance (top 20 by profit):
{geo_perf.to_csv(index=False)}

Top returned SKUs:
{returns.to_string()}

Top cancelled SKUs:
{cancels.to_string()}

Based on this data, provide detailed, actionable recommendations structured under the following sections. Be specific — mention SKU names and state names where relevant. Format as Markdown with bold section headers.

## 📈 Revenue Growth Opportunities
What the seller should focus on to increase revenue from existing SKUs and geographies.

## 🌍 New Market Expansion (Geography)
Which Indian states/regions show the most potential to enter next, and why.

## 🛍️ SKU Strategy
Which SKUs to scale up, which to discontinue, and any bundling or pricing suggestions.

## 🔄 Returns & Cancellations Reduction
Specific actions to reduce return and cancellation rates for flagged SKUs.

## 🆕 New Product Categories to Explore
Based on category trends in India's fashion accessories ecommerce market, suggest new product categories the seller can add.

## 🏪 New Marketplace Opportunities
Suggest other Indian or global platforms (beyond current) the seller should consider expanding to.

## ⚡ 30-Day Action Plan
Top 5 concrete actions the seller must take in the next 30 days to improve profitability.
"""

    resp = _AZURE_CLIENT.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=2000,
    )
    return resp.choices[0].message.content


def update_monthly_history(df: pd.DataFrame, additional_expense: float):
    history = load_monthly_history()
    sales_df = df[df["status"] == "sale"].copy()
    if sales_df.empty or "order_date" not in sales_df.columns:
        return history

    sales_df["month"] = sales_df["order_date"].dt.to_period("M").astype(str)
    grouped = sales_df.groupby("month").agg(
        revenue=("revenue", "sum"),
        cp=("cp", "sum"),
        orders=("sku", "count"),
    ).reset_index()

    month_count = max(len(grouped), 1)
    per_month_extra = float(additional_expense) / month_count

    for _, row in grouped.iterrows():
        month = row["month"]
        revenue = float(row["revenue"])
        cp = float(row["cp"])
        cost = cp + per_month_extra
        profit = revenue - cost
        history[month] = {
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "orders": int(row["orders"]),
            "additional_expense": per_month_extra,
            "updated_at": datetime.now().isoformat(),
        }

    save_monthly_history(history)
    return history


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = df.columns.str.strip().str.lower()
    rename = {}
    for raw, clean in COLUMN_MAP.items():
        if raw in df.columns:
            rename[raw] = clean
    df = df.rename(columns=rename)
    if "sku" in df.columns:
        df["sku"] = df["sku"].apply(normalize_sku)
    return df


def _parse_date_series(series: pd.Series) -> pd.Series:
    cleaned = series.copy()
    if cleaned.dtype == object:
        cleaned = cleaned.astype(str).str.strip()
        cleaned = cleaned.replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "0": pd.NA, "0.0": pd.NA, "-": pd.NA, "--": pd.NA})

    numeric = pd.to_numeric(cleaned, errors="coerce")
    looks_like_excel_serial = numeric.notna() & (numeric > 1000)
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    if looks_like_excel_serial.any():
        parsed.loc[looks_like_excel_serial] = pd.to_datetime(
            numeric.loc[looks_like_excel_serial],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    text_mask = numeric.isna()
    if text_mask.any():
        text_source = cleaned.loc[text_mask]
        # Indian reports are typically dd/mm/yyyy, so parse day-first first.
        text_parsed = pd.to_datetime(text_source, errors="coerce", dayfirst=True)
        needs_fallback = text_parsed.isna()
        if needs_fallback.any():
            parsed_fallback = pd.to_datetime(text_source.loc[needs_fallback], errors="coerce", dayfirst=False)
            text_parsed.loc[needs_fallback] = parsed_fallback
        parsed.loc[text_mask] = text_parsed

    return parsed


def parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["order_date", "settlement_due_date", "cancelled_on", "return_date"]:
        if col in df.columns:
            df[col] = _parse_date_series(df[col])
    return df


def classify_orders(df: pd.DataFrame) -> pd.DataFrame:
    """Tag each row as: sale / cancelled / returned."""
    df["status"] = "sale"
    if "cancelled_on" in df.columns:
        cancelled_mask = df["cancelled_on"].notna()
        df.loc[cancelled_mask, "status"] = "cancelled"
    if "return_date" in df.columns:
        return_mask = df["return_date"].notna()
        df.loc[return_mask, "status"] = "returned"
    return df


def _to_numeric_series(series: pd.Series) -> pd.Series:
    if not isinstance(series, pd.Series):
        series = pd.Series([series])
    cleaned = series.astype(str).str.replace(",", "", regex=False)
    cleaned = cleaned.str.replace("₹", "", regex=False)
    cleaned = cleaned.str.replace(" ", "", regex=False)
    cleaned = cleaned.replace({"nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "": pd.NA})
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def _sum_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for column in columns:
        if column in df.columns:
            total = total + _to_numeric_series(df[column])
    return total


def compute_financials(df: pd.DataFrame, sku_cp: dict) -> pd.DataFrame:
    """
    SP = prepaid final settled amount + postpaid final settled amount - deductions
    Revenue (sales only) = SP
    Product cost (sales only) = CP
    Contribution (sales only) = SP - CP
    """
    # Calculate net settled amount by subtracting prepaid/postpaid deductions.
    prepaid = _to_numeric_series(df.get("prepaid_final_amount", 0))
    postpaid = _to_numeric_series(df.get("postpaid_final_amount", 0))
    prepaid_deductions = _sum_numeric_columns(df, PREPAID_DEDUCTION_COLS)
    postpaid_deductions = _sum_numeric_columns(df, POSTPAID_DEDUCTION_COLS)
    deductions_total = prepaid_deductions + postpaid_deductions

    df["gross_settled_amount"] = prepaid + postpaid
    df["gt_charges"] = deductions_total
    df["final_amount"] = df["gross_settled_amount"] - deductions_total
    df["sp"] = df["final_amount"]

    df["cp"] = _to_numeric_series(df["sku"].map(sku_cp).fillna(0))
    df["revenue"] = df.apply(lambda r: r["sp"] if r["status"] == "sale" else 0, axis=1)
    df["profit"] = df.apply(lambda r: (r["sp"] - r["cp"]) if r["status"] == "sale" else 0, axis=1)
    return df


def week_label(dt):
    if pd.isnull(dt):
        return "Unknown"
    return f"W{dt.isocalendar()[1]} ({dt.strftime('%b %Y')})"


def get_upload_token(uploaded_file) -> str:
    data = uploaded_file.getvalue()
    return hashlib.md5(data).hexdigest()


# ── Sidebar ──────────────────────────────────────────────────────────────────

def sidebar(df: pd.DataFrame):
    st.sidebar.header("🔧 Filters & Settings")

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Dashboard Filters")

    article_types = ["All"] + sorted(df["article_type"].dropna().unique().tolist()) if "article_type" in df.columns else ["All"]
    art_filter = st.sidebar.selectbox("Article Type", article_types)

    states = ["All"] + sorted(df["state"].dropna().unique().tolist()) if "state" in df.columns else ["All"]
    state_filter = st.sidebar.selectbox("State / Geography", states)

    time_options = ["Monthly View", "Weekly View"]
    time_filter = st.sidebar.radio("Time Granularity", time_options)

    return art_filter, state_filter, time_filter


def monthly_cost_editor():
    costs = load_monthly_costs()
    with st.sidebar.expander("🧾 Monthly Cost Editor", expanded=False):
        if not costs:
            st.caption("No month-level costs saved yet.")
            return

        months = sorted(costs.keys())
        chosen = st.selectbox("Select Month", months)
        cur = costs.get(chosen, {})

        logistic = st.number_input("Logistic Charges (₹)", min_value=0.0, value=float(cur.get("logistic_cost", 0)), step=100.0)
        ops = st.number_input("Ops Cost (₹)", min_value=0.0, value=float(cur.get("ops_cost", 0)), step=100.0)
        misc = st.number_input("Misc Cost (₹)", min_value=0.0, value=float(cur.get("misc_cost", 0)), step=100.0)

        if st.button("💾 Update Month Costs"):
            costs[chosen] = {
                "logistic_cost": float(logistic),
                "ops_cost": float(ops),
                "misc_cost": float(misc),
                "updated_at": datetime.now().isoformat(),
            }
            save_monthly_costs(costs)
            st.success(f"Updated costs for {chosen}.")


# ── SKU Cost Price Manager ───────────────────────────────────────────────────

def sku_cp_manager(df: pd.DataFrame, sku_cp: dict) -> dict:
    skus_in_file = df["sku"].dropna().unique().tolist()
    missing = [s for s in skus_in_file if s not in sku_cp]

    with st.sidebar.expander("🏷️ Cost Price Setup", expanded=False):
        st.caption("Upload or edit SKU cost prices. Values are stored permanently.")

        cp_uploaded = st.file_uploader(
            "Upload SKU Cost Price File",
            type=["csv", "xlsx"],
            key="cp_file_uploader_sidebar",
            help="File should include SKU and cost price columns.",
        )
        if cp_uploaded is not None:
            sku_cp, cp_msg, cp_msg_type = import_sku_cp_file(cp_uploaded, sku_cp)
            if cp_msg_type == "success":
                st.success(cp_msg)
            else:
                st.error(cp_msg)
            missing = [s for s in skus_in_file if s not in sku_cp]

        if missing:
            st.warning(f"⚠️ {len(missing)} new SKU(s) found without a cost price. Please fill them in below.")

        all_skus = sorted(set(list(sku_cp.keys()) + skus_in_file))
        updated = {}

        cols_per_row = 3
        rows = [all_skus[i:i+cols_per_row] for i in range(0, len(all_skus), cols_per_row)]

        for row in rows:
            cols = st.columns(cols_per_row)
            for col, sku in zip(cols, row):
                default_val = float(sku_cp.get(sku, 0))
                highlight = "🆕 " if sku in missing else ""
                val = col.number_input(
                    f"{highlight}{sku}",
                    min_value=0.0,
                    value=default_val,
                    step=1.0,
                    key=f"cp_{sku}",
                )
                updated[sku] = val

        if st.button("💾 Save Cost Prices"):
            save_sku_cp(updated)
            st.success("Cost prices saved!")
            return updated

        return {**sku_cp, **{s: updated.get(s, sku_cp.get(s, 0)) for s in all_skus}}


# ── Filter helper ────────────────────────────────────────────────────────────

def apply_filters(df, art_filter, state_filter):
    filtered = df.copy()
    if art_filter != "All" and "article_type" in filtered.columns:
        filtered = filtered[filtered["article_type"] == art_filter]
    if state_filter != "All" and "state" in filtered.columns:
        filtered = filtered[filtered["state"] == state_filter]
    return filtered


# ── Pages ────────────────────────────────────────────────────────────────────

def page_overview(df, logistic_cost, ops_cost, misc_cost, commission, time_filter):
    st.header("📈 Revenue, Cost & Profit Overview")
    st.caption("Need to update SKU prices? Use sidebar → Cost Price Setup.")

    view_df = df.copy()
    selected_month = "All"
    if "order_date" in view_df.columns:
        month_vals = sorted(view_df["order_date"].dropna().dt.to_period("M").astype(str).unique().tolist())
        month_options = ["All"] + month_vals
        month_col, _spacer = st.columns([1.6, 8.4])
        with month_col:
            st.caption("Month")
            selected_month = st.selectbox(
                "Month",
                month_options,
                index=len(month_options) - 1,
                key="overview_month_filter",
                label_visibility="collapsed",
            )
        if selected_month != "All":
            view_df = view_df[view_df["order_date"].dt.to_period("M").astype(str) == selected_month].copy()

    sales_df = view_df[view_df["status"] == "sale"].copy()

    has_split_components = (
        "prepaid_final_amount" in view_df.columns
        and "postpaid_final_amount" in view_df.columns
    )

    total_prepaid_settled = _to_numeric_series(view_df["prepaid_final_amount"]).sum() if "prepaid_final_amount" in view_df.columns else None
    total_postpaid_settled = _to_numeric_series(view_df["postpaid_final_amount"]).sum() if "postpaid_final_amount" in view_df.columns else None
    total_prepaid_deductions = _sum_numeric_columns(view_df, PREPAID_DEDUCTION_COLS).sum() if any(c in view_df.columns for c in PREPAID_DEDUCTION_COLS) else None
    total_postpaid_deductions = _sum_numeric_columns(view_df, POSTPAID_DEDUCTION_COLS).sum() if any(c in view_df.columns for c in POSTPAID_DEDUCTION_COLS) else None
    returned_df = view_df[view_df["status"] == "returned"].copy()
    total_return_settled = (
        _to_numeric_series(returned_df["prepaid_final_amount"]).sum() + _to_numeric_series(returned_df["postpaid_final_amount"]).sum()
    ) if ("prepaid_final_amount" in returned_df.columns and "postpaid_final_amount" in returned_df.columns) else _to_numeric_series(returned_df.get("final_amount", 0)).sum()

    total_revenue = sales_df["revenue"].sum()
    revenue_formula_total = (
        total_prepaid_settled
        + total_postpaid_settled
        - total_prepaid_deductions
        - total_postpaid_deductions
        - total_return_settled
    ) if has_split_components and total_prepaid_deductions is not None and total_postpaid_deductions is not None else total_revenue

    total_cp = sales_df["cp"].sum()
    total_cost = total_cp
    total_profit = total_revenue - total_cost
    total_sp = sales_df["sp"].sum()
    total_lom = float(logistic_cost + ops_cost + misc_cost + commission)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Total Revenue (SP)", format_inr(total_revenue))
    c2.metric("🏷️ Total Product Cost (CP)", format_inr(total_cp))
    c3.metric("📦 Logistic + Ops + Misc", format_inr(total_lom))
    c4.metric("✅ Net Profit", format_inr(total_profit), delta=f"{'▲' if total_profit >= 0 else '▼'} {abs(total_profit):,.0f}")

    # Trendline based on the current data selection, not all historical uploads.
    if not sales_df.empty and "order_date" in sales_df.columns:
        trend_source = sales_df.copy()
        trend_source["month"] = trend_source["order_date"].dt.to_period("M").astype(str)
        trend = trend_source.groupby("month").agg(
            Revenue=("revenue", "sum"),
            ProductCost=("cp", "sum"),
        ).reset_index().sort_values("month")

        trend["Cost"] = trend["ProductCost"]
        trend["Profit"] = trend["Revenue"] - trend["Cost"]

        st.subheader("📅 Month-on-Month Trend")
        fig_trend = px.line(
            trend,
            x="month",
            y=["Revenue", "Cost", "Profit"],
            markers=True,
            title="MoM Revenue vs Cost vs Profit",
        )
        for trace in fig_trend.data:
            trace.text = [format_inr_short(v) for v in trace.y]
            trace.textposition = "top center"
            trace.hovertemplate = "%{x}<br>%{fullData.name}: ₹%{y:,.0f}<extra></extra>"
        max_val = trend[["Revenue", "Cost", "Profit"]].to_numpy().max() if not trend.empty else 1
        tick_vals, tick_text = make_lakh_ticks(max_val)
        fig_trend.update_yaxes(tickmode="array", tickvals=tick_vals, ticktext=tick_text)
        fig_trend = style_figure(fig_trend)
        st.plotly_chart(fig_trend, use_container_width=True)

    with st.expander("🧮 Calculation Breakdown", expanded=False):
        if total_prepaid_settled is None or total_postpaid_settled is None:
            st.caption("Split prepaid/postpaid components are unavailable in persisted view. Re-upload the file to see full component-level breakup.")
        else:
            r1c1, r1c2 = st.columns(2)
            r1c1.metric("1. Prepaid Final Settled Amount Total", format_inr(total_prepaid_settled))
            r1c2.metric("2. Postpaid Final Settled Amount Total", format_inr(total_postpaid_settled))

            r2c1, r2c2 = st.columns(2)
            r2c1.metric("3. Sum of All Deductions (Prepaid)", format_inr(total_prepaid_deductions if total_prepaid_deductions is not None else 0))
            r2c2.metric("4. Sum of All Deductions (Postpaid)", format_inr(total_postpaid_deductions if total_postpaid_deductions is not None else 0))

            r3c1, r3c2 = st.columns(2)
            r3c1.metric("5. prepaid_final_amount (raw column sum)", format_inr(total_prepaid_settled))
            r3c2.metric("6. postpaid_final_amount (raw column sum)", format_inr(total_postpaid_settled))

            st.markdown("---")
            prepaid_ded = total_prepaid_deductions if total_prepaid_deductions is not None else 0
            postpaid_ded = total_postpaid_deductions if total_postpaid_deductions is not None else 0
            st.markdown(
                f"**7. Total Revenue = "
                f"{format_inr(total_prepaid_settled)} (Prepaid Settled)"
                f" + {format_inr(total_postpaid_settled)} (Postpaid Settled)"
                f" − {format_inr(prepaid_ded)} (Prepaid Deductions)"
                f" − {format_inr(postpaid_ded)} (Postpaid Deductions)"
                f" − {format_inr(total_return_settled)} (Returns)"
                f" = {format_inr(revenue_formula_total)}**"
            )

    st.markdown("---")

    # Time series
    if "order_date" in sales_df.columns:
        ts = sales_df.copy()
        if time_filter == "Weekly View":
            ts["period"] = ts["order_date"].apply(week_label)
        else:
            ts["period"] = ts["order_date"].dt.to_period("M").astype(str)

        grouped = ts.groupby("period").agg(
            Revenue=("revenue", "sum"),
            CostCP=("cp", "sum"),
            Profit=("profit", "sum"),
        ).reset_index()
        grouped["Cost"] = grouped["CostCP"]
        grouped["Profit"] = grouped["Revenue"] - grouped["Cost"]

        fig = px.bar(
            grouped, x="period", y=["Revenue", "Cost", "Profit"],
            barmode="group", title="Revenue vs Cost vs Profit",
            labels={"value": "₹", "period": "Period"},
            color_discrete_map={"Revenue": "#2196F3", "Cost": "#FF5722", "Profit": "#4CAF50"},
        )
        for trace in fig.data:
            trace.text = [format_inr_short(v) for v in trace.y]
            trace.textposition = "outside"
            trace.hovertemplate = "%{x}<br>%{fullData.name}: ₹%{y:,.0f}<extra></extra>"
        max_grouped = grouped[["Revenue", "Cost", "Profit"]].to_numpy().max() if not grouped.empty else 1
        tick_vals, tick_text = make_lakh_ticks(max_grouped)
        fig.update_yaxes(tickmode="array", tickvals=tick_vals, ticktext=tick_text)
        fig = style_figure(fig)
        st.plotly_chart(fig, use_container_width=True)

    # Article type breakdown
    if "article_type" in sales_df.columns:
        art_group = sales_df.groupby("article_type").agg(
            Revenue=("revenue", "sum"),
            Profit=("profit", "sum"),
            Orders=("sku", "count"),
        ).reset_index()

        col1, col2 = st.columns(2)
        with col1:
            fig2 = px.bar(
                art_group,
                x="article_type",
                y="Revenue",
                title="Revenue by Article Type",
                color="article_type",
                text=art_group["Revenue"].apply(format_inr_short),
            )
            fig2.update_traces(textposition="outside", hovertemplate="%{x}<br>Revenue: ₹%{y:,.0f}<extra></extra>")
            fig2.update_layout(showlegend=False)
            fig2 = style_figure(fig2)
            st.plotly_chart(fig2, use_container_width=True)
        with col2:
            fig3 = px.bar(art_group, x="article_type", y="Profit",
                          title="Profit by Article Type", color="article_type",
                          color_discrete_sequence=px.colors.qualitative.Set2,
                          text=art_group["Profit"].apply(format_inr_short))
            fig3.update_traces(textposition="outside", hovertemplate="%{x}<br>Profit: ₹%{y:,.0f}<extra></extra>")
            fig3.update_layout(showlegend=False)
            fig3 = style_figure(fig3)
            st.plotly_chart(fig3, use_container_width=True)

    # SKU performance table
    st.subheader("🔑 SKU-wise Performance")
    sku_group = sales_df.groupby("sku").agg(
        Orders=("sku", "count"),
        Revenue=("revenue", "sum"),
        TotalCP=("cp", "sum"),
        Profit=("profit", "sum"),
    ).reset_index()
    sku_group["Margin %"] = (sku_group["Profit"] / sku_group["Revenue"].replace(0, 1) * 100).round(1)
    sku_group = sku_group.sort_values("Profit", ascending=False)
    st.dataframe(sku_group.style.format({
        "Revenue": "₹{:,.0f}", "TotalCP": "₹{:,.0f}", "Profit": "₹{:,.0f}", "Margin %": "{:.1f}%"
    }), use_container_width=True)


def page_geography(df):
    st.header("🗺️ Geography Analysis")

    if "state" not in df.columns:
        st.info("No state column found in data.")
        return

    sales_df = df[df["status"] == "sale"]

    state_group = sales_df.groupby("state").agg(
        Orders=("sku", "count"),
        Revenue=("revenue", "sum"),
        Profit=("profit", "sum"),
    ).reset_index().sort_values("Revenue", ascending=False)

    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(state_group.head(15), x="Revenue", y="state", orientation="h",
                     title="Top 15 States by Revenue", color="Revenue",
                     color_continuous_scale="Blues")
        fig = style_figure(fig)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.pie(state_group.head(10), names="state", values="Orders",
                      title="Order Share – Top 10 States", hole=0.35)
        fig2 = style_figure(fig2)
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("State-wise Breakdown")
    st.dataframe(state_group.style.format({
        "Revenue": "₹{:,.0f}", "Profit": "₹{:,.0f}"
    }), use_container_width=True)


def page_returns(df):
    st.header("🔄 Returns Analysis")

    ret_df = df[df["status"] == "returned"].copy()

    if ret_df.empty:
        st.success("No returns found in this dataset.")
        return

    total_returns = len(ret_df)
    lost_revenue  = ret_df["sp"].sum()  # SP since net was never realized
    st.metric("Total Returned Orders", total_returns)
    st.metric("Estimated Lost Revenue (SP)", f"₹{lost_revenue:,.0f}")

    st.markdown("---")
    tabs = st.tabs(["By SKU", "By State", "Timeline"])

    with tabs[0]:
        placed = df.groupby("sku").size().reset_index(name="PlacedOrders")
        sku_ret = ret_df.groupby("sku").size().reset_index(name="Returns")
        sku_ret = placed.merge(sku_ret, on="sku", how="left").fillna(0)
        sku_ret["Returns"] = sku_ret["Returns"].astype(int)
        sku_ret["Return %"] = (sku_ret["Returns"] / sku_ret["PlacedOrders"].replace(0, 1) * 100).round(2)
        sku_ret = sku_ret.sort_values("Returns", ascending=False)

        fig = px.bar(sku_ret.head(30), x="sku", y="Return %", title="Return % by SKU (Returns / Total Placed Orders)",
                     color="Return %", color_continuous_scale="Reds")
        fig = style_figure(fig)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sku_ret, use_container_width=True)

    with tabs[1]:
        if "state" in ret_df.columns:
            state_ret = ret_df.groupby("state").size().reset_index(name="Returns").sort_values("Returns", ascending=False)
            fig2 = px.bar(state_ret.head(15), x="Returns", y="state", orientation="h",
                          title="Returns by State (Top 15)", color="Returns",
                          color_continuous_scale="Oranges")
            fig2 = style_figure(fig2)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No state column available.")

    with tabs[2]:
        if "return_date" in ret_df.columns:
            ret_df["return_month"] = ret_df["return_date"].dt.to_period("M").astype(str)
            timeline = ret_df.groupby("return_month").size().reset_index(name="Returns")
            fig3 = px.line(timeline, x="return_month", y="Returns",
                           title="Return Volume Over Time", markers=True)
            fig3 = style_figure(fig3)
            st.plotly_chart(fig3, use_container_width=True)


def page_cancellations(df):
    st.header("❌ Cancellations Analysis")

    can_df = df[df["status"] == "cancelled"].copy()

    if can_df.empty:
        st.success("No cancellations found in this dataset.")
        return

    total_can = len(can_df)
    lost_rev   = can_df["sp"].sum()
    st.metric("Total Cancelled Orders", total_can)
    st.metric("Potential Revenue Lost", f"₹{lost_rev:,.0f}")

    st.markdown("---")
    tabs = st.tabs(["By Reason", "By SKU", "By State"])

    with tabs[0]:
        if "cancel_reason" in can_df.columns:
            reason_grp = can_df.groupby("cancel_reason").size().reset_index(name="Count").sort_values("Count", ascending=False)
            fig = px.bar(reason_grp, x="Count", y="cancel_reason", orientation="h",
                         title="Cancellations by Reason", color="Count",
                         color_continuous_scale="Purples")
            fig = style_figure(fig)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(reason_grp, use_container_width=True)
        else:
            st.info("No cancellation reason column found.")

    with tabs[1]:
        placed = df.groupby("sku").size().reset_index(name="PlacedOrders")
        sku_can = can_df.groupby("sku").size().reset_index(name="Cancellations")
        sku_can = placed.merge(sku_can, on="sku", how="left").fillna(0)
        sku_can["Cancellations"] = sku_can["Cancellations"].astype(int)
        sku_can["Cancellation %"] = (sku_can["Cancellations"] / sku_can["PlacedOrders"].replace(0, 1) * 100).round(2)
        sku_can = sku_can.sort_values("Cancellations", ascending=False)

        fig2 = px.bar(sku_can.head(30), x="sku", y="Cancellation %", title="Cancellation % by SKU (Cancellations / Total Placed Orders)",
                      color="Cancellation %", color_continuous_scale="Reds")
        fig2 = style_figure(fig2)
        st.plotly_chart(fig2, use_container_width=True)
        st.dataframe(sku_can, use_container_width=True)

    with tabs[2]:
        if "state" in can_df.columns:
            state_can = can_df.groupby("state").size().reset_index(name="Cancellations").sort_values("Cancellations", ascending=False)
            fig3 = px.bar(state_can.head(15), x="Cancellations", y="state", orientation="h",
                          title="Cancellations by State (Top 15)", color="Cancellations",
                          color_continuous_scale="Blues")
            fig3 = style_figure(fig3)
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("No state column available.")


def page_insights(df):
    st.header("💡 Auto Insights")

    sales = df[df["status"] == "sale"]
    cancelled = df[df["status"] == "cancelled"]
    returned  = df[df["status"] == "returned"]

    total_orders = len(df)
    cancel_rate  = len(cancelled) / total_orders * 100 if total_orders else 0
    return_rate  = len(returned) / total_orders * 100 if total_orders else 0
    sale_rate    = len(sales) / total_orders * 100 if total_orders else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Sale Rate", f"{sale_rate:.1f}%")
    c2.metric("Cancellation Rate", f"{cancel_rate:.1f}%", delta=f"{cancel_rate:.1f}% of orders", delta_color="inverse")
    c3.metric("Return Rate", f"{return_rate:.1f}%", delta=f"{return_rate:.1f}% of orders", delta_color="inverse")

    fig = px.pie(
        values=[len(sales), len(cancelled), len(returned)],
        names=["Sales", "Cancellations", "Returns"],
        title="Order Outcome Breakdown",
        color_discrete_map={"Sales": "#4CAF50", "Cancellations": "#F44336", "Returns": "#FF9800"},
        hole=0.4,
    )
    fig = style_figure(fig)
    st.plotly_chart(fig, use_container_width=True)

    # Top performing SKU
    if not sales.empty:
        top_sku = sales.groupby("sku")["profit"].sum().idxmax()
        top_profit = sales.groupby("sku")["profit"].sum().max()
        st.success(f"🏆 Best performing SKU: **{top_sku}** with ₹{top_profit:,.0f} net profit")

        worst_sku = sales.groupby("sku")["profit"].sum().idxmin()
        worst_profit = sales.groupby("sku")["profit"].sum().min()
        if worst_profit < 0:
            st.error(f"⚠️ Loss-making SKU: **{worst_sku}** with ₹{worst_profit:,.0f} net profit — review pricing/CP")
        else:
            st.info(f"📉 Lowest profit SKU: **{worst_sku}** with ₹{worst_profit:,.0f}")

    # High cancellation SKUs
    if not cancelled.empty:
        high_can = cancelled.groupby("sku").size().sort_values(ascending=False).head(3)
        st.warning(f"🔴 SKUs with most cancellations: {', '.join([f'{s} ({n})' for s, n in high_can.items()])}")

    # State with most returns
    if not returned.empty and "state" in returned.columns:
        top_ret_state = returned.groupby("state").size().idxmax()
        st.warning(f"📍 State with most returns: **{top_ret_state}**")


def page_ai_recommendations(df, report_key: str):
    st.header("🤖 AI-Powered Business Recommendations")
    st.caption("Auto-generated insights powered by Azure OpenAI based on your uploaded month's data.")

    cache_key = f"ai_advice_{report_key}"
    if cache_key not in st.session_state:
        with st.spinner("Analysing your data and generating recommendations..."):
            st.session_state[cache_key] = get_ai_recommendations(df)

    st.markdown(st.session_state[cache_key])

    if st.button("🔄 Regenerate Recommendations"):
        del st.session_state[cache_key]
        st.rerun()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    apply_custom_theme()
    st.title("📦 Rozetta Craft Monthly Revenue Dashboard")
    st.caption("Upload your monthly Myntra report (CSV/XLSX) to get started.")

    if get_supabase_client() is not None:
        st.caption("Storage: Supabase")
    else:
        st.caption("Storage: Local fallback")

    sku_cp = load_sku_cp()

    # ── File uploader lives in the sidebar so it's always accessible ─────────
    with st.sidebar:
        st.markdown("---")
        with st.expander("📂 Upload New Monthly Report", expanded=False):
            uploaded = st.file_uploader("Upload Monthly Report (CSV/XLSX)", type=["csv", "xlsx"], key="main_report_uploader")

    # ── If no file uploaded, try loading persisted data from Supabase/local ──
    if not uploaded:
        stored_df = load_uploaded_orders()
        if stored_df.empty:
            st.info("👆 Use the **'Upload New Monthly Report'** section in the sidebar to get started.")
            st.markdown("### Onboarding")
            st.markdown("1. Upload monthly order CSV/XLSX via the sidebar")
            st.markdown("2. Use sidebar to upload/edit SKU cost prices")
            st.markdown("3. Confirm Logistic/Ops/Misc costs on dashboard screen")
            return

        # Avoid mixing historical uploads: default to active/latest upload token.
        if "upload_token" in stored_df.columns:
            active_token = str(st.session_state.get("active_upload_token", "")).strip()
            chosen_token = ""
            token_series = stored_df["upload_token"].astype(str)
            if active_token and (token_series == active_token).any():
                chosen_token = active_token
            else:
                latest_per_token = (
                    stored_df.dropna(subset=["order_date"]) 
                    .groupby("upload_token")["order_date"]
                    .max()
                    .sort_values()
                )
                if not latest_per_token.empty:
                    chosen_token = str(latest_per_token.index[-1])
            if chosen_token:
                stored_df = stored_df[token_series == chosen_token].copy()
                st.caption("Showing latest uploaded report by default.")

        # Use persisted data — financials already computed, costs from Supabase
        df = stored_df.copy()
        logistic_cost = float(df["gt_charges"].sum()) if "gt_charges" in df.columns else 0.0
        ops_cost = 0.0
        misc_cost = 0.0
        commission = 0.0

        art_filter, state_filter, time_filter = sidebar(df)
        sku_cp = sku_cp_manager(df, sku_cp)
        filtered = apply_filters(df, art_filter, state_filter)

        pages = {
            "📈 Overview": lambda: page_overview(filtered, logistic_cost, ops_cost, misc_cost, commission, time_filter),
            "🗺️ Geography": lambda: page_geography(filtered),
            "🔄 Returns": lambda: page_returns(filtered),
            "❌ Cancellations": lambda: page_cancellations(filtered),
            "💡 Insights": lambda: page_insights(filtered),
            "🤖 AI Recommendations": lambda: page_ai_recommendations(df, "stored"),
        }
        selection = st.sidebar.radio("Navigate", list(pages.keys()), key="nav_stored")
        pages[selection]()
        return

    # ── Load & process uploaded file ──────────────────────────────────────────
    try:
        df_raw = read_monthly_report(uploaded)
    except Exception as e:
        st.error(f"Invalid file format. Please upload CSV/XLSX Myntra report. Details: {e}")
        st.stop()

    is_valid, validation_msg = validate_input_file_columns(df_raw)
    if not is_valid:
        st.error(validation_msg)
        st.stop()

    df = normalise_columns(df_raw.copy())
    df = parse_dates(df)
    df = classify_orders(df)

    # ── Mandatory monthly inputs on dashboard (per uploaded sheet) ───────────
    upload_token = get_upload_token(uploaded)
    st.session_state["active_upload_token"] = upload_token
    cfg_key = f"monthly_cfg_{upload_token}"
    if cfg_key not in st.session_state:
        st.session_state[cfg_key] = {
            "logistic_cost": 0.0,
            "analysis_period": "",
            "confirmed": False,
        }

    cfg = st.session_state[cfg_key]
    if not cfg.get("confirmed", False):
        st.subheader("🧾 Confirm Monthly Inputs")
        st.caption("Select the report month/year manually, then confirm the monthly costs for this upload.")

        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        year_options = [str(y) for y in range(datetime.now().year - 2, datetime.now().year + 2)]

        default_period = cfg.get("analysis_period", "")
        if default_period and "-" in default_period:
            default_year, default_month = default_period.split("-", 1)
        else:
            default_year, default_month = str(datetime.now().year), f"{datetime.now().month:02d}"

        year_idx = year_options.index(default_year) if default_year in year_options else len(year_options) - 1
        month_idx = int(default_month) - 1 if default_month.isdigit() and 1 <= int(default_month) <= 12 else datetime.now().month - 1

        with st.form(f"monthly_input_form_{upload_token}"):
            ycol, mcol = st.columns(2)
            selected_year = ycol.selectbox("Year", year_options, index=year_idx)
            selected_month_name = mcol.selectbox("Month", month_names, index=month_idx)

            analysis_period_input = f"{selected_year}-{month_names.index(selected_month_name) + 1:02d}"

            logistic_input = st.number_input(
                "📦 Logistic + Ops + Misc (₹)",
                min_value=0.0,
                value=float(cfg.get("logistic_cost", 0.0)),
                step=100.0,
                help="Leave 0 to use the deduction columns from the selected month, or enter your own value.",
            )
            proceed = st.form_submit_button("✅ Confirm Values and Proceed", type="primary")

        if proceed:
            st.session_state[cfg_key] = {
                "logistic_cost": float(logistic_input),
                "analysis_period": analysis_period_input,
                "confirmed": True,
            }
            st.rerun()

        st.info("Please confirm these monthly values to proceed with dashboard analysis.")
        st.stop()

    logistic_cost = float(st.session_state[cfg_key]["logistic_cost"])
    analysis_period = st.session_state[cfg_key].get("analysis_period", "")

    if analysis_period and "-" in analysis_period and "settlement_due_date" in df.columns:
        df = df[df["settlement_due_date"].dt.to_period("M").astype(str) == analysis_period].copy()
        if not df.empty:
            df["order_date"] = df["settlement_due_date"]

    deduction_total = float((_sum_numeric_columns(df, PREPAID_DEDUCTION_COLS) + _sum_numeric_columns(df, POSTPAID_DEDUCTION_COLS)).sum())
    if logistic_cost == 0:
        logistic_cost = deduction_total
    ops_cost = 0.0
    misc_cost = 0.0
    commission = 0.0

    if df.empty:
        st.error("No rows available for the selected analysis month. Please edit monthly inputs and choose the correct month/year.")
        st.stop()

    with st.expander("🧾 Monthly Inputs (Current Upload)", expanded=False):
        st.write(f"Logistic Charges: {format_inr(deduction_total)}")
        st.write(f"Ops Cost: {format_inr(ops_cost)}")
        st.write(f"Misc Cost: {format_inr(misc_cost)}")
        st.write(f"Commission: {format_inr(commission)}")
        if analysis_period:
            st.write(f"Analysis Month: {analysis_period}")
        if st.button("Edit Monthly Inputs", key=f"edit_monthly_inputs_{upload_token}"):
            st.session_state[cfg_key]["confirmed"] = False
            st.rerun()

    # ── Collect inputs ────────────────────────────────────────────────────────
    art_filter, state_filter, time_filter = sidebar(df)
    sku_cp = sku_cp_manager(df, sku_cp)

    # If any SKU is missing CP, ask directly on dashboard with a CTA
    skus_in_file = df["sku"].dropna().unique().tolist()
    missing_cp = [s for s in skus_in_file if sku_cp.get(s, 0) == 0]
    if missing_cp:
        st.error("⚠️ Some SKUs are missing cost price. Please enter values below to proceed.")
        st.caption("This is required for accurate revenue/profit calculation for the current upload.")

        cols_per_row = 3
        rows = [missing_cp[i:i+cols_per_row] for i in range(0, len(missing_cp), cols_per_row)]

        for row in rows:
            cols = st.columns(cols_per_row)
            for col, sku in zip(cols, row):
                col.number_input(
                    f"Cost Price for {sku}",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key=f"missing_cp_{sku}",
                )

        if st.button("✅ Confirm and Proceed", type="primary"):
            # Fetch values from session state (where Streamlit stores input values)
            entered_cp = {s: st.session_state.get(f"missing_cp_{s}", 0) for s in missing_cp}
            unresolved = [s for s in missing_cp if entered_cp.get(s, 0) == 0]
            if unresolved:
                st.warning(f"Please enter cost price for: {', '.join(unresolved)}")
                st.stop()

            sku_cp.update({s: float(v) for s, v in entered_cp.items()})
            save_sku_cp(sku_cp)
            st.success("Cost prices saved. Recalculating dashboard...")
            st.rerun()

        st.stop()

    df = compute_financials(df, sku_cp)
    persist_uploaded_orders(df, upload_token)
    filtered = apply_filters(df, art_filter, state_filter)

    # ── Navigation ────────────────────────────────────────────────────────────
    pages = {
        "📈 Overview": lambda: page_overview(filtered, logistic_cost, ops_cost, misc_cost, commission, time_filter),
        "🗺️ Geography": lambda: page_geography(filtered),
        "🔄 Returns": lambda: page_returns(filtered),
        "❌ Cancellations": lambda: page_cancellations(filtered),
        "💡 Insights": lambda: page_insights(filtered),
        "🤖 AI Recommendations": lambda: page_ai_recommendations(df, upload_token),
    }

    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Pages")
    selected = st.sidebar.radio("Navigate to", list(pages.keys()), label_visibility="collapsed")

    pages[selected]()

    # ── Raw data viewer ───────────────────────────────────────────────────────
    with st.expander("🗂️ View Raw Processed Data"):
        st.dataframe(filtered, use_container_width=True)
        csv_out = filtered.to_csv(index=False).encode()
        st.download_button("⬇️ Download Processed CSV", csv_out, "processed_report.csv", "text/csv")


if __name__ == "__main__":
    main()
