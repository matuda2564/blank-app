#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TSLA 1分足の直近60本からSMA20/50を計算し、
SMA20がSMA50を上抜けクロスしたら "buy"、それ以外は "hold"。
結果を decision_log.json に追記保存します（1行=1JSON）。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf


TICKER = "TSLA"
OUTPUT_PATH = Path("decision_log.json")


def fetch_1m_data_last_60(ticker: str) -> pd.DataFrame:
    """
    yfinanceで1分足を取得。
    Yahooの仕様上 period は分単位を受け付けないため、1営業日分(period="1d")を取得し
    後段で末尾60本に絞る（SMA50計算に十分な本数を確保）。
    """
    df = yf.download(
        tickers=ticker,
        interval="1m",
        period="1d",
        progress=False,
        auto_adjust=False,
        prepost=True,  # プレ/アフターも取得（必要に応じてFalseに）
        threads=True,
    )
    if df is None or df.empty:
        raise RuntimeError("データ取得に失敗しました（空データ）。")
    # 末尾60本のみ（SMA50計算に必要）
    df = df.tail(60).copy()
    # 列名を小文字統一（MultiIndex の場合は第1レベルを採用）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[0]).lower() for col in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df


def compute_sma_signals(df: pd.DataFrame) -> dict:
    """
    SMA20/50を計算し、直近バーでゴールデンクロス（上抜け）発生かを判定。
    signal: buy（上抜け）/ hold（それ以外）
    """
    # 安全のため終値を使用
    if "close" not in df.columns:
        raise RuntimeError("close列が見つかりません。")

    df["sma20"] = df["close"].rolling(window=20, min_periods=20).mean()
    df["sma50"] = df["close"].rolling(window=50, min_periods=50).mean()

    # 最新と1本前のSMAを取得
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None

    sma20_cur = float(last["sma20"]) if pd.notna(last["sma20"]) else None
    sma50_cur = float(last["sma50"]) if pd.notna(last["sma50"]) else None

    # 十分なデータが無い場合は hold
    if sma20_cur is None or sma50_cur is None or prev is None:
        signal = "hold"
    else:
        sma20_prev = float(prev["sma20"]) if pd.notna(prev["sma20"]) else None
        sma50_prev = float(prev["sma50"]) if pd.notna(prev["sma50"]) else None
        if sma20_prev is None or sma50_prev is None:
            signal = "hold"
        else:
            # 上抜け（ゴールデンクロス）のみを検知
            crossed_up = (sma20_prev < sma50_prev) and (sma20_cur >= sma50_cur)
            signal = "buy" if crossed_up else "hold"

    price = float(last["close"])
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal": signal,
        "price": round(price, 4),
        "sma20": round(sma20_cur, 6) if sma20_cur is not None else None,
        "sma50": round(sma50_cur, 6) if sma50_cur is not None else None,
    }
    return result


def append_json_line(path: Path, obj: dict) -> None:
    """
    1行1JSONで追記保存（ログローテ不要・取り込み容易）。
    既存ファイルがなければ作成。
    """
    line = json.dumps(obj, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    try:
        df = fetch_1m_data_last_60(TICKER)
        result = compute_sma_signals(df)
        append_json_line(OUTPUT_PATH, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        # エラー時もログに残す（signal=holdで保存）
        fallback = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "signal": "hold",
            "price": None,
            "sma20": None,
            "sma50": None,
            "error": str(e),
        }
        append_json_line(OUTPUT_PATH, fallback)
        # 失敗時は標準出力にも表示して終了コード1を返す
        print(json.dumps(fallback, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

