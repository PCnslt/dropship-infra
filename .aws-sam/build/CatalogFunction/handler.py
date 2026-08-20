"""
Dropship catalog service — product search/detail, pricing, DAP disclosure.
Uses the shared layer (supplier, pricing) — provider-agnostic.
"""
import json
import os
from common import ok, err
import supplier
import pricing


def h_health(event):
    provider = supplier.get_provider()
    return ok({"service": "dropship-catalog", "status": "ok", "supplier": provider.name})


def h_oauth_callback(event):
    """OAuth redirect target. AliExpress redirects here with ?code=... after
    the app owner authorizes. We exchange the code for an access token and
    store it (SSM) so the provider flips to live."""
    import boto3
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code", "")
    print(f"[oauth] callback hit, code len={len(code)}, params={list(qs.keys())}")
    if not code:
        return err("missing code", 400)
    try:
        from ae_client import AliExpressClient
        c = AliExpressClient()
        tok = c.generate_token(code)
        print(f"[oauth] token exchange response keys={list(tok.keys()) if isinstance(tok, dict) else type(tok)}")
        print(f"[oauth] raw response (first 500): {json.dumps(tok)[:500]}")
        # robust extraction: try multiple common shapes
        access = ""
        refresh = ""
        if isinstance(tok, dict):
            for wrapper in (tok, tok.get("data", {}), tok.get("result", {}),
                            tok.get("aliexpress_auth_token_create_response", {}),
                            tok.get("response", {})):
                if isinstance(wrapper, dict) and wrapper.get("access_token"):
                    access = wrapper["access_token"]
                    refresh = wrapper.get("refresh_token", "")
                    break
        if not access:
            return err("token exchange returned no access_token: " + json.dumps(tok)[:400], 502)
        try:
            ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            ssm.put_parameter(Name="/dropship/aliexpress/session", Value=access,
                              Type="SecureString", Overwrite=True)
            ssm.put_parameter(Name="/dropship/aliexpress/refresh_token",
                              Value=refresh, Type="SecureString", Overwrite=True)
        except Exception as e:
            print(f"[oauth] SSM write failed: {e}")
            return err(f"token stored but SSM write failed: {e}", 500)
        print("[oauth] token stored successfully")
        return ok({"authorized": True, "provider": "aliexpress",
                   "expires_in": tok.get("expires_in", "")})
    except Exception as e:
        print(f"[oauth] token exchange exception: {e}")
        return err(f"token exchange failed: {e}", 502)


def h_list_products(event):
    qs = event.get("queryStringParameters") or {}
    q = qs.get("q", "")
    prov = supplier.get_provider()
    if prov.name == "mock":
        products = prov.search(q)
        out = []
        for p in products:
            price = pricing.price_product(p["price"], p["shipping"])
            out.append({**p, "list_price": price.list_price, "list_currency": price.currency})
        return ok({"products": out})
    # real provider: no keyword search in dropship API without query; return empty
    return ok({"products": []})


def h_get_product(event, pid):
    prov = supplier.get_provider()
    p = prov.product_details(pid)
    if not p or "error" in p:
        return err("not found", 404)
    price = pricing.price_product(float(p.get("price", 0)), float(p.get("shipping", 0)))
    return ok({"product": {**p, "list_price": price.list_price,
                           "list_currency": price.currency, "breakdown": price.__dict__,
                           "dap": True}})


def h_dap_disclosure(event):
    """Return the DAP (Delivered At Place) disclosure text shown at checkout."""
    return ok({
        "dap": True,
        "disclosure": "Import duties, taxes, and customs fees are the buyer's "
                      "responsibility and are not included in the item or shipping "
                      "price. These charges may be collected by the carrier or "
                      "customs at delivery.",
        "shipping_note": "Ships from our warehouse · 10–20 business days · tracked",
    })


ROUTES = [
    ("GET", "/health", h_health),
    ("GET", "/products", h_list_products),
    ("GET", "/dap", h_dap_disclosure),
    ("GET", "/oauth/callback", h_oauth_callback),
]

PARAM_ROUTES = [
    ("GET", "/products/", h_get_product),
]


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return ok({"ok": True})
    path = event.get("requestContext", {}).get("http", {}).get("path", "/")
    for m, p, fn in ROUTES:
        if method == m and path == p:
            return fn(event)
    for m, prefix, fn in PARAM_ROUTES:
        if method == m and path.startswith(prefix):
            param = path[len(prefix):]
            if param and "/" not in param:
                return fn(event, param)
    return err(f"no route: {method} {path}", 404)
