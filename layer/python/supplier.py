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
        return self.client.product_details(product_id, **kw)

    def create_order(self, logistics_address, product_items):
        return self.client.create_order(logistics_address, product_items)

    def order_details(self, order_id):
        return self.client.order_details(order_id)


def get_provider():
    if os.environ.get("ALIEXPRESS_SESSION"):
        return AliExpressProvider()
    return MockProvider()
