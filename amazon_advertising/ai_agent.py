"""
AIエージェント: 広告・カタログのデータを分析し改善提案を生成する
OpenAI GPT-4o / Google Gemini 1.5 Flash に対応
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProposalType(str, Enum):
    LOWER_BID = "入札額を下げる"
    RAISE_BID = "入札額を上げる"
    PAUSE_KEYWORD = "キーワードを停止"
    ADJUST_BUDGET = "予算を調整"
    LISTING_TITLE = "タイトルを改善"
    LISTING_BULLETS = "箇条書きを改善"
    LISTING_DESCRIPTION = "説明文を改善"
    LISTING_IMAGE = "画像を追加・改善"


@dataclass
class Proposal:
    proposal_type: ProposalType
    target_id: str
    target_name: str
    reason: str
    current_value: Any
    suggested_value: Any
    expected_effect: str
    approved: bool = False
    rejected: bool = False
    executed: bool = False
    extra: dict = field(default_factory=dict)


_AD_ANALYSIS_SYSTEM = """
あなたはAmazonスポンサープロダクト広告の最適化エキスパートです。
キーワードの日次パフォーマンスデータを分析し、具体的な改善提案を JSON 形式で出力してください。

出力フォーマット（JSON配列）:
[
  {
    "proposal_type": "入札額を下げる" | "入札額を上げる" | "キーワードを停止" | "予算を調整",
    "target_id": "keywordId または campaignId",
    "target_name": "キーワード文字列またはキャンペーン名",
    "reason": "判断理由（日本語・具体的に）",
    "current_value": 現在の入札額または予算（数値）,
    "suggested_value": 推奨値（数値）,
    "expected_effect": "期待される効果（日本語）"
  }
]

判断基準:
- ACOS > 目標ACOSの場合: 入札額を10〜20%下げる
- ACOS < 目標ACOSの半分かつクリック数 > 5: 入札額を10〜15%上げる
- クリック数 = 0 かつ インプレッション > 50: キーワードを停止
- 表示回数が非常に少ない重要キーワード: 入札額を上げる
- JSON以外は出力しないこと
"""

_LISTING_ANALYSIS_SYSTEM = """
あなたはAmazon商品リスティング最適化のエキスパートです。
商品情報を分析し、売上アップのための具体的な改善提案をJSON形式で出力してください。

出力フォーマット（JSON配列）:
[
  {
    "proposal_type": "タイトルを改善" | "箇条書きを改善" | "説明文を改善" | "画像を追加・改善",
    "target_id": "asin",
    "target_name": "商品タイトル（短縮）",
    "reason": "改善が必要な理由（日本語）",
    "current_value": "現在のテキスト（省略可）",
    "suggested_value": "改善後の具体的なテキスト",
    "expected_effect": "期待される効果"
  }
]

