"""
Dropship catalog service — curated catalog + live AliExpress product resolution,
pricing, DAP disclosure, and product import (admin).
"""
import json
import os
from common import ok, err
import supplier
import pricing
import db


def h_health(event):
    provider = supplier.get_provider()
    return ok({"service": "dropship-catalog", "status": "ok", "supplier": provider.name})


def h_oauth_callback(event):
    """OAuth redirect target — exchanges code for token, stores in SSM."""
    import boto3
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code", "")
    print(f"[oauth] callback hit, code len={len(code)}")
    if not code:
        return err("missing code", 400)
    try:
        from ae_client import AliExpressClient
        c = AliExpressClient()
        tok = c.generate_token(code)
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
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        ssm.put_parameter(Name="/dropship/aliexpress/session", Value=access,
                          Type="SecureString", Overwrite=True)
        ssm.put_parameter(Name="/dropship/aliexpress/refresh_token",
                          Value=refresh, Type="SecureString", Overwrite=True)
        print("[oauth] token stored")
        return ok({"authorized": True, "provider": "aliexpress",
                   "expires_in": tok.get("expires_in", "")})
    except Exception as e:
        print(f"[oauth] exchange failed: {e}")
        return err(f"token exchange failed: {e}", 502)


def _resolve(prov, source_pid):
    """Resolve a curated product's live price from the supplier; fall back to cached."""
    p = prov.product_details(source_pid)
    if not p or "error" in p:
        return None
    return p


def h_list_products(event):
    qs = event.get("queryStringParameters") or {}
    q = (qs.get("q") or "").lower()
    prov = supplier.get_provider()
    curated = db.list_products()
    out = []
    for c in curated:
        p = _resolve(prov, c["source_product_id"])
        base = {
            "id": c["id"],
            "source_product_id": c["source_product_id"],
            "title": (c.get("title") or (p or {}).get("title") or "Product"),
            "image": c.get("image") or (p or {}).get("image") or "",
            "category": c.get("category", ""),
        }
        if p:
            source_cost = float(p.get("price", 0) or 0)
            ship = 5.0
            price = pricing.price_product(source_cost, ship)
            base["list_price"] = price.list_price
            base["source_cost"] = source_cost
        else:
            base["list_price"] = None
        if q:
            if q not in base["title"].lower() and q not in base["category"].lower():
                continue
        out.append(base)
    out = [o for o in out if o.get("list_price") is not None]

    # Demo fallback: if no curated products are resolvable yet (e.g. app still
    # in "Test" status), return mock products so the storefront is never empty.
    if not out:
        from supplier import MockProvider
        for p in MockProvider().search(""):
            price = pricing.price_product(p["price"], p["shipping"])
            out.append({"id": p["id"], "source_product_id": p["id"], "title": p["title"],
                        "image": "", "category": p["category"], "list_price": price.list_price,
                        "source_cost": p["price"]})
    return ok({"products": out})


def h_get_product(event, pid):
    prov = supplier.get_provider()
    c = db.get_product(pid)
    if not c:
        return err("not found", 404)
    p = _resolve(prov, c["source_product_id"])
    if not p:
        return err("product unavailable from supplier", 404)
    source_cost = float(p.get("price", 0) or 0)
    ship = 5.0
    price = pricing.price_product(source_cost, ship)
    return ok({"product": {
        "id": c["id"], "source_product_id": c["source_product_id"],
        "title": c.get("title") or p.get("title") or "Product",
        "image": c.get("image") or p.get("image") or "",
        "list_price": price.list_price, "source_cost": source_cost,
        "shipping": ship, "breakdown": price.__dict__, "dap": True,
    }})


def h_import_product(event):
    """Admin: curate a product by AliExpress product ID. Fetches live details + stores."""
    body = json.loads(event.get("body") or "{}")
    source_pid = body.get("product_id")
    if not source_pid:
        return err("product_id required")
    prov = supplier.get_provider()
    p = _resolve(prov, source_pid)
    if not p:
        return err("could not fetch product from supplier (unsaleable or invalid ID)", 404)
    rec = db.put_product(source_pid, title=body.get("title") or p.get("title") or "",
                         image=body.get("image") or p.get("image") or "",
                         category=body.get("category") or p.get("category") or "")
    return ok({"product": rec, "live": p})


def h_dap_disclosure(event):
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
    ("POST", "/import", h_import_product),
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
