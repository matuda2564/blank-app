"""広告APIのデモ用サンプルデータ"""
import random


def get_demo_campaigns() -> list[dict]:
    return [
        {"campaignId": f"camp_{i}", "name": f"キャンペーン_{i+1}", "state": "enabled",
         "dailyBudget": 3000 + i * 500, "targetingType": "manual"}
        for i in range(5)
    ]


def get_demo_keyword_report() -> list[dict]:
    keywords = [
        ("登山靴", 120, 8, 45000, 15000),
        ("アウトドアシューズ", 80, 2, 12000, 0),
        ("トレッキングシューズ 防水", 200, 20, 30000, 120000),
        ("ハイキング 靴 メンズ", 50, 1, 8000, 5000),
        ("登山 装備", 300, 5, 20000, 0),
        ("山 靴 おすすめ", 90, 12, 18000, 90000),
        ("靴 防水 軽量", 60, 0, 5000, 0),
        ("アウトドア 靴 レディース", 150, 18, 27000, 135000),
    ]
    rows = []
    for i, (kw, imp, clicks, cost, sales) in enumerate(keywords):
        acos = round(cost / sales * 100, 1) if sales > 0 else None
        rows.append({
            "keywordId": f"kw_{i}",
            "keywordText": kw,
            "campaignId": f"camp_{i % 5}",
            "campaignName": f"キャンペーン_{(i % 5) + 1}",
            "impressions": imp,
            "clicks": clicks,
            "cost": cost,
            "attributedSales14d": sales,
            "attributedConversions14d": max(0, clicks // 6),
            "acos": acos,
            "ctr": round(clicks / imp * 100, 2) if imp > 0 else 0,
            "cpc": round(cost / clicks, 0) if clicks > 0 else 0,
        })
    return rows


def get_demo_listing(asin: str = "B00DEMO001") -> dict:
    return {
        "asin": asin,
        "title": "アウトドア登山靴 防水 軽量 メンズ レディース",
        "bulletPoints": [
            "防水素材使用",
            "軽量設計 片足約350g",
            "グリップ力の高いソール",
        ],
        "description": "山登りに最適な登山靴です。防水加工で雨の日も安心。",
        "mainImage": "https://example.com/main.jpg",
        "additionalImages": [],
        "price": 8980,
        "rating": 3.8,
        "reviewCount": 45,
        "category": "スポーツ&アウトドア > 登山・クライミング > 靴",
    }
