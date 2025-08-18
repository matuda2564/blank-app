import argparse
import os
import sys
import time
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

import pandas as pd


# ------------------------------
# Configurables (can be overridden via CLI)
# ------------------------------
DEFAULT_INPUT_FILENAME = "costco_allcategories_20pages_with_asin.csv"
DEFAULT_DESKTOP_SUBDIR = Path("OneDrive") / "Desktop"

# If True, divide Keepa prices by 100 (for APIs that return JPY in cents)
KEEPA_PRICE_DIV100_DEFAULT = False

# Seconds to sleep and retry once when rate-limit-like errors are detected
RATE_LIMIT_SLEEP_SECONDS_DEFAULT = 25


# ------------------------------
# Utilities
# ------------------------------
def normalize_text(text: str) -> str:
    if text is None:
        return ""
    # Normalize to NFKC, lowercase, strip spaces (including full-width)
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = s.replace(" ", "").replace("\u3000", "")
    return s


def to_num(value: Any) -> float:
    """Convert various price-like strings into float.
    - Removes commas/spaces/currency symbols like 円/¥/￥
    - Accepts strings like "￥2,980 税込"
    - Returns math.nan on failure
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan

    # Already numeric
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return math.nan

    # Normalize wide chars
    s = unicodedata.normalize("NFKC", str(value))
    # Remove currency/words
    s = s.replace("円", "").replace("¥", "").replace("￥", "")
    s = s.replace("税込", "").replace("税別", "").replace("税抜", "")
    # Keep only first numeric pattern
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return math.nan
    num = m.group(0).replace(",", "")
    try:
        return float(num)
    except Exception:
        return math.nan


def get_desktop_path() -> Path:
    # Prefer %USERPROFILE%\OneDrive\Desktop (Windows default)
    # Fall back to ~/OneDrive/Desktop
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidate = Path(userprofile) / DEFAULT_DESKTOP_SUBDIR
        if candidate.exists():
            return candidate
    # Fallback
    home = Path.home()
    candidate = home / DEFAULT_DESKTOP_SUBDIR
    if candidate.exists():
        return candidate
    # Last resort: home/Desktop
    return home / "Desktop"


def resolve_input_path(cli_input: Optional[str]) -> Path:
    if cli_input:
        # Expand environment variables and user (~)
        expanded = os.path.expandvars(cli_input)
        expanded = os.path.expanduser(expanded)
        return Path(expanded)

    # Default path: %USERPROFILE%\OneDrive\Desktop\<filename>
    base = get_desktop_path()
    return base / DEFAULT_INPUT_FILENAME


def detect_costco_price_column(df: pd.DataFrame) -> Optional[str]:
    columns = list(df.columns)
    norm_to_original: Dict[str, str] = {normalize_text(c): c for c in columns}

    # Priority exact matches
    priority = [
        "costco価格",
        "コストコ価格",
        "costcoprice",
        "価格",
        "原価",
        "仕入価格",
        "仕入れ価格",
    ]
    for key in priority:
        if key in norm_to_original:
            return norm_to_original[key]

    # Heuristic: contains "costco" and any of these tokens
    tokens = ["価", "price", "値", "原価", "仕入"]
    for c in columns:
        nc = normalize_text(c)
        if "costco" in nc and any(tok in nc for tok in tokens):
            return c

    # Fallback: use "価格元テキスト" if present
    if "価格元テキスト" in columns:
        return "価格元テキスト"

    # Last fallback: if "価格" exists
    if "価格" in columns:
        return "価格"

    return None


def detect_asin_column(df: pd.DataFrame) -> Optional[str]:
    columns = list(df.columns)
    norm_to_original: Dict[str, str] = {normalize_text(c): c for c in columns}
    if "asin" in norm_to_original:
        return norm_to_original["asin"]
    # Common variants
    for c in columns:
        nc = normalize_text(c)
        if nc in {"asin", "商品asin"}:
            return c
    # Direct exact if exists
    if "ASIN" in columns:
        return "ASIN"
    return None


def is_rate_limit_error_message(msg: str) -> bool:
    if not msg:
        return False
    m = msg.lower()
    keywords = ["429", "token", "quota", "rate", "exhaust"]
    return any(k in m for k in keywords)


def fetch_keepa_latest_price(
    api,
    asin: str,
    div100: bool,
    rate_limit_sleep_seconds: int,
) -> Tuple[Optional[float], str, Optional[str]]:
    """Return (price, price_source, error_message).
    price_source in {"amazon", "buyBox", "new", ""}
    error_message is None if successful.
    """
    from keepa import Keepa  # type: ignore  # ensure import error surfaces clearly later if missing

    def _query_once() -> Tuple[Optional[float], str]:
        result = api.query(asin, history=True)
        # API returns a list of product dicts
        product = None
        if isinstance(result, list) and result:
            product = result[0]
        elif isinstance(result, dict) and result.get("products"):
            prods = result.get("products")
            if isinstance(prods, list) and prods:
                product = prods[0]

        if not product:
            return None, ""

        stats = product.get("stats", {}) if isinstance(product, dict) else {}
        current = stats.get("current", {}) if isinstance(stats, dict) else {}

        price_source_order = ["amazon", "buyBox", "new"]
        for src in price_source_order:
            val = current.get(src)
            if val is not None:
                try:
                    price_val = float(val)
                except Exception:
                    continue
                if div100:
                    price_val = price_val / 100.0
                if price_val is not None and price_val > 0:
                    return price_val, src
        return None, ""

    try:
        price, src = _query_once()
        if price is not None and price > 0:
            return price, src, None
        # No price found but no exception
        return None, src, "Keepa価格なし"
    except Exception as e:
        msg = str(e)
        if is_rate_limit_error_message(msg):
            print(f"[INFO] Keepaレート制限検知。{rate_limit_sleep_seconds}秒待機して再試行します: ASIN={asin}")
            time.sleep(rate_limit_sleep_seconds)
            try:
                price, src = _query_once()
                if price is not None and price > 0:
                    return price, src, None
                return None, src, "再試行後もKeepa価格なし"
            except Exception as e2:
                return None, "", f"再試行失敗: {str(e2)}"
        else:
            return None, "", msg


def build_output_paths() -> Tuple[Path, Path, Path]:
    desktop = get_desktop_path()
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    allrows = desktop / f"price_gap_allrows_{now_str}.csv"
    candidates = desktop / f"price_gap_candidates_{now_str}.csv"
    diagnostics = desktop / f"price_gap_diagnostics_{now_str}.csv"
    return allrows, candidates, diagnostics


def ensure_columns(df: pd.DataFrame, required_cols: list) -> pd.DataFrame:
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
    return df[required_cols]


def main():
    parser = argparse.ArgumentParser(description="Costco vs Keepa price gap finder")
    parser.add_argument("--input", dest="input_path", help="Input CSV path. Defaults to OneDrive/Desktop standard path.")
    parser.add_argument("--div100", action="store_true", help="Divide Keepa price by 100 (JPY cents to JPY)")
    parser.add_argument("--sleep-on-429", type=int, default=RATE_LIMIT_SLEEP_SECONDS_DEFAULT, help="Seconds to sleep on 429-like errors before one retry")
    args = parser.parse_args()

    # Resolve paths and flags
    input_csv_path = resolve_input_path(args.input_path)
    div100 = bool(args.div100) if args.div100 is not None else KEEPA_PRICE_DIV100_DEFAULT
    rate_limit_sleep_seconds = int(args.sleep_on_429)

    print(f"[INFO] 入力CSV: {input_csv_path}")
    if not input_csv_path.exists():
        print(f"[FATAL] 入力CSVが見つかりません: {input_csv_path}")
        sys.exit(1)

    # Read CSV
    try:
        df = pd.read_csv(input_csv_path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Retry with default encoding
        df = pd.read_csv(input_csv_path)

    # Detect and rename ASIN column
    asin_col = detect_asin_column(df)
    if not asin_col:
        print("[FATAL] ASIN列が見つかりません。列名に 'ASIN' が必要です。")
        sys.exit(1)
    if asin_col != "ASIN":
        df = df.rename(columns={asin_col: "ASIN"})

    # Detect Costco price column per rules
    costco_col = detect_costco_price_column(df)
    if not costco_col:
        print("[FATAL] Costco価格列を特定できませんでした（例: '価格' や '価格元テキスト'）。")
        sys.exit(1)
    if costco_col != "Costco価格":
        df = df.rename(columns={costco_col: "Costco価格"})

    # Prepare numeric Costco price
    df["Costco価格"] = df["Costco価格"].apply(to_num)

    # Prepare optional columns that might be missing in input
    for base_col in ["カテゴリ", "商品名", "商品URL"]:
        if base_col not in df.columns:
            df[base_col] = ""

    # Initialize result columns
    df["Amazon推定売価"] = math.nan
    df["価格ソース"] = ""
    df["粗利"] = math.nan
    df["ROI(%)"] = math.nan
    df["異常値フラグ"] = False
    df["除外理由"] = ""

    # Keepa API setup (only if there are ASINs to query)
    api_key = os.environ.get("KEEPA_API_KEY")
    if not api_key:
        print("[FATAL] 環境変数 KEEPA_API_KEY が設定されていません。")
        sys.exit(1)

    try:
        from keepa import Keepa  # type: ignore
    except Exception as e:
        print(f"[FATAL] keepa ライブラリのインポートに失敗しました: {e}")
        sys.exit(1)

    try:
        api = Keepa(api_key)
    except Exception as e:
        print(f"[FATAL] Keepa API 初期化に失敗しました: {e}")
        sys.exit(1)

    # Fetch prices with de-duplication
    asin_series = df["ASIN"].astype(str).fillna("").str.strip()
    unique_asins = sorted({a for a in asin_series.tolist() if a})
    asin_to_price: Dict[str, Tuple[Optional[float], str, Optional[str]]] = {}

    total = len(unique_asins)
    for i, asin in enumerate(unique_asins):
        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"[INFO] 進捗: {i + 1}/{total}")
        price, src, err = fetch_keepa_latest_price(api, asin, div100=div100, rate_limit_sleep_seconds=rate_limit_sleep_seconds)
        asin_to_price[asin] = (price, src, err)

    if div100:
        print("[INFO] Keepa価格を1/100スケールで解釈しました（/100を適用）。")

    # Compute metrics row-wise
    def compute_row(row: pd.Series) -> pd.Series:
        asin = str(row.get("ASIN", "")).strip()
        costco_price = row.get("Costco価格", math.nan)
        price = math.nan
        src = ""
        reason = ""
        abnormal = False

        if asin and asin in asin_to_price:
            p, s, err = asin_to_price[asin]
            src = s or ""
            if err:
                reason = err
            if p is not None:
                price = p

        # Validate Keepa price
        if not (isinstance(price, (int, float)) and price > 0 and price <= 1_000_000):
            abnormal = abnormal or True
            if not reason:
                reason = "Keepa価格無効"

        # Ratio check if Costco price > 0
        if isinstance(costco_price, (int, float)) and costco_price > 0 and isinstance(price, (int, float)) and price > 0:
            ratio = price / costco_price
            if ratio > 30 or ratio < 0.3:
                abnormal = True

        gross = math.nan
        roi = math.nan
        if isinstance(costco_price, (int, float)) and isinstance(price, (int, float)):
            if not math.isnan(costco_price) and not math.isnan(price):
                gross = price - costco_price
                if costco_price > 0:
                    roi = (gross / costco_price) * 100.0

        row["Amazon推定売価"] = price
        row["価格ソース"] = src
        row["粗利"] = gross
        row["ROI(%)"] = roi
        row["異常値フラグ"] = bool(abnormal)
        row["除外理由"] = reason
        return row

    df = df.apply(compute_row, axis=1)

    # Build outputs
    required_cols = [
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

    allrows_df = ensure_columns(df.copy(), required_cols)

    # Candidates filter: ROI >= 20 and 粗利 >= 500 and not abnormal
    def meets_candidate(row: pd.Series) -> bool:
        roi = row.get("ROI(%)", math.nan)
        gross = row.get("粗利", math.nan)
        abnormal = bool(row.get("異常値フラグ", False))
        try:
            return (not abnormal) and (isinstance(roi, (int, float)) and not math.isnan(roi) and roi >= 20) and (
                isinstance(gross, (int, float)) and not math.isnan(gross) and gross >= 500
            )
        except Exception:
            return False

    candidates_df = allrows_df[allrows_df.apply(meets_candidate, axis=1)].copy()

    # Diagnostics: rows with reason or abnormal
    diagnostics_df = allrows_df[(allrows_df["除外理由"].astype(str) != "") | (allrows_df["異常値フラグ"].astype(bool))].copy()

    # Output paths
    out_allrows, out_candidates, out_diagnostics = build_output_paths()

    # Save CSVs
    out_allrows.parent.mkdir(parents=True, exist_ok=True)
    allrows_df.to_csv(out_allrows, encoding="utf-8-sig", index=False)
    print(f"[INFO] 保存: {out_allrows}")

    candidates_df.to_csv(out_candidates, encoding="utf-8-sig", index=False)
    print(f"[INFO] 保存: {out_candidates}")

    diagnostics_df.to_csv(out_diagnostics, encoding="utf-8-sig", index=False)
    print(f"[INFO] 保存: {out_diagnostics}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[FATAL] 中断されました。")
        sys.exit(130)
