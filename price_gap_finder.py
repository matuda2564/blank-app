import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import keepa  # type: ignore
except Exception as import_error:
    print(f"[WARN] keepa import failed: {import_error}")
    keepa = None  # type: ignore


# =========================
# ユーザー設定（閾値・ファイル名など）
# =========================
MIN_ROI_PCT: float = 20.0
MIN_PROFIT_JPY: float = 500.0

INPUT_FILENAME: str = "costco_allcategories_20pages_with_asin.csv"
OUTPUT_ALL_PREFIX: str = "price_gap_allrows"
OUTPUT_CAND_PREFIX: str = "price_gap_candidates"
OUTPUT_DIAG_PREFIX: str = "price_gap_diagnostics"

RATE_LIMIT_SLEEP_SECONDS: int = 25
PROGRESS_EVERY: int = 50

EXPECTED_COLUMNS: List[str] = [
    "カテゴリ",
    "商品名",
    "ASIN",
    "商品URL",
    "Costco価格",
    "Amazon推定売価",
    "粗利",
    "ROI(%)",
    "価格ソース",
    "異常値フラグ",
    "除外理由",
]


def get_desktop_dir() -> str:
    user_profile = os.environ.get("USERPROFILE", "")
    if not user_profile:
        return os.path.expanduser("~/OneDrive/Desktop")
    return os.path.join(user_profile, "OneDrive", "Desktop")


def get_input_path() -> str:
    return os.path.join(get_desktop_dir(), INPUT_FILENAME)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_output_path(prefix: str) -> str:
    return os.path.join(get_desktop_dir(), f"{prefix}_{timestamp()}.csv")


def to_num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return float(value)
    try:
        text = str(value)
    except Exception:
        return None
    text = text.strip()
    if text == "":
        return None
    cleaned = (
        text.replace(",", "")
        .replace(" ", "")
        .replace("\t", "")
        .replace("円", "")
        .replace("￥", "")
        .replace("¥", "")
    )
    num = pd.to_numeric(cleaned, errors="coerce")
    if pd.isna(num):
        return None
    return float(num)


def is_plausible(price: Optional[float]) -> bool:
    if price is None or pd.isna(price):
        return False
    return 0.0 < float(price) <= 1_000_000.0


def is_anomalous(amazon_price: Optional[float], costco_price: Optional[float]) -> bool:
    if amazon_price is None or pd.isna(amazon_price):
        return False
    if not is_plausible(amazon_price):
        return True
    if costco_price is None or pd.isna(costco_price):
        return False
    if float(costco_price) <= 0.0:
        return False
    ratio = float(amazon_price) / float(costco_price)
    if ratio > 30.0:
        return True
    if ratio < 0.30:
        return True
    return False


def as_keepa_currency(value: Optional[Any]) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    if v <= 0:
        return None
    return v / 100.0


def choose_latest_price(product: Optional[Dict[str, Any]]) -> Tuple[Optional[float], str, str]:
    if product is None:
        return None, "", "no_product"
    stats = product.get("stats", {}) if isinstance(product, dict) else {}
    current = stats.get("current", {}) if isinstance(stats, dict) else {}
    if not isinstance(current, dict) or not current:
        return None, "", "no_current_price"
    for key, label in (
        ("amazon", "Keepa.stats.amazon"),
        ("buyBox", "Keepa.stats.buyBox"),
        ("new", "Keepa.stats.new"),
    ):
        price = as_keepa_currency(current.get(key))
        if price is not None:
            return price, label, ""
    return None, "", "no_current_price"


def is_rate_limit_error(message: str) -> bool:
    m = message.lower()
    tokens = ["429", "token", "empty", "exhaust", "rate", "limit", "quota"]
    return any(t in m for t in tokens)


def fetch_product_with_retry(api: Any, asin: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        products = api.query(asin, history=True)
        if not products:
            return None, "no_product"
        return products[0], None
    except Exception as e:
        msg = str(e)
        if is_rate_limit_error(msg):
            print(f"[WARN] 429 suspected for ASIN {asin}. Waiting {RATE_LIMIT_SLEEP_SECONDS}s before retry...")
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)
            try:
                products = api.query(asin, history=True)
                if not products:
                    return None, "no_product"
                return products[0], None
            except Exception as e2:
                return None, f"error:{e2}"
        return None, f"error:{e}"


