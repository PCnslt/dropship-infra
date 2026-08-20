"""
Supplier abstraction — AliExpress ↔ Mock provider, mirrors payments.py/carriers.py.

The rest of the system calls supplier.get_provider() and never knows whether it's
talking to the real AliExpress API or the mock. When ALIEXPRESS_SESSION is set
(one-time OAuth done), it returns AliExpressProvider; otherwise MockProvider so
the entire marketplace works end-to-end during build/test.

Mock provider returns deterministic fake products/orders so we can build & test
the full storefront + fulfillment pipeline with zero external dependency.
"""
import os
import time

from ae_client import AliExpressClient


class MockProvider:
    name = "mock"
    _products = {
        "mock-1001": {"id": "mock-1001", "title": "Wireless Noise-Cancelling Headphones",
                      "price": 38.0, "currency": "USD", "image": "", "shipping": 4.5,
                      "source": "aliexpress", "category": "electronics"},
        "mock-1002": {"id": "mock-1002", "title": "Smart Watch Fitness Tracker",
                      "price": 42.0, "currency": "USD", "image": "", "shipping": 4.0,
                      "source": "aliexpress", "category": "electronics"},
        "mock-1003": {"id": "mock-1003", "title": "Portable Blender Bottle",
                      "price": 31.0, "currency": "USD", "image": "", "shipping": 5.0,
                      "source": "aliexpress", "category": "home"},
    }

    def product_details(self, product_id, **kw):
        p = self._products.get(product_id)
        if not p:
            return {"error": "not found"}
        return dict(p)

    def search(self, query=""):
        all_p = list(self._products.values())
        if not query:
            return all_p
        q = query.lower()
        return [p for p in all_p if q in p["title"].lower()]

    def create_order(self, logistics_address, product_items):
        oid = "mock-order-" + str(int(time.time()))
        return {"order_id": oid, "status": "pending"}

    def order_details(self, order_id):
        return {"order_id": order_id, "status": "shipped", "tracking": "MOCK-TRACK-123"}


class AliExpressProvider:
    name = "aliexpress"

    def __init__(self):
        self.client = AliExpressClient()

    @property
    def enabled(self):
        return bool(self.client.session)

    def product_details(self, product_id, **kw):
        raw = self.client.product_details(product_id, **kw)
        return _map_product(raw)

    def create_order(self, logistics_address, product_items):
        return self.client.create_order(logistics_address, product_items)

    def order_details(self, order_id):
        return self.client.order_details(order_id)


def _map_product(raw):
    """Map AliExpress `aliexpress.ds.product.get` nested response to a flat product dict."""
    if not isinstance(raw, dict):
        return {"error": "bad response"}
    rsp = raw.get("aliexpress_ds_product_get_response", raw)
    if not isinstance(rsp, dict):
        return {"error": "bad response shape"}
    code = rsp.get("rsp_code", rsp.get("code"))
    if code not in (None, 0, "0", 200, "200"):
        return {"error": rsp.get("rsp_msg") or f"product unavailable (code {code})"}
    result = rsp.get("result") or {}
    subject = result.get("subject") or result.get("product_title") or ""
    # price: try common fields
    price = result.get("ws_display") or result.get("target_sale_price") or result.get("sale_price")
    if not price and result.get("ae_item_sku_info_dtos"):
        skus = result.get("ae_item_sku_info_dtos", [])
        if isinstance(skus, list) and skus and isinstance(skus[0], dict):
            price = skus[0].get("sku_price") or skus[0].get("price")
    img = ""
    if result.get("ae_item_properties"):
        pass  # images often in a separate multimedia field; leave empty fallback
    return {
        "id": str(result.get("product_id") or result.get("item_id") or ""),
        "title": subject,
        "price": float(price or 0),
        "currency": result.get("currency_code", "USD"),
        "image": img,
        "source": "aliexpress",
        "category": result.get("category_id", ""),
    }


def get_provider():
    # token can come from env OR SSM (set by the OAuth callback). Check SSM at
    # runtime so the provider flips to live without a redeploy.
    session = os.environ.get("ALIEXPRESS_SESSION", "")
    if not session:
        try:
            import boto3
            ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            r = ssm.get_parameter(Name="/dropship/aliexpress/session", WithDecryption=True)
            session = r["Parameter"]["Value"]
        except Exception:
            session = ""
    if session:
        os.environ["ALIEXPRESS_SESSION"] = session
        return AliExpressProvider()
    return MockProvider()
