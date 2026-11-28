import os
import sys
import re
import time
import argparse
import datetime as dt
import unicodedata
from typing import Any, Dict, Optional, Tuple

import pandas as pd


# External dependency: keepa
try:
    import keepa  # type: ignore
except Exception as e:  # pragma: no cover
    print("[FATAL] keepa パッケージをインポートできません。pip install keepa を実行してください。", file=sys.stderr)
    raise


# ==========================
# Configuration (overridable via CLI)
# ==========================
KEEPA_PRICE_DIV100_DEFAULT: bool = False


# ==========================
# Utilities
# ==========================
def normalize_text(value: str) -> str:
    """NFKC正規化 + 小文字化 + 空白削除 を適用した文字列を返す。"""
    if value is None:
        return ""
    # 全角→半角, 濁点結合の正規化など
    normed = unicodedata.normalize("NFKC", str(value))
    # 小文字化
    normed = normed.lower()
    # すべての空白類を除去（半角/全角スペース、タブ、改行）
    normed = re.sub(r"\s+", "", normed)
    return normed


def to_num(value: Any) -> Optional[float]:
    """価格テキストから数値を抽出して float を返す。

    - カンマ、空白、通貨記号（円/¥/￥）や『税込/税抜』等を排除
    - 数字が見つからない場合は None
    """
    if value is None:
        return None
    text = str(value)
    if text.strip() == "":
        return None

    # NFKCで全角→半角
    text = unicodedata.normalize("NFKC", text)
    # 通貨記号などのノイズ除去
    text = text.replace("円", "").replace("¥", "").replace("￥", "")
    text = text.replace("税込", "").replace("税抜", "")
    # カンマ除去
    text = text.replace(",", "")
    # スペース除去
    text = re.sub(r"\s+", " ", text).strip()

    # 最初に出現する数値（整数 or 小数）を抽出
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def resolve_desktop_path() -> str:
    """OneDrive配下のDesktopを解決。存在しない場合はホーム配下をフォールバック。"""
    userprofile = os.environ.get("USERPROFILE")
    candidates = []
    if userprofile:
        candidates.append(os.path.join(userprofile, "OneDrive", "Desktop"))
    # 明示フォールバック
    candidates.append(os.path.expanduser(os.path.join("~", "OneDrive", "Desktop")))
    candidates.append(os.path.expanduser("~/Desktop"))

    for path in candidates:
        if os.path.isdir(path):
            return path
    # 最後のフォールバック: カレント
    return os.getcwd()


def default_input_csv_path() -> str:
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        return os.path.join(userprofile, "OneDrive", "Desktop", "costco_allcategories_20pages_with_asin.csv")
    # フォールバック
    return os.path.expanduser(os.path.join("~", "OneDrive", "Desktop", "costco_allcategories_20pages_with_asin.csv"))


def try_read_csv(path: str) -> pd.DataFrame:
    """エンコーディングに強いCSV読込（変更不可）。"""
    # 既定候補: utf-8-sig, cp932, utf-8
    encodings = ["utf-8-sig", "cp932", "utf-8"]
    errors = []
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:  # pragma: no cover
            errors.append(f"{enc}: {e}")
            continue
    raise RuntimeError("CSVの読込に失敗しました: " + "; ".join(errors))


def find_costco_price_column(df: pd.DataFrame) -> Tuple[Optional[str], str]:
    """Costco価格の列を自動特定。返り値は (原始列名 or None, 判定理由)"""
    columns = list(df.columns)
    normalized_map: Dict[str, str] = {normalize_text(c): c for c in columns}

    # 優先順（完全一致判定は正規化後の同値性）
    exact_priority = [
        "costco価格",
        "コストコ価格",
        "costcoprice",
        "価格",
        "原価",
        "仕入価格",
        "仕入れ価格",
    ]

    for key in exact_priority:
        norm_key = normalize_text(key)
        if norm_key in normalized_map:
            return normalized_map[norm_key], f"候補集合に完全一致: {normalized_map[norm_key]}"

    # 2段階目: 列名に costco を含み、かつ 価/price/値/原価/仕入 の語を含む
    for norm_col, orig in normalized_map.items():
        if "costco" in norm_col and (
            ("価" in norm_col)
            or ("price" in norm_col)
            or ("値" in norm_col)
            or ("原価" in norm_col)
            or ("仕入" in norm_col)
        ):
            return orig, f"costco+価格語を含む: {orig}"

    # 3段階目: フォールバックとして "価格元テキスト"
    for cand in ["価格元テキスト"]:
        norm_cand = normalize_text(cand)
        if norm_cand in normalized_map:
            return normalized_map[norm_cand], "フォールバック: 価格元テキスト"

    return None, "該当列なし"


