import os
import sys
import re
import time
import argparse
import datetime as dt
import unicodedata
import random
import statistics
from typing import Any, Dict, Optional, Tuple, List, Iterable

try:
	import pandas as pd
except Exception:
	print("[FATAL] pandas を import できません。`pip install pandas keepa` を実行してください。", file=sys.stderr)
	raise

try:
	import keepa  # type: ignore
except Exception:
	print("[FATAL] keepa を import できません。`pip install keepa pandas` を実行してください。", file=sys.stderr)
	raise


# === 設定（CLIで上書き可能） ===
KEEPA_PRICE_DIV100_DEFAULT: bool = True            # Keepa価格は 1/100 スケールが基本
PROGRESS_LOG_EVERY: int = 50                        # 進捗ログの間隔
BATCH_SIZE_DEFAULT: int = 10                        # Keepa負荷と成功率のバランス
MAX_RETRIES: int = 5                                # 429時の最大リトライ回数


# === ユーティリティ ===
def normalize_text(value: str) -> str:
	if value is None:
		return ""
	s = unicodedata.normalize("NFKC", str(value))
	s = s.lower()
	s = re.sub(r"\s+", "", s)
	return s


def to_num(value: Any) -> Optional[float]:
	if value is None:
		return None
	text = str(value).strip()
	if not text:
		return None
	text = unicodedata.normalize("NFKC", text)
	text = text.replace("円", "").replace("¥", "").replace("￥", "")
	text = text.replace("税込", "").replace("税抜", "")
	text = text.replace(",", "")
	text = re.sub(r"\s+", " ", text).strip()
	m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
	if not m:
		return None
	try:
		return float(m.group(0))
	except Exception:
		return None


def resolve_desktop_path() -> str:
	up = os.environ.get("USERPROFILE")
	cand = []
	if up:
		cand.append(os.path.join(up, "OneDrive", "Desktop"))
	cand.append(os.path.expanduser(os.path.join("~", "OneDrive", "Desktop")))
	cand.append(os.path.expanduser("~/Desktop"))
	for p in cand:
		if os.path.isdir(p):
			return p
	return os.getcwd()


def default_input_csv_path() -> str:
	up = os.environ.get("USERPROFILE", "")
	if up:
		return os.path.join(up, "OneDrive", "Desktop", "costco_allcategories_20pages_with_asin.csv")
	return os.path.expanduser(os.path.join("~", "OneDrive", "Desktop", "costco_allcategories_20pages_with_asin.csv"))


def try_read_csv(path: str) -> pd.DataFrame:
	for enc in ("utf-8-sig", "cp932", "utf-8"):
		try:
			return pd.read_csv(path, encoding=enc)
		except Exception:
			continue
	raise RuntimeError("CSVの読込に失敗しました（utf-8-sig / cp932 / utf-8 すべて失敗）")


def ensure_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
	for c in columns:
		if c not in df.columns:
			df[c] = ""
	return df


def find_costco_price_column(df: pd.DataFrame) -> Tuple[Optional[str], str]:
	cols = list(df.columns)
	nmap: Dict[str, str] = {normalize_text(c): c for c in cols}
	exact = [
		"costco価格", "コストコ価格", "costcoprice",
		"価格", "原価", "仕入価格", "仕入れ価格",
	]
	for key in exact:
		nk = normalize_text(key)
		if nk in nmap:
			return nmap[nk], f"候補集合に完全一致: {nmap[nk]}"
	for nk, orig in nmap.items():
		if "costco" in nk and any(k in nk for k in ["価", "price", "値", "原価", "仕入"]):
			return orig, f"costco+価格語を含む: {orig}"
	if normalize_text("価格元テキスト") in nmap:
		return nmap[normalize_text("価格元テキスト")], "フォールバック: 価格元テキスト"
	return None, "該当列なし"


def get_keepa_client() -> "keepa.Keepa":  # type: ignore[name-defined]
	key = os.environ.get("KEEPA_API_KEY")
	if not key:
		print("[FATAL] 環境変数 KEEPA_API_KEY が未設定です。`setx KEEPA_API_KEY \"<キー>\"` を設定し、新しい端末で再実行してください。", file=sys.stderr)
		sys.exit(2)
	return keepa.Keepa(key)


def keepa_minutes_to_datetime(keepa_minutes: int) -> dt.datetime:
	return dt.datetime(2011, 1, 1) + dt.timedelta(minutes=int(keepa_minutes))