def to_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.Series([None] * len(df))
    df = df[EXPECTED_COLUMNS]
    numeric_cols = ["Costco価格", "Amazon推定売価", "粗利", "ROI(%)"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "異常値フラグ" in df.columns:
        df["異常値フラグ"] = df["異常値フラグ"].fillna(False).astype(bool)
    for text_col in ["カテゴリ", "商品名", "ASIN", "商品URL", "価格ソース", "除外理由"]:
        df[text_col] = df[text_col].fillna("").astype(str)
    return df


def main() -> None:
    desktop_dir = get_desktop_dir()
    input_path = get_input_path()
    print(f"[INFO] Input CSV: {input_path}")

    try:
        input_df = pd.read_csv(input_path, encoding="utf-8")
    except UnicodeDecodeError:
        input_df = pd.read_csv(input_path, encoding="utf-8-sig")

    api_key = os.environ.get("KEEPA_API_KEY", "").strip()
    api: Any = None
    if not api_key:
        print("[WARN] KEEPA_API_KEY not set. Keepa queries will fail.")
    if keepa is not None and api_key:
        try:
            api = keepa.Keepa(api_key)
        except Exception as e:
            print(f"[WARN] Keepa init failed: {e}")
            api = None

    total = len(input_df)
    print(f"[INFO] Rows to process: {total}")

    rows: List[Dict[str, Any]] = []

    for idx, row in input_df.iterrows():
        category = str(row.get("カテゴリ", "")) if pd.notna(row.get("カテゴリ", "")) else ""
        title = str(row.get("商品名", "")) if pd.notna(row.get("商品名", "")) else ""
        asin = str(row.get("ASIN", "")) if pd.notna(row.get("ASIN", "")) else ""
        url = str(row.get("商品URL", "")) if pd.notna(row.get("商品URL", "")) else ""
        costco_price = to_num(row.get("Costco価格", None))

        amazon_price: Optional[float] = None
        price_source: str = ""
        reason: str = ""

        if asin == "":
            amazon_price = None
            reason = "missing_asin"
        elif api is None:
            amazon_price = None
            if not api_key:
                reason = "error:missing_keepa_api_key"
            else:
                reason = "error:keepa_init_failed"
        else:
            product, err = fetch_product_with_retry(api, asin)
            if err:
                amazon_price = None
                reason = err
            else:
                price, source, pick_reason = choose_latest_price(product)
                amazon_price = price
                price_source = source
                if pick_reason:
                    reason = pick_reason

        profit: Optional[float]
        roi_pct: Optional[float]
        if amazon_price is None or pd.isna(amazon_price) or costco_price is None or pd.isna(costco_price):
            profit = None
            roi_pct = None
        else:
            profit = float(amazon_price) - float(costco_price)
            if float(costco_price) == 0.0:
                roi_pct = None
            else:
                roi_pct = (profit / float(costco_price)) * 100.0

        anomalous = is_anomalous(amazon_price, costco_price)

        rows.append(
            {
                "カテゴリ": category,
                "商品名": title,
                "ASIN": asin,
                "商品URL": url,
                "Costco価格": costco_price,
                "Amazon推定売価": amazon_price,
                "粗利": profit,
                "ROI(%)": roi_pct,
                "価格ソース": price_source,
                "異常値フラグ": anomalous,
                "除外理由": reason,
            }
        )

        i = idx + 1
        if i % PROGRESS_EVERY == 0 or i == total:
            print(f"[INFO] 進捗: {i}/{total}")

    allrows_df = to_df(rows)

    candidates_mask = (
        allrows_df["Amazon推定売価"].notna()
        & allrows_df["粗利"].notna()
        & allrows_df["ROI(%)"].notna()
        & (~allrows_df["異常値フラグ"].astype(bool))
        & (allrows_df["ROI(%)"] >= MIN_ROI_PCT)
        & (allrows_df["粗利"] >= MIN_PROFIT_JPY)
    )
    candidates_df = allrows_df[candidates_mask].copy()

    diag_mask = (allrows_df["除外理由"].astype(str) != "") | (allrows_df["異常値フラグ"].astype(bool))
    diagnostics_df = allrows_df[diag_mask].copy()

    os.makedirs(desktop_dir, exist_ok=True)
    ts = timestamp()
    out_all = os.path.join(desktop_dir, f"{OUTPUT_ALL_PREFIX}_{ts}.csv")
    out_cand = os.path.join(desktop_dir, f"{OUTPUT_CAND_PREFIX}_{ts}.csv")
    out_diag = os.path.join(desktop_dir, f"{OUTPUT_DIAG_PREFIX}_{ts}.csv")

    allrows_df.to_csv(out_all, index=False, encoding="utf-8-sig")
    candidates_df.to_csv(out_cand, index=False, encoding="utf-8-sig")
    diagnostics_df.to_csv(out_diag, index=False, encoding="utf-8-sig")

    print(
        f"[DONE] allrows: {len(allrows_df)} / candidates: {len(candidates_df)} / diagnostics: {len(diagnostics_df)}"
    )
    print(f"[DONE] Saved: {out_all}")
    print(f"[DONE] Saved: {out_cand}")
    print(f"[DONE] Saved: {out_diag}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] Unhandled error: {e}")
        sys.exit(1)

