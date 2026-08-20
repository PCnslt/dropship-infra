"""
AliExpress Open Platform — DropShipping API client (Python).

Implements the request signing + endpoints for the AliExpress Open Platform.
Ports the logic from the reference ae_sdk (Node) to Python. Uses only stdlib.

Base URLs:
  - OP API   : https://api-sg.aliexpress.com/rest   (methods contain "/")
  - TOP API  : https://api-sg.aliexpress.com/sync   (methods like aliexpress.ds.product.get)

Auth: /auth/token/create (code -> access_token+refresh_token), /auth/token/refresh.
A "session" (access token) is required for dropshipping calls; obtained via a
one-time OAuth authorization flow where the app owner logs in and approves.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.request
import urllib.parse

OP_API_URL = "https://api-sg.aliexpress.com/rest"
TOP_API_URL = "https://api-sg.aliexpress.com/sync"


class AliExpressError(Exception):
    pass


class AliExpressClient:
    def __init__(self, app_key=None, app_secret=None, session=None):
        self.app_key = app_key or os.environ.get("ALIEXPRESS_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("ALIEXPRESS_APP_SECRET", "")
        self.session = session or os.environ.get("ALIEXPRESS_SESSION", "")

    # ---------- signing ----------
    def _sign(self, params):
        """HMAC-SHA256(app_secret) over (method if OP) + sorted key+value concat."""
        p = dict(params)
        basestring = ""
        method = p.get("method", "")
        if isinstance(method, str) and "/" in method:
            basestring = method
            del p["method"]
        for k in sorted(p.keys()):
            v = p[k]
            if v is not None:
                basestring += k + str(v)
        return hmac.new(self.app_secret.encode("utf-8"), basestring.encode("utf-8"),
                        hashlib.sha256).hexdigest().upper()

    def _assemble(self, params):
        p = dict(params)
        method = p.get("method", "")
        is_op = isinstance(method, str) and "/" in method
        base = OP_API_URL + method if is_op else TOP_API_URL
        if is_op:
            del p["method"]
        parts = []
        for i, k in enumerate(sorted(p.keys())):
            v = p[k]
            if v is None:
                continue
            prefix = "?" if not parts else "&"
            parts.append(f"{prefix}{k}={urllib.parse.quote(str(v))}")
        return base + "".join(parts)

    def _call(self, method, params=None, timeout=30):
        if not self.app_key or not self.app_secret:
            raise AliExpressError("app_key/app_secret not configured")
        p = dict(params or {})
        p["method"] = method
        p["session"] = self.session
        p["app_key"] = self.app_key
        p["simplify"] = "true"
        p["sign_method"] = "sha256"
        p["timestamp"] = str(int(time.time() * 1000))
        p["sign"] = self._sign(p)
        url = self._assemble(p)
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise AliExpressError(f"HTTP {e.code}: {body[:300]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise AliExpressError(f"non-JSON response: {body[:300]}")
        if data.get("error_response"):
            raise AliExpressError(json.dumps(data["error_response"])[:400])
        return data

    # ---------- auth ----------
    def generate_token(self, code):
        return self._call("/auth/token/create", {"code": code})

    def refresh_token(self, refresh_token):
        return self._call("/auth/token/refresh", {"refresh_token": refresh_token})

    # ---------- dropshipping ----------
    def product_details(self, product_id, ship_to_country="US",
                        target_currency="USD", target_language="en"):
        return self._call("aliexpress.ds.product.get", {
            "product_id": product_id,
            "ship_to_country": ship_to_country,
            "target_currency": target_currency,
            "target_language": target_language,
        })

    def freight_calculate(self, product_id, quantity=1, country="US", province=""):
        """Shipping cost for a product to a destination."""
        dto = {
            "product_id": product_id,
            "quantity": quantity,
            "country": country,
            "province": province,
        }
        return self._call("aliexpress.logistics.buyer.freight.calculate", {
            "param_aeop_freight_calculate_for_buyer_d_t_o": json.dumps(dto),
        })

    def create_order(self, logistics_address, product_items):
        """Place a dropship order. Returns order id."""
        return self._call("aliexpress.ds.order.create", {
            "param_place_order_request4_open_api_d_t_o": json.dumps({
                "logistics_address": logistics_address,
                "product_items": product_items,
            }),
        })

    def order_details(self, order_id):
        return self._call("aliexpress.trade.ds.order.get", {"order_id": order_id})

    def categories(self):
        return self._call("aliexpress.ds.category.get", {})


def get_client():
    """Return a client from env vars. Raises if creds missing."""
    return AliExpressClient()
