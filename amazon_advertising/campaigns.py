import gzip
import io
import json
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from .auth import AdvAuth

API_BASE = "https://advertising-api-fe.amazon.com"


class CampaignsAPI:
    def __init__(self, auth: AdvAuth):
        self.auth = auth

    def list_campaigns(self) -> list[dict]:
        resp = requests.get(
            f"{API_BASE}/v2/sp/campaigns",
            headers=self.auth.get_headers(),
            params={"stateFilter": "enabled,paused"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_ad_groups(self, campaign_id: Optional[str] = None) -> list[dict]:
        params = {"stateFilter": "enabled,paused"}
        if campaign_id:
            params["campaignIdFilter"] = campaign_id
        resp = requests.get(
            f"{API_BASE}/v2/sp/adGroups",
            headers=self.auth.get_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def list_keywords(self, campaign_id: Optional[str] = None) -> list[dict]:
        params = {"stateFilter": "enabled,paused"}
        if campaign_id:
            params["campaignIdFilter"] = campaign_id
        resp = requests.get(
            f"{API_BASE}/v2/sp/keywords",
            headers=self.auth.get_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def request_report(self, report_date: str) -> str:
        """日次パフォーマンスレポートをリクエストし reportId を返す"""
        body = {
            "reportDate": report_date,
            "metrics": "impressions,clicks,cost,attributedSales14d,attributedConversions14d",
            "segment": "query",
        }
        resp = requests.post(
            f"{API_BASE}/v2/sp/keywords/report",
            headers=self.auth.get_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["reportId"]

    def download_report(self, report_id: str, max_wait: int = 120) -> list[dict]:
        """レポート完成を待ってダウンロードし dict のリストを返す"""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            resp = requests.get(
                f"{API_BASE}/v2/reports/{report_id}",
                headers=self.auth.get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            if data["status"] == "SUCCESS":
                doc = requests.get(data["location"])
                doc.raise_for_status()
                raw = gzip.decompress(doc.content)
                return json.loads(raw)
            if data["status"] == "FAILURE":
                raise RuntimeError(f"レポート生成失敗: {data}")
            time.sleep(5)
        raise TimeoutError("レポートのダウンロードがタイムアウトしました")

    def get_yesterday_report(self) -> list[dict]:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        report_id = self.request_report(yesterday)
        return self.download_report(report_id)

    def update_keyword_bid(self, keyword_id: str, new_bid: float) -> dict:
        resp = requests.put(
            f"{API_BASE}/v2/sp/keywords",
            headers=self.auth.get_headers(),
            json=[{"keywordId": keyword_id, "bid": round(new_bid, 2)}],
        )
        resp.raise_for_status()
        return resp.json()

    def update_campaign_budget(self, campaign_id: str, new_budget: float) -> dict:
        resp = requests.put(
            f"{API_BASE}/v2/sp/campaigns",
            headers=self.auth.get_headers(),
            json=[{"campaignId": campaign_id, "dailyBudget": round(new_budget, 2)}],
        )
        resp.raise_for_status()
        return resp.json()

    def pause_keyword(self, keyword_id: str) -> dict:
        resp = requests.put(
            f"{API_BASE}/v2/sp/keywords",
            headers=self.auth.get_headers(),
            json=[{"keywordId": keyword_id, "state": "paused"}],
        )
        resp.raise_for_status()
        return resp.json()