def now_keepa_minutes() -> int:
	return int((dt.datetime.utcnow() - dt.datetime(2011, 1, 1)).total_seconds() // 60)


def is_rate_limit_error(exc: Exception) -> bool:
	m = str(exc).lower()
	return any(t in m for t in ["429", "token", "quota", "rate", "exhaust"]) or isinstance(exc, keepa.RateLimitException)  # type: ignore[attr-defined]


def sleep_with_jitter(base_seconds: float, attempt: int) -> None:
	backoff = base_seconds * (2 ** max(0, attempt - 1))
	jitter = random.uniform(0.3, 2.3)
	wait = min(backoff + jitter, 120.0)
	print(f"[INFO] 429対策: {wait:.1f}s 待機 (試行 {attempt}/{MAX_RETRIES})")
	time.sleep(wait)


def get_token_hints(api: "keepa.Keepa") -> Tuple[Optional[int], Optional[int]]:
	"""keepaクライアントから tokensLeft / refillRate を可能なら読み取る（存在しない環境もある）。"""
	left = None
	refill = None
	for attr in ("tokens_left", "tokensLeft"):
		left = left or getattr(api, attr, None)
	for attr in ("refill_rate", "refillRate"):
		refill = refill or getattr(api, attr, None)
	try:
		# 一部実装では api.tokens が dict のことがある
		tokens = getattr(api, "tokens", None)
		if isinstance(tokens, dict):
			left = left or tokens.get("tokensLeft")
			refill = refill or tokens.get("refillRate")
	except Exception:
		pass
	return (left if isinstance(left, int) else None, refill if isinstance(refill, int) else None)


def query_keepa_batch(api: "keepa.Keepa", asins: List[str], *, history: bool) -> List[Optional[Dict[str, Any]]]:
	"""ASINのバッチを問い合わせ（429に強い）。戻り値は入力順を維持。

	- 最大 MAX_RETRIES 回まで指数バックオフ
	- tokensLeft/refillRate が得られたら待機時間の目安に使用
	"""
	assert len(asins) > 0
	last_exc: Optional[Exception] = None
	for attempt in range(1, MAX_RETRIES + 1):
		try:
			prods = api.query(asins, history=history, domain="JP")  # type: ignore[arg-type]
			# keepaは見つからないASINは None を返すことがある
			if isinstance(prods, list):
				return [p if isinstance(p, dict) else None for p in prods]
			return [None] * len(asins)
		except TypeError:
			# 一部環境で domain は int (JP=6)
			try:
				prods = api.query(asins, history=history, domain=6)  # JP=6
				if isinstance(prods, list):
					return [p if isinstance(p, dict) else None for p in prods]
				return [None] * len(asins)
			except Exception as e:
				last_exc = e
				if is_rate_limit_error(e) and attempt < MAX_RETRIES:
					left, refill = get_token_hints(api)
					base = 60.0 if (left is not None and left <= 0) else 20.0
					if refill:
						base = max(base, float(refill))
					sleep_with_jitter(base_seconds=base, attempt=attempt)
					continue
				raise
		except Exception as e:
			last_exc = e
			if is_rate_limit_error(e) and attempt < MAX_RETRIES:
				left, refill = get_token_hints(api)
				base = 60.0 if (left is not None and left <= 0) else 20.0
				if refill:
					base = max(base, float(refill))
				sleep_with_jitter(base_seconds=base, attempt=attempt)
				continue
			raise
	# ここまで来たら失敗
	raise RuntimeError(f"Keepaクエリ失敗（{asins[:3]}...）: {last_exc}")


# === Keepa データ処理 ===
CSV_SERIES_ALIASES: Dict[str, List[str]] = {
	"buybox": ["buyBoxPrices", "buyBox", "BUY_BOX_SHIPPING", "BUY_BOX", "BUY_BOX_FBA"],
	"amazon": ["amazon", "amazonPrice", "AMAZON", "AMAZON_FBA"],
	"new": ["new", "newPrice", "NEW"],
}


def get_price_scale(div100: bool) -> float:
	return 0.01 if div100 else 1.0


def get_csv_container(prod: Dict[str, Any]) -> Optional[Dict[str, Any]]:
	for key in ("csv", "data", "prices", "history", "tracks"):
		val = prod.get(key)
		if isinstance(val, dict):
			return val
	return None


def get_series(prod: Dict[str, Any], series_name: str) -> Optional[List[Any]]:
	container = get_csv_container(prod)
	if container:
		for alias in CSV_SERIES_ALIASES.get(series_name, []):
			if alias in container and isinstance(container[alias], list):
				return container[alias]
	# 一部はトップレベルにある可能性
	for alias in CSV_SERIES_ALIASES.get(series_name, []):
		if alias in prod and isinstance(prod[alias], list):
			return prod[alias]
	return None


def iter_price_points(series: List[Any]) -> Iterable[Tuple[int, float]]:
	"""Keepa csv: [t0, p0, t1, p1, ...] を (time_minute, price_raw) で後方から返す。"""
	if not series:
		return []
	# 価格は奇数インデックス（1,3,5, ...）
	start = len(series) - 1
	if start % 2 == 0:
		start -= 1
	idx = start
	while idx > 0:
		price_raw = series[idx]
		time_min = series[idx - 1]
		try:
			price_val = float(price_raw) if price_raw is not None else 0.0
		except Exception:
			price_val = 0.0
		yield int(time_min), price_val
		idx -= 2


def last_valid_price_in_window(series: List[Any], *, within_days: Optional[int], div100: bool) -> Optional[Tuple[float, int]]:
	"""指定期間内で最後の非ゼロ価格を後方探索して返す。戻り: (price_scaled, time_minute)"""
	now_kmin = now_keepa_minutes()
	limit_min = None if within_days is None else now_kmin - within_days * 1440
	for t_min, price_raw in iter_price_points(series):
		if price_raw and price_raw > 0:
			if limit_min is None or t_min >= limit_min:
				price = price_raw * get_price_scale(div100)
				if 0 < price < 1_000_000:
					return price, t_min
	return None


def median_price(series: List[Any], *, within_days: Optional[int], div100: bool) -> Optional[float]:
	"""期間内の非ゼロ価格の中央値。"""
	now_kmin = now_keepa_minutes()
	limit_min = None if within_days is None else now_kmin - within_days * 1440
	vals: List[float] = []
	for t_min, price_raw in iter_price_points(series):
		if price_raw and price_raw > 0:
			if limit_min is None or t_min >= limit_min:
				vals.append(price_raw * get_price_scale(div100))
	if not vals:
		return None
	try:
		return statistics.median(vals)
	except Exception:
		vals.sort()
		mid = len(vals) // 2
		return vals[mid]


def is_extreme_outlier(price: float, ref_median: Optional[float]) -> bool:
	if ref_median is None or ref_median <= 0:
		return False
	return price >= ref_median * 10 or price <= ref_median / 10


def infer_stock_state(prod: Dict[str, Any]) -> str:
	"""在庫状態の推定。
	- current系が存在 → in-stock
	- 直近30日間、価格系列（buyBox/amazon/new）の最後の非ゼロが見つからない → oos
	- それ以外 → unknown
	"""
	stats = prod.get("stats") or {}
	current = (stats.get("current") or {}) if isinstance(stats, dict) else {}
	for k in ("buyBoxPrice", "buyBox", "amazonPrice", "amazon", "newPrice", "new"):
		v = current.get(k)
		try:
			fv = float(v) if v is not None else 0.0
		except Exception:
			fv = 0.0
		if fv > 0:
			return "in-stock"
	# 履歴から判断
	for name in ("buybox", "amazon", "new"):
		series = get_series(prod, name)
		if not series:
			continue
		last = last_valid_price_in_window(series, within_days=30, div100=True)
		if last is not None:
			return "unknown"
	return "oos"


def pick_amazon_price_from_product(prod: Dict[str, Any]) -> Optional[float]:
	"""価格優先順位に基づいて、最終的な妥当価格のみを返す（値が見つからないときだけ None）。

	この関数は最終価格のみ返す。詳細なソースや理由は `decide_amazon_price` 側で扱う。
	"""
	price, _, _, _ = decide_amazon_price(prod, div100=True)
	return price


def decide_amazon_price(
	prod: Dict[str, Any], *, div100: bool
) -> Tuple[Optional[float], Optional[str], str, str]:
	"""優先順位／履歴探索／異常値ガードをすべて実施し、
	(price, source, stock_state, note) を返す。price が None の場合のみ見つからなかった。
	"""
	stock_state = infer_stock_state(prod)
	notes: List[str] = []
	stats = prod.get("stats") or {}
	current = (stats.get("current") or {}) if isinstance(stats, dict) else {}

	# 近傍中央値（診断用）
	bb_series = get_series(prod, "buybox")
	bb_med_30 = median_price(bb_series, within_days=30, div100=div100) if bb_series else None
	bb_med_90 = median_price(bb_series, within_days=90, div100=div100) if bb_series else None

	def validate(price: Optional[float], *, ref_median: Optional[float], label: str) -> Tuple[Optional[float], Optional[str]]:
		if price is None:
			return None, None
		if not (0 < price < 1_000_000):
			notes.append(f"{label}: 異常値レンジ→除外")
			return None, None
		if is_extreme_outlier(price, ref_median):
			notes.append(f"{label}: 近傍中央値 {ref_median} と乖離→除外")
			return None, None
		return price, label

	# --- Step1: stats.current.buyBoxPrice → current.buyBoxPrice
	for key in ("buyBoxPrice", "buyBox"):
		v = current.get(key)
		price = (float(v) * get_price_scale(div100)) if v else None
		p, src = validate(price, ref_median=bb_med_30 or bb_med_90, label=f"stats.current.{key}")
		if p is not None:
			return p, src, stock_state, "; ".join(notes)

	# --- Step2: stats.current.amazonPrice → current.amazonPrice
	for key in ("amazonPrice", "amazon"):
		v = current.get(key)
		price = (float(v) * get_price_scale(div100)) if v else None
		p, src = validate(price, ref_median=bb_med_30 or bb_med_90, label=f"stats.current.{key}")
		if p is not None:
			return p, src, stock_state, "; ".join(notes)

	# --- Step3: stats.current.newPrice → current.newPrice
	for key in ("newPrice", "new"):
		v = current.get(key)
		price = (float(v) * get_price_scale(div100)) if v else None
		p, src = validate(price, ref_median=bb_med_30 or bb_med_90, label=f"stats.current.{key}")
		if p is not None:
			return p, src, stock_state, "; ".join(notes)

	# --- Step4: buyBoxPrices（履歴）を 直近→過去90日→過去365日→全期間
	if bb_series:
		for days, tag in [(30, "-30d"), (90, "-90d"), (365, "-365d"), (None, "-all")]:
			res = last_valid_price_in_window(bb_series, within_days=days, div100=div100)
			if res is None:
				continue
			price, _t = res
			ref_med = bb_med_30 or bb_med_90 or median_price(bb_series, within_days=days, div100=div100)
			p, src = validate(price, ref_median=ref_med, label=f"buyBoxPrices@{tag}")
			if p is not None:
				return p, src, stock_state, "; ".join(notes)

	# --- Step5: csv の系列（buyBox / amazon / new）を 後方探索
	for name in ("buybox", "amazon", "new"):
		series = get_series(prod, name)
		if not series:
			continue
		res = last_valid_price_in_window(series, within_days=None, div100=div100)
		if res is None:
			continue
		price, _t = res
		ref_med = bb_med_30 or bb_med_90 or median_price(series, within_days=90, div100=div100)
		p, src = validate(price, ref_median=ref_med, label=f"csv.{name}.last")
		if p is not None:
			return p, src, stock_state, "; ".join(notes)

	# ここまで見つからなければ None
	return None, None, stock_state, "; ".join(notes) if notes else ""


def calc_profit(costco_price: Optional[float], amazon_price: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
	"""粗利とROIを計算（紹介料15%控除）。Noneが混ざる場合は (None, None)"""
	if costco_price is None or amazon_price is None:
		return None, None
	try:
		c = float(costco_price)
		a = float(amazon_price)
		if c <= 0 or a <= 0:
			return None, None
		net_sales = a * 0.85
		profit = net_sales - c
		roi = (profit / c) * 100.0 if c > 0 else None
		return (profit, roi if roi is not None else None)
	except Exception:
		return None, None


class DiagnosticsRecorder:
	def __init__(self) -> None:
		self.rows: List[Dict[str, Any]] = []

	def append(self, asin: str, source: Optional[str], used_value: Optional[float], note: str, stock_state: str, median30: Optional[float], median90: Optional[float]) -> None:
		self.rows.append({
			"ASIN": asin,
			"価格ソース": source or "",
			"採用価格": used_value,
			"在庫状態": stock_state,
			"備考": note or "",
			"中央値30d": median30,
			"中央値90d": median90,
		})

	def to_dataframe(self) -> pd.DataFrame:
		if not self.rows:
			return pd.DataFrame(columns=["ASIN","価格ソース","採用価格","在庫状態","備考","中央値30d","中央値90d"])
		return pd.DataFrame(self.rows)


# グローバル診断レコーダ（append_diag 用）
GLOBAL_DIAG_RECORDER: Optional[DiagnosticsRecorder] = None


def append_diag(asin: str, source: Optional[str], used_value: Optional[float], note: str, stock_state: str = "", median30: Optional[float] = None, median90: Optional[float] = None) -> None:
	"""診断CSVに1行追記（互換API）。内部ではグローバルレコーダに委譲。"""
	global GLOBAL_DIAG_RECORDER
	if GLOBAL_DIAG_RECORDER is None:
		GLOBAL_DIAG_RECORDER = DiagnosticsRecorder()
	GLOBAL_DIAG_RECORDER.append(asin, source, used_value, note, stock_state, median30, median90)


def process_asins(api: "keepa.Keepa", asins: List[str], *, div100: bool, batch_size: int, diag: DiagnosticsRecorder) -> Dict[str, Tuple[Optional[float], Optional[str], str, str, Optional[float], Optional[float]]]:
	"""ASINごとに (price, source, stock_state, note, med30, med90) を返す辞書。"""
	result: Dict[str, Tuple[Optional[float], Optional[str], str, str, Optional[float], Optional[float]]] = {}
	for i in range(0, len(asins), batch_size):
		batch = asins[i:i + batch_size]
		try:
			products = query_keepa_batch(api, batch, history=True)
		except Exception as e:
			print(f"[ERROR] Keepaバッチ取得失敗: {e}")
			products = [None] * len(batch)
		for asin, prod in zip(batch, products):
			if prod is None:
				result[asin] = (None, None, "unknown", "Keepa: 製品が見つからない", None, None)
				continue
			# 決定ロジック
			price, src, stock, note = decide_amazon_price(prod, div100=div100)
			# 診断用中央値（buybox系列中心）
			bb_series = get_series(prod, "buybox")
			med30 = median_price(bb_series, within_days=30, div100=div100) if bb_series else None
			med90 = median_price(bb_series, within_days=90, div100=div100) if bb_series else None
			result[asin] = (price, src, stock, note, med30, med90)
			diag.append(asin, src, price, note, stock, med30, med90)
	return result


def main() -> None:
	parser = argparse.ArgumentParser(description="Costco×Keepa 価格差抽出（最終販売価格採用・診断強化・429堅牢）")
	parser.add_argument("--input", dest="input_csv", default=None, help="入力CSVパス（省略時は OneDrive/Desktop 既定）")
	group = parser.add_mutually_exclusive_group()
	group.add_argument("--div100", dest="div100", action="store_true", help="Keepa価格を/100で解釈")
	group.add_argument("--no-div100", dest="div100", action="store_false", help="Keepa価格を/100しない")
	parser.set_defaults(div100=None)
	parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, help="Keepaクエリのバッチサイズ（既定10）")
	parser.add_argument("--min-profit", type=float, default=300.0, help="抽出の粗利しきい値（円）")
	parser.add_argument("--min-roi", type=float, default=15.0, help="抽出のROIしきい値（%%）")
	args = parser.parse_args()

	input_path = args.input_csv or default_input_csv_path()
	input_path = os.path.expandvars(os.path.expanduser(input_path))

	div100 = KEEPA_PRICE_DIV100_DEFAULT if args.div100 is None else bool(args.div100)
	print(f"[INFO] Keepa価格スケール /100: {'ON' if div100 else 'OFF'}")
	print(f"[INFO] バッチサイズ: {args.batch_size}")

	# 入力読込
	try:
		df = try_read_csv(input_path)
	except Exception as e:
		print(f"[FATAL] 入力CSV読込失敗: {e}", file=sys.stderr)
		sys.exit(2)

	# Costco価格列検出
	costco_col, reason = find_costco_price_column(df)
	if costco_col is None:
		print("[INFO] Costco価格列の特定に失敗 → 全行NaNで継続")
		df["Costco価格"] = pd.Series([None] * len(df), dtype="float")
	else:
		print(f"[INFO] Costco価格列: '{costco_col}' （{reason}）")
		df["Costco価格"] = df[costco_col].map(to_num).astype("float")

	# ASINチェック
	if "ASIN" not in df.columns:
		print("[FATAL] 入力CSVに 'ASIN' 列がありません。", file=sys.stderr)
		sys.exit(2)

	asin_series = df["ASIN"].astype(str).fillna("")
	unique_asins = [a.strip() for a in asin_series.unique().tolist() if a and a.strip() and a.strip().lower() != "nan"]
	print(f"[INFO] クエリ対象 ASIN 件数: {len(unique_asins)}")
	print(f"[INFO] 先頭ASIN(最大10件): {unique_asins[:10]}")

	# 出力列の雛形
	for col, dtype in (("Amazon推定売価", "float"), ("粗利", "float"), ("ROI(%)", "float")):
		df[col] = pd.Series([None] * len(df), dtype=dtype)
	df["価格ソース"] = ""
	df["在庫状態"] = ""

	api = get_keepa_client()
	diag = DiagnosticsRecorder()
	# グローバルへもセット（append_diag 互換用）
	global GLOBAL_DIAG_RECORDER
	GLOBAL_DIAG_RECORDER = diag

	# バッチで取得
	results: Dict[str, Tuple[Optional[float], Optional[str], str, str, Optional[float], Optional[float]]] = {}
	total = len(unique_asins)
	for idx in range(0, total, args.batch_size):
		chunk = unique_asins[idx: idx + args.batch_size]
		if (idx + 1) % PROGRESS_LOG_EVERY == 0 or (idx + len(chunk)) == total:
			print(f"[INFO] 進捗: {idx + len(chunk)}/{total}")
		chunk_result = process_asins(api, chunk, div100=div100, batch_size=args.batch_size, diag=diag)
		results.update(chunk_result)

	def map_price(asin: Any) -> Tuple[Optional[float], str, str]:
		asin_key = str(asin).strip()
		if asin_key not in results:
			return None, "", ""
		p, src, stock, _note, _m30, _m90 = results[asin_key]
		return p, (src or ""), stock

	mapped = df["ASIN"].map(map_price)
	df["Amazon推定売価"] = mapped.map(lambda x: x[0])
	df["価格ソース"] = mapped.map(lambda x: x[1])
	df["在庫状態"] = mapped.map(lambda x: x[2])

	# 収益計算
	def eval_row(r: pd.Series) -> pd.Series:
		c = r.get("Costco価格")
		a = r.get("Amazon推定売価")
		profit, roi = calc_profit(c, a)
		r["粗利"] = profit
		r["ROI(%)"] = roi
		return r

	df = df.apply(eval_row, axis=1)

	# 抽出条件: 粗利 ≥ 300, ROI ≥ 15%, Amazon推定売価 が存在
	df_candidates = df[(pd.to_numeric(df["Amazon推定売価"], errors="coerce").notna())]
	df_candidates = df_candidates[(pd.to_numeric(df_candidates["粗利"], errors="coerce") >= args.min_profit) & (pd.to_numeric(df_candidates["ROI(%)"], errors="coerce") >= args.min_roi)].copy()

	# 出力列順
	fixed_cols = [
		"カテゴリ", "商品名", "商品URL", "Costco価格", "Amazon推定売価", "粗利", "ROI(%)", "ASIN", "価格ソース", "在庫状態"
	]

	def fix_order(d: pd.DataFrame) -> pd.DataFrame:
		d = d.copy()
		d = ensure_columns(d, fixed_cols)
		for c in ["Costco価格","Amazon推定売価","粗利","ROI(%)"]:
			if c in d.columns:
				d[c] = pd.to_numeric(d[c], errors="coerce")
		return d[fixed_cols]

	df_candidates = fix_order(df_candidates)

	# 診断CSV
	df_diag = diag.to_dataframe()

	# 出力
	desktop = resolve_desktop_path()
	now = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
	out_cand = os.path.join(desktop, f"price_gap_candidates_{now}.csv")
	out_diag = os.path.join(desktop, f"price_gap_diagnostics_{now}.csv")

	for pth, data in [(out_cand, df_candidates), (out_diag, df_diag)]:
		try:
			data.to_csv(pth, encoding="utf-8-sig", index=False)
			print(f"[INFO] 保存: {pth}")
		except Exception as e:
			print(f"[ERROR] CSV保存失敗: {pth} -> {e}", file=sys.stderr)


if __name__ == "__main__":
	try:
		main()
	except KeyboardInterrupt:
		print("[INFO] 中断されました。"); sys.exit(130)