def get_keepa_client() -> "keepa.Keepa":  # type: ignore[name-defined]
    api_key = os.environ.get("KEEPA_API_KEY")
    if not api_key:
        print("[FATAL] 環境変数 KEEPA_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(2)
    return keepa.Keepa(api_key)


def is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc) if exc else ""
    msg_low = msg.lower()
    keywords = ["429", "token", "quota", "rate", "exhaust"]
    return any(k in msg_low for k in keywords)


def query_keepa_product(api: "keepa.Keepa", asin: str, *, history: bool = True) -> Optional[Dict[str, Any]]:  # type: ignore[name-defined]
    """Keepa APIから製品情報を取得。JPドメイン指定で、互換のため二通りのdomain指定にトライ。"""
    # まず domain='JP' を試す
    try:
        products = api.query(asin, history=history, domain='JP')
        if products and isinstance(products, list):
            return products[0]
        return None
    except TypeError:
        # ライブラリ差異: int指定にフォールバック（JP=6）
        products = api.query(asin, history=history, domain=6)
        if products and isinstance(products, list):
            return products[0]
        return None


def fetch_latest_price(api: "keepa.Keepa", asin: str, *, div100: bool) -> Tuple[Optional[float], Optional[str], Optional[str]]:  # type: ignore[name-defined]
    """最新価格（stats.current）の amazon→buyBox→new 優先で取得。

    戻り値: (price_jpy, source_key, error_reason)
    """
    # 1回再試行まで
    for attempt in range(2):
        try:
            product = query_keepa_product(api, asin, history=True)
            if not product:
                return None, None, "Keepa: 製品が見つかりません"
            stats = product.get("stats") or {}
            current = stats.get("current") or {}
            for key in ("amazon", "buyBox", "new"):
                raw = current.get(key)
                try:
                    price_val = float(raw) if raw is not None else None
                except Exception:
                    price_val = None
                if price_val is None or price_val <= 0:
                    continue
                price_jpy = price_val / 100.0 if div100 else price_val
                return price_jpy, key, None
            return None, None, "Keepa: stats.current に有効な価格がありません"
        except Exception as e:
            if is_rate_limit_error(e) and attempt == 0:
                print("[INFO] Keepa レート制限検知。25秒待機して再試行します…")
                time.sleep(25)
                continue
            return None, None, f"Keepa取得失敗: {e}"