JSON以外は出力しないこと。
"""


def _call_openai(api_key: str, system: str, user_msg: str) -> list[dict]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    raw = resp.choices[0].message.content
    data = json.loads(raw)
    return data if isinstance(data, list) else data.get("proposals", list(data.values())[0])


def _call_gemini(api_key: str, system: str, user_msg: str) -> list[dict]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system,
    )
    resp = model.generate_content(user_msg)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def analyze_ads(
    keyword_rows: list[dict],
    target_acos: float = 25.0,
    ai_provider: str = "openai",
    api_key: str = "",
) -> list[Proposal]:
    """広告キーワードデータを分析して改善提案リストを返す"""
    user_msg = (
        f"目標ACOS: {target_acos}%\n\n"
        f"キーワードデータ:\n{json.dumps(keyword_rows, ensure_ascii=False, indent=2)}"
    )

    if ai_provider == "openai":
        raw = _call_openai(api_key, _AD_ANALYSIS_SYSTEM, user_msg)
    else:
        raw = _call_gemini(api_key, _AD_ANALYSIS_SYSTEM, user_msg)

    proposals = []
    for item in raw:
        try:
            proposals.append(
                Proposal(
                    proposal_type=ProposalType(item["proposal_type"]),
                    target_id=str(item["target_id"]),
                    target_name=item["target_name"],
                    reason=item["reason"],
                    current_value=item["current_value"],
                    suggested_value=item["suggested_value"],
                    expected_effect=item["expected_effect"],
                )
            )
        except (KeyError, ValueError):
            continue
    return proposals


def analyze_listing(
    listing: dict,
    ai_provider: str = "openai",
    api_key: str = "",
) -> list[Proposal]:
    """商品リスティングを分析して改善提案リストを返す"""
    user_msg = f"商品情報:\n{json.dumps(listing, ensure_ascii=False, indent=2)}"

    if ai_provider == "openai":
        raw = _call_openai(api_key, _LISTING_ANALYSIS_SYSTEM, user_msg)
    else:
        raw = _call_gemini(api_key, _LISTING_ANALYSIS_SYSTEM, user_msg)

    proposals = []
    for item in raw:
        try:
            proposals.append(
                Proposal(
                    proposal_type=ProposalType(item["proposal_type"]),
                    target_id=str(item["target_id"]),
                    target_name=item["target_name"],
                    reason=item["reason"],
                    current_value=item.get("current_value", ""),
                    suggested_value=item["suggested_value"],
                    expected_effect=item["expected_effect"],
                )
            )
        except (KeyError, ValueError):
            continue
    return proposals


def get_demo_ad_proposals() -> list[Proposal]:
    """デモ用の固定提案（API不要）"""
    return [
        Proposal(
            proposal_type=ProposalType.LOWER_BID,
            target_id="kw_0",
            target_name="登山靴",
            reason="ACOS 300%と目標の12倍。費用対効果が著しく悪いため入札額を下げます",
            current_value=200,
            suggested_value=80,
            expected_effect="ACOS を目標値付近まで改善。月間広告費を約¥18,000削減見込み",
        ),
        Proposal(
            proposal_type=ProposalType.PAUSE_KEYWORD,
            target_id="kw_1",
            target_name="アウトドアシューズ",
            reason="80インプレッションに対してクリック0。検索意図が商品と一致していない可能性",
            current_value="enabled",
            suggested_value="paused",
            expected_effect="無駄な広告費をゼロに。予算をROI良好なキーワードへ集中",
        ),
        Proposal(
            proposal_type=ProposalType.RAISE_BID,
            target_id="kw_2",
            target_name="トレッキングシューズ 防水",
            reason="ACOS 25%と目標値内。クリック率・転換率ともに良好。入札額を上げて露出を拡大",
            current_value=150,
            suggested_value=200,
            expected_effect="表示回数・売上の増加。月間売上+¥30,000〜50,000見込み",
        ),
        Proposal(
            proposal_type=ProposalType.PAUSE_KEYWORD,
            target_id="kw_6",
            target_name="靴 防水 軽量",
            reason="60インプレッションに対してクリック0。競合が多く入札競争に負けている",
            current_value="enabled",
            suggested_value="paused",
            expected_effect="予算の無駄遣いを防止",
        ),
    ]


def get_demo_listing_proposals() -> list[Proposal]:
    """デモ用のリスティング改善提案"""
    return [
        Proposal(
            proposal_type=ProposalType.LISTING_TITLE,
            target_id="B00DEMO001",
            target_name="アウトドア登山靴",
            reason="タイトルが短く主要キーワードが不足。検索流入が限定的になっている",
            current_value="アウトドア登山靴 防水 軽量 メンズ レディース",
            suggested_value="【防水・軽量350g】登山靴 トレッキングシューズ メンズ レディース アウトドア ハイキング 滑りにくい グリップ力 ソール 幅広 3E",
            expected_effect="タイトルへのキーワード追加で検索露出が増加。クリック率改善見込み",
        ),
        Proposal(
            proposal_type=ProposalType.LISTING_BULLETS,
            target_id="B00DEMO001",
            target_name="アウトドア登山靴",
            reason="箇条書きが3項目と少なく、具体的なスペックや差別化ポイントが伝わっていない",
            current_value="防水素材使用 / 軽量設計 / グリップ力の高いソール",
            suggested_value="【完全防水】防水透湿素材採用。雨の日・沢渡りも安心 / 【超軽量350g】長時間歩行でも疲れにくい / 【ハイグリップソール】濡れた岩場・泥道も滑りにくい / 【幅広3E設計】日本人の足型に最適 / 【男女兼用】メンズ・レディース共用サイズ展開",
            expected_effect="購買意欲を高める情報を追加。コンバージョン率の改善",
        ),
        Proposal(
            proposal_type=ProposalType.LISTING_IMAGE,
            target_id="B00DEMO001",
            target_name="アウトドア登山靴",
            reason="サブ画像が0枚。競合は6〜7枚の画像を使用しており差がある",
            current_value="メイン画像1枚のみ",
            suggested_value="追加推奨: ①靴底(ソール)のアップ ②防水性能(水をかける)デモ ③軽量さを示す重さ計 ④着用イメージ(山道) ⑤サイズ表 ⑥梱包内容",
            expected_effect="購入前の不安解消・信頼感向上。コンバージョン率+15〜20%見込み",
        ),
    ]
