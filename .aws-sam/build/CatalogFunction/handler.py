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
    feed = qs.get("feed") or ""
    category_id = qs.get("category_id") or ""
    prov = supplier.get_provider()

    # Real provider: pull live products from AliExpress feed (or curated catalog).
    if prov.name == "aliexpress":
        try:
            cache_key = category_id or feed or "default"
            cached = db.feed_cache_fresh(cache_key, max_age=300)
            if cached is not None:
                # already-mapped products cached; apply query filter + return
                out = [p for p in cached if not q or (q in p["title"].lower() or q in (p.get("category", "") or "").lower())]
                return ok({"products": out})
            if category_id:
                raw = prov.search_by_category(category_id, page_size="20", country=qs.get("country", "US"))
            elif feed:
                raw = prov.search(feed_name=feed, page_size="20", country=qs.get("country", "US"))
            else:
                # if curated catalog has items, resolve them; else pull default feed
                curated = db.list_products()
                if curated:
                    raw = []
                    for c in curated:
                        p = prov.product_details(c["source_product_id"])
                        if p and "error" not in p:
                            raw.append({"id": c["id"], "source_product_id": c["source_product_id"],
                                        "title": c.get("title") or p.get("title"),
                                        "price": p.get("price", 0),
                                        "image": c.get("image") or p.get("image"),
                                        "category": c.get("category", "")})
                else:
                    raw = prov.search(feed_name="DS_ConsumerElectronics_bestsellers", page_size="20", country="US")
            out = []
            for p in raw:
                source_cost = float(p.get("price", 0) or 0)
                price = pricing.price_product(source_cost, 5.0)
                title = p.get("title", "")
                out.append({"id": p.get("id"), "source_product_id": p.get("source_product_id"),
                            "title": title, "image": p.get("image", ""),
                            "category": p.get("category", ""),
                            "rating": p.get("rating", ""), "volume": p.get("volume", 0),
                            "discount": p.get("discount", ""),
                            "list_price": price.list_price, "source_cost": source_cost,
                            "list_currency": price.currency})
            db.put_feed_cache(cache_key, out)
            out = [p for p in out if not q or (q in p["title"].lower() or q in (p.get("category", "") or "").lower())]
            return ok({"products": out})
        except Exception as e:
            print(f"[catalog] live products failed, fallback: {e}")
            # fall through to demo

    # Demo fallback (or mock provider)
    from supplier import MockProvider
    out = []
    for p in MockProvider().search(""):
        price = pricing.price_product(p["price"], p["shipping"])
        if q and q not in p["title"].lower():
            continue
        out.append({"id": p["id"], "source_product_id": p["id"], "title": p["title"],
                    "image": "", "category": p["category"], "list_price": price.list_price,
                    "source_cost": p["price"]})
    return ok({"products": out})


def _find_in_feed_cache(pid):
    """Search the feed cache (all keys) for a product by id or source_product_id."""
    try:
        items = db._scan("begins_with(PK, :p)", {}, {":p": "CACHE#feed"})
    except Exception:
        return None
    for c in items:
        prods = c.get("products", []) or []
        for p in prods:
            if str(p.get("id")) == str(pid) or str(p.get("source_product_id")) == str(pid):
                return p
    return None


def h_get_product(event, pid):
    prov = supplier.get_provider()
    # 1) feed cache (rich data for recommended products) — fastest + full data
    cached = _find_in_feed_cache(pid)
    if cached:
        return ok({"product": {
            "id": cached.get("id"), "source_product_id": cached.get("source_product_id"),
            "title": cached.get("title", ""), "image": cached.get("image", ""),
            "category": cached.get("category", ""), "rating": cached.get("rating", ""),
            "volume": cached.get("volume", 0), "discount": cached.get("discount", ""),
            "list_price": cached.get("list_price", 0), "source_cost": cached.get("source_cost", 0),
            "shipping": 5.0, "dap": True,
        }})
    # 2) curated catalog
    c = db.get_product(pid)
    if c:
        p = _resolve(prov, c["source_product_id"])
        if p and "error" not in p:
            source_cost = float(p.get("price", 0) or 0)
            price = pricing.price_product(source_cost, 5.0)
            return ok({"product": {
                "id": c["id"], "source_product_id": c["source_product_id"],
                "title": c.get("title") or p.get("title") or "Product",
                "image": c.get("image") or p.get("image") or "",
                "list_price": price.list_price, "source_cost": source_cost,
                "shipping": 5.0, "breakdown": price.__dict__, "dap": True,
            }})
    # 3) live lookup by product id
    p = _resolve(prov, pid)
    if not p:
        return err("product unavailable from supplier", 404)
    source_cost = float(p.get("price", 0) or 0)
    price = pricing.price_product(source_cost, 5.0)
    return ok({"product": {
        "id": pid, "source_product_id": pid,
        "title": p.get("title") or "Product", "image": p.get("image") or "",
        "list_price": price.list_price, "source_cost": source_cost,
        "shipping": 5.0, "breakdown": price.__dict__, "dap": True,
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


def h_freight(event):
    qs = event.get("queryStringParameters") or {}
    pid = qs.get("product_id") or ""
    qty = int(qs.get("quantity", 1))
    country = qs.get("country", "US")
    if not pid:
        return err("product_id required")
    prov = supplier.get_provider()
    if prov.name == "aliexpress":
        try:
            f = prov.freight(pid, qty, country)
            return ok({"freight": f})
        except Exception as e:
            print(f"[catalog] freight failed: {e}")
    # fallback: flat $5 shipping
    return ok({"freight": {"shipping": 5.0, "currency": "USD", "service": "standard", "time": "10-20 days"}})


def h_categories(event):
    prov = supplier.get_provider()
    if prov.name == "aliexpress":
        try:
            return ok(prov.categories())
        except Exception as e:
            print(f"[catalog] categories failed: {e}")
    # demo fallback
    return ok({"all": [], "top": [
        {"id": "electronics", "name": "Electronics"},
        {"id": "home", "name": "Home & Kitchen"},
        {"id": "sports", "name": "Sports & Outdoors"},
        {"id": "beauty", "name": "Beauty"},
    ]})


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
    ("GET", "/categories", h_categories),
    ("GET", "/freight", h_freight),
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
