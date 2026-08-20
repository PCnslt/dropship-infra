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
