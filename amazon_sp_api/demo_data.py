"""SP-API申請中でも動作確認できるサンプルデータ"""
from datetime import datetime, timedelta


def get_demo_orders() -> list[dict]:
    base = datetime(2026, 5, 1)
    return [
        {
            "AmazonOrderId": f"503-{1000000 + i}-{2000000 + i}",
            "PurchaseDate": (base + timedelta(days=i)).isoformat(),
            "OrderStatus": "Shipped",
            "FulfillmentChannel": "MFN",
            "SalesChannel": "Amazon.co.jp",
            "ShipServiceLevel": "JP_Std",
            "OrderTotal": {"CurrencyCode": "JPY", "Amount": str(1500 + i * 300)},
            "NumberOfItemsShipped": 1,
            "NumberOfItemsUnshipped": 0,
            "PaymentMethod": "Other",
            "MarketplaceId": "A1VC38T7YXB528",
            "ShipmentServiceLevelCategory": "Standard",
            "OrderType": "StandardOrder",
            "BuyerInfo": {},
            "IsBusinessOrder": False,
            "IsPrime": False,
            "IsGlobalExpressEnabled": False,
            "IsPremiumOrder": False,
            "IsSOB": False,
            "DefaultShipFromLocationAddress": {
                "Name": "出荷元",
                "AddressLine1": "東京都渋谷区1-1-1",
                "City": "渋谷区",
                "StateOrRegion": "東京都",
                "PostalCode": "150-0001",
                "CountryCode": "JP",
            },
            "ShippingAddress": {
                "Name": f"テスト顧客{i+1}",
                "AddressLine1": f"大阪府大阪市{i+1}-{i+1}-{i+1}",
                "City": "大阪市",
                "StateOrRegion": "大阪府",
                "PostalCode": f"5300{i:04d}",
                "CountryCode": "JP",
            },
        }
        for i in range(10)
    ]


def get_demo_order_items(order_id: str) -> list[dict]:
    return [
        {
            "ASIN": f"B00DEMO{order_id[-4:]}",
            "OrderItemId": f"item-{order_id}",
            "Title": f"サンプル商品 ({order_id[-6:]})",
            "QuantityOrdered": 1,
            "QuantityShipped": 1,
            "ItemPrice": {"CurrencyCode": "JPY", "Amount": "1500"},
            "ItemTax": {"CurrencyCode": "JPY", "Amount": "150"},
            "ConditionId": "New",
            "ConditionSubtypeId": "New",
        }
    ]