def ensure_columns(df: pd.DataFrame, columns: list) -> pd.DataFrame:
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Costco×Keepa 価格差抽出スクリプト")
    parser.add_argument("--input", dest="input_csv", default=None, help="入力CSVパス（省略時は OneDrive/Desktop の既定パス）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--div100", dest="div100", action="store_true", help="Keepa価格を/100して解釈する")
    group.add_argument("--no-div100", dest="div100", action="store_false", help="Keepa価格を/100しない")
    parser.set_defaults(div100=None)
    args = parser.parse_args()

    input_path = args.input_csv or default_input_csv_path()
    input_path = os.path.expandvars(os.path.expanduser(input_path))

    div100: bool = KEEPA_PRICE_DIV100_DEFAULT if args.div100 is None else bool(args.div100)
    print(f"[INFO] Keepa価格スケール /100: {'ON' if div100 else 'OFF'}")

    # 入力
    try:
        df = try_read_csv(input_path)
    except Exception as e:
        print(f"[FATAL] 入力CSVの読込に失敗: {e}", file=sys.stderr)
        sys.exit(2)

    # Costco価格列の自動特定
    costco_col, detect_reason = find_costco_price_column(df)
    if costco_col is None:
        print("[INFO] Costco価格列の特定に失敗しました。全行に NaN を設定し処理を継続します。")
        df["Costco価格"] = pd.Series([None] * len(df), dtype="float")
    else:
        print(f"[INFO] Costco価格列を特定: '{costco_col}' （{detect_reason}）")
        # 数値化
        df["Costco価格"] = df[costco_col].map(to_num).astype("float")

    # ASIN の正規化（そのまま基本使用）
    if "ASIN" not in df.columns:
        print("[FATAL] 入力CSVに 'ASIN' 列が存在しません。", file=sys.stderr)
        sys.exit(2)

    # 出力列の雛形を追加
    df["Amazon推定売価"] = pd.Series([None] * len(df), dtype="float")
    df["価格ソース"] = ""
    df["粗利"] = pd.Series([None] * len(df), dtype="float")
    df["ROI(%)"] = pd.Series([None] * len(df), dtype="float")
    df["異常値フラグ"] = False
    df["除外理由"] = ""

    # Keepa クライアント
    api = get_keepa_client()

    # ユニークASIN毎にKeepa取得
    asin_series = df["ASIN"].astype(str).fillna("")
    unique_asins = [a for a in asin_series.unique().tolist() if a and a.strip() and a.strip().lower() != "nan"]

    asin_to_price: Dict[str, Tuple[Optional[float], Optional[str], Optional[str]]] = {}

    total = len(unique_asins)
    for idx, asin in enumerate(unique_asins):
        if (idx + 1) % 50 == 0 or (idx + 1) == total:
            print(f"[INFO] 進捗: {idx + 1}/{total}")
        price, source, err = fetch_latest_price(api, asin, div100=div100)
        asin_to_price[asin] = (price, source, err)

    # マッピング適用
    def map_price(asin: Any) -> Tuple[Optional[float], str, str]:
        a = str(asin)
        res = asin_to_price.get(a)
        if not res:
            return None, "", "ASIN未取得"
        price, source, err = res
        return price, (source or ""), (err or "")

    mapped = df["ASIN"].map(map_price)
    df["Amazon推定売価"] = mapped.map(lambda x: x[0])
    df["価格ソース"] = mapped.map(lambda x: x[1])
    df_err = mapped.map(lambda x: x[2])

    # 異常値ガード & 収益計算
    def evaluate_row(row: pd.Series) -> pd.Series:
        costco_price = row.get("Costco価格")
        amz_price = row.get("Amazon推定売価")
        reason_list = []
        abnormal = False

        # Keepa取得エラー
        keepa_err = row.get("_keepa_err_tmp")
        if keepa_err:
            reason_list.append(str(keepa_err))

        # Amazon推定売価の異常値
        if amz_price is None or not isinstance(amz_price, (int, float)):
            abnormal = True
            reason_list.append("Amazon推定売価が取得できない")
        else:
            if not (0 < float(amz_price) <= 1_000_000):
                abnormal = True
                reason_list.append("Amazon推定売価が異常(0<price≤1,000,000 以外)")

        # Costco価格の異常比率チェック
        if isinstance(costco_price, (int, float)) and isinstance(amz_price, (int, float)) and costco_price and costco_price > 0:
            ratio = float(amz_price) / float(costco_price)
            if ratio > 30 or ratio < 0.3:
                abnormal = True
                reason_list.append("価格比が異常(>30x または <0.3x)")

        # 粗利 & ROI
        gross = None
        roi = None
        if isinstance(costco_price, (int, float)) and isinstance(amz_price, (int, float)):
            gross = float(amz_price) - float(costco_price)
            if costco_price and costco_price > 0:
                roi = (gross / float(costco_price)) * 100.0

        row["粗利"] = gross
        row["ROI(%)"] = roi
        row["異常値フラグ"] = bool(abnormal)
        if reason_list:
            row["除外理由"] = "; ".join([r for r in reason_list if r])
        return row

    df["_keepa_err_tmp"] = df_err
    df = df.apply(evaluate_row, axis=1)
    df.drop(columns=["_keepa_err_tmp"], inplace=True)

    # 候補抽出: ROI(%) >= 20 かつ 粗利 >= 500 かつ 異常値フラグ == False
    candidates_df = df[
        (df["異常値フラグ"] == False)
        & (df["粗利"].astype(float) >= 500)
        & (df["ROI(%)"].astype(float) >= 20)
    ].copy()

    # ダイアグ: 除外理由がある or 異常値フラグ=True
    diagnostics_df = df[(df["除外理由"].astype(str) != "") | (df["異常値フラグ"] == True)].copy()

    # 列順を固定。不足列は空で埋める
    fixed_order = [
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

    def prepare_columns(d: pd.DataFrame) -> pd.DataFrame:
        d = d.copy()
        d = ensure_columns(d, fixed_order)
        # 型整形（存在する場合のみ）
        for col in ["Costco価格", "Amazon推定売価", "粗利", "ROI(%)"]:
            if col in d.columns:
                d[col] = pd.to_numeric(d[col], errors="coerce")
        # 並べ替え
        return d[fixed_order]

    allrows_out = prepare_columns(df)
    candidates_out = prepare_columns(candidates_df)
    diagnostics_out = prepare_columns(diagnostics_df)

    # 出力
    desktop = resolve_desktop_path()
    now = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_all = os.path.join(desktop, f"price_gap_allrows_{now}.csv")
    out_cand = os.path.join(desktop, f"price_gap_candidates_{now}.csv")
    out_diag = os.path.join(desktop, f"price_gap_diagnostics_{now}.csv")

    for path, data in [
        (out_all, allrows_out),
        (out_cand, candidates_out),
        (out_diag, diagnostics_out),
    ]:
        try:
            data.to_csv(path, encoding="utf-8-sig", index=False)
            print(f"[INFO] 保存: {path}")
        except Exception as e:  # pragma: no cover
            print(f"[ERROR] CSV保存に失敗: {path} -> {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # pragma: no cover
        print("[INFO] 中断されました。")
        sys.exit(130)
