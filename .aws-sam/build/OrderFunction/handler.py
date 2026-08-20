"""
Dropship order service — order creation, fulfillment relay to supplier, tracking.
Uses the shared layer (supplier, pricing) — provider-agnostic.
"""
import json
from common import ok, err
import supplier
import pricing


def h_create_order(event):
    body = json.loads(event.get("body") or "{}")
    items = body.get("items")
    address = body.get("logistics_address")
    if not items or not address:
        return err("items and logistics_address required")

    prov = supplier.get_provider()
    # build product_items for the supplier call
    product_items = []
    source_total = 0.0
    for it in items:
        pid = it.get("product_id")
        qty = int(it.get("quantity", 1))
        p = prov.product_details(pid)
        if not p or "error" in p:
            return err(f"product {pid} unavailable")
        product_items.append({"product_id": pid, "quantity": qty})
        source_total += float(p.get("price", 0)) * qty

    result = prov.create_order(address, product_items)
    if not result or "error" in result:
        return err("supplier order failed", 502)

    # our order record (source hidden from buyer)
    price = pricing.price_product(source_total, 0.0)
    return ok({
        "order": {
            "id": result.get("order_id"),
            "supplier_order_id": result.get("order_id"),
            "status": result.get("status", "pending"),
            "breakdown": price.__dict__,
        }
    })


def h_get_order(event, oid):
    prov = supplier.get_provider()
    o = prov.order_details(oid)
    if not o or "error" in o:
        return err("not found", 404)
    return ok({"order": o})


def h_track(event, oid):
    prov = supplier.get_provider()
    o = prov.order_details(oid)
    if not o or "error" in o:
        return err("not found", 404)
    return ok({"tracking": o.get("tracking", ""), "status": o.get("status", "")})


ROUTES = [
    ("POST", "/orders", h_create_order),
]

PARAM_ROUTES = [
    ("GET", "/orders/", h_get_order),
    ("GET", "/track/", h_track),
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
