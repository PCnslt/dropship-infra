"""
AliExpress Open Platform — DropShipping API client (Python).

Implements the GOP protocol request signing for the AliExpress Open Platform.

Base URLs:
  - GOP/REST : https://api-sg.aliexpress.com/rest   (methods with leading "/")
  - TOP      : https://api-sg.aliexpress.com/sync   (methods like aliexpress.ds.product.get)

Signing (GOP, verified against official docs + working reference):
  basestring = method + "".join(sorted("key"+value for each param))
  where params = {app_key, timestamp, sign_method, <api params>}  (NO session when
  empty, NO simplify). sign = HMAC-SHA256(basestring, app_secret).hex().upper().

Auth: /auth/token/create (code -> access_token+refresh_token), /auth/token/refresh.
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
    def _sign(self, method, params):
        """HMAC-SHA256(app_secret).

        GOP (method starts with "/"): basestring = method + sorted(key+value) of
          params (method NOT in params).
        TOP (method like "aliexpress.ds.product.get"): basestring = sorted(key+value)
          of params INCLUDING method as a key.
        """
        if method.startswith("/"):
            basestring = method
            items = params
        else:
            basestring = ""
            items = {**params, "method": method}
        for k in sorted(items.keys()):
            v = items[k]
            if v is None:
                continue
            basestring += k + str(v)
        return hmac.new(self.app_secret.encode("utf-8"), basestring.encode("utf-8"),
                        hashlib.sha256).hexdigest().upper()

    def _call(self, method, params=None, timeout=30):
        if not self.app_key or not self.app_secret:
            raise AliExpressError("app_key/app_secret not configured")
        p = dict(params or {})
        p["app_key"] = self.app_key
        p["sign_method"] = "sha256"
        p["timestamp"] = str(int(time.time() * 1000))
        # only include session if present (token calls have none)
        if self.session:
            p["session"] = self.session
        p["sign"] = self._sign(method, p)

        # build URL: GOP uses rest+method; TOP uses /sync with method as a param
        is_op = method.startswith("/")
        if is_op:
            url = OP_API_URL + method
            query = p  # method NOT in query for GOP
        else:
            url = TOP_API_URL
            query = {**p, "method": method}

        parts = []
        for k in sorted(query.keys()):
            parts.append(f"{k}={urllib.parse.quote(str(query[k]))}")
        full = url + "?" + "&".join(parts)

        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise AliExpressError(f"HTTP {e.code}: {body[:400]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise AliExpressError(f"non-JSON response: {body[:400]}")
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
        dto = {"product_id": product_id, "quantity": quantity,
               "country": country, "province": province}
        return self._call("aliexpress.logistics.buyer.freight.calculate", {
            "param_aeop_freight_calculate_for_buyer_d_t_o": json.dumps(dto),
        })

    def create_order(self, logistics_address, product_items):
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
    return AliExpressClient()
