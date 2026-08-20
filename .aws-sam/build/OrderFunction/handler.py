"""
Dropship order service — order creation (persisted), fulfillment relay to supplier,
tracking, and buyer↔supplier message relay. Provider-agnostic.
"""
import json
import time
import uuid
from common import ok, err
import supplier
import pricing
import db


def h_create_order(event):
    body = json.loads(event.get("body") or "{}")
    items = body.get("items")
    address = body.get("logistics_address")
    buyer_sub = body.get("buyer_sub", "")
    if not items or not address:
        return err("items and logistics_address required")

    prov = supplier.get_provider()
    # relay directly — the client already validated products + prices.
    product_items = [{"product_id": it.get("product_id"), "quantity": int(it.get("quantity", 1))}
                     for it in items]

    try:
        result = prov.create_order(address, product_items)
    except Exception as e:
        return err(f"supplier order failed: {e}", 502)
    if not result or "error" in result:
        return err("supplier order failed", 502)

    # compute our listing total from the client-supplied prices (fallback 0)
    source_total = float(body.get("source_total", 0) or 0)
    price = pricing.price_product(source_total, 0.0)
    order_id = result.get("order_id") or f"ord-{uuid.uuid4().hex[:12]}"
    db.put_order(
        order_id,
        buyer_sub=buyer_sub,
        items=product_items,
        address=address,
        status=result.get("status", "pending"),
        supplier_order_id=result.get("order_id", ""),
        total=price.list_price,
        currency=price.currency,
    )
    return ok({
        "order": {
            "id": order_id,
            "supplier_order_id": result.get("order_id"),
            "status": result.get("status", "pending"),
            "breakdown": price.__dict__,
        }
    })


def h_list_orders(event):
    qs = event.get("queryStringParameters") or {}
    buyer_sub = qs.get("buyer_sub", "")
    orders = db.list_orders(buyer_sub=buyer_sub)
    orders.sort(key=lambda x: x.get("created", 0), reverse=True)
    return ok({"orders": orders})


def h_get_order(event, oid):
    o = db.get_order(oid)
    if not o:
        return err("not found", 404)
    return ok({"order": o})


def h_track(event, oid):
    o = db.get_order(oid)
    if not o:
        return err("not found", 404)
    prov = supplier.get_provider()
    tracking = o.get("tracking", "")
    status = o.get("status", "pending")
    # try live tracking if supplier order exists
    if o.get("supplier_order_id"):
        try:
            sup = prov.order_details(o["supplier_order_id"])
            if sup and "error" not in sup:
                tracking = sup.get("tracking", tracking)
                status = sup.get("status", status)
                db.update_order(oid, tracking=tracking, status=status)
        except Exception:
            pass
    return ok({"tracking": tracking, "status": status, "order_id": oid})


def h_send_message(event, oid):
    body = json.loads(event.get("body") or "{}")
    text = body.get("text", "")
    sender = body.get("sender", "buyer")
    if not text:
        return err("text required")
    m = db.put_message(oid, sender, text)
    return ok({"message": m})


def h_list_messages(event, oid):
    return ok({"messages": db.list_messages(oid)})


ROUTES = [
    ("POST", "/orders", h_create_order),
    ("GET", "/orders", h_list_orders),
]

PARAM_ROUTES = [
    ("GET", "/orders/", h_get_order),
    ("GET", "/track/", h_track),
    ("POST", "/orders/", h_send_message),
    ("GET", "/messages/", h_list_messages),
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
