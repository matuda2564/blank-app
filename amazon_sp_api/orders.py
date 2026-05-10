from datetime import datetime, timezone
from typing import Optional
import requests

from .auth import SPAPIAuth


SP_API_BASE = "https://sellingpartnerapi-fe.amazon.com"


class OrdersAPI:
    def __init__(self, auth: SPAPIAuth):
        self.auth = auth

    def get_orders(
        self,
        created_after: datetime,
        created_before: Optional[datetime] = None,
        order_statuses: Optional[list[str]] = None,
        max_results: int = 100,
    ) -> list[dict]:
        params = {
            "MarketplaceIds": self.auth.credentials.marketplace_id,
            "CreatedAfter": created_after.astimezone(timezone.utc).isoformat(),
            "MaxResultsPerPage": min(max_results, 100),
        }

        if created_before:
            params["CreatedBefore"] = created_before.astimezone(timezone.utc).isoformat()

        if order_statuses:
            params["OrderStatuses"] = ",".join(order_statuses)

        orders = []
        next_token = None

        while True:
            if next_token:
                params["NextToken"] = next_token

            response = requests.get(
                f"{SP_API_BASE}/orders/v0/orders",
                headers=self.auth.get_headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            payload = data.get("payload", {})
            orders.extend(payload.get("Orders", []))

            next_token = payload.get("NextToken")
            if not next_token or len(orders) >= max_results:
                break

        return orders[:max_results]

    def get_order_items(self, order_id: str) -> list[dict]:
        response = requests.get(
            f"{SP_API_BASE}/orders/v0/orders/{order_id}/orderItems",
            headers=self.auth.get_headers(),
        )
        response.raise_for_status()
        return response.json().get("payload", {}).get("OrderItems", [])
