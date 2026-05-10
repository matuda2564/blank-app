import requests
from .auth import SPAPIAuth

SP_API_BASE = "https://sellingpartnerapi-fe.amazon.com"


class ListingsAPI:
    def __init__(self, auth: SPAPIAuth):
        self.auth = auth

    def get_item(self, asin: str) -> dict:
        resp = requests.get(
            f"{SP_API_BASE}/catalog/2022-04-01/items/{asin}",
            headers=self.auth.get_headers(),
            params={
                "marketplaceIds": self.auth.credentials.marketplace_id,
                "includedData": "summaries,attributes,images",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def _patch_listing(self, asin: str, seller_id: str, sku: str, patches: list[dict]) -> dict:
        resp = requests.patch(
            f"{SP_API_BASE}/listings/2021-08-01/items/{seller_id}/{sku}",
            headers=self.auth.get_headers(),
            params={"marketplaceIds": self.auth.credentials.marketplace_id},
            json={
                "productType": "SHOES",
                "patches": patches,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def update_title(self, asin: str, title: str, seller_id: str = "", sku: str = "") -> dict:
        return self._patch_listing(asin, seller_id, sku, [
            {"op": "replace", "path": "/attributes/item_name", "value": [{"value": title, "language_tag": "ja_JP"}]}
        ])

    def update_bullet_points(self, asin: str, bullets: list[str], seller_id: str = "", sku: str = "") -> dict:
        value = [{"value": b, "language_tag": "ja_JP"} for b in bullets]
        return self._patch_listing(asin, seller_id, sku, [
            {"op": "replace", "path": "/attributes/bullet_point", "value": value}
        ])

    def update_description(self, asin: str, description: str, seller_id: str = "", sku: str = "") -> dict:
        return self._patch_listing(asin, seller_id, sku, [
            {"op": "replace", "path": "/attributes/product_description", "value": [{"value": description, "language_tag": "ja_JP"}]}
        ])
