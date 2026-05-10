import requests
from .auth import SPAPIAuth

SP_API_BASE = "https://sellingpartnerapi-fe.amazon.com"


class InvoicesAPI:
    """Amazon Easy Ship および Marketplace 配送伝票の取得"""

    def __init__(self, auth: SPAPIAuth):
        self.auth = auth

    def get_easy_ship_documents(self, amazon_order_id: str) -> bytes | None:
        """Easy Ship 配送伝票 (PDF) を取得する"""
        response = requests.get(
            f"{SP_API_BASE}/easyShip/2022-03-23/packages/documents",
            headers=self.auth.get_headers(),
            params={
                "amazonOrderId": amazon_order_id,
                "documentType": "ShippingLabel",
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()

        doc_url = data.get("payload", {}).get("fileContents", {}).get("contents")
        if not doc_url:
            return None

        doc_response = requests.get(doc_url)
        doc_response.raise_for_status()
        return doc_response.content

    def get_invoice_document(self, order_id: str) -> bytes | None:
        """注文の請求書PDFを取得する (Invoices API)"""
        response = requests.get(
            f"{SP_API_BASE}/tax/invoices/v1/invoices",
            headers=self.auth.get_headers(),
            params={
                "marketplaceId": self.auth.credentials.marketplace_id,
                "orderId": order_id,
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json().get("payload", {})

        download_url = payload.get("downloadUrl")
        if not download_url:
            return None

        doc_response = requests.get(download_url)
        doc_response.raise_for_status()
        return doc_response.content

    def get_shipment_label(self, shipment_id: str) -> bytes | None:
        """Merchant Fulfilled の配送ラベルを取得する"""
        response = requests.get(
            f"{SP_API_BASE}/shipping/v2/shipments/{shipment_id}/documents",
            headers=self.auth.get_headers(),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()

        contents = data.get("payload", {}).get("fileContents", {}).get("contents")
        if not contents:
            return None

        return contents.encode() if isinstance(contents, str) else contents
