"""
Dropship curated catalog — DynamoDB persistence for the products we choose to
list. We do NOT auto-import all of AliExpress; an admin/owner curates products by
source product ID, and the catalog service resolves live price via AliExpress on
demand (or uses a cached price).

Table: single-table pattern like Passage.
  - PRODUCT#<id> : the curated listing (source_product_id, title, image, category)
"""
import os
import time
import json
import decimal
import boto3

TABLE = os.environ.get("TABLE_NAME", "dropship-catalog")


def _ts():
    return int(time.time())


def _client():
    return boto3.client("dynamodb")


def _table():
    return boto3.resource("dynamodb").Table(TABLE)


def _to_ddb(obj):
    """Recursively convert float -> Decimal so DynamoDB accepts nested structures."""
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    return obj


def _from_ddb(obj):
    """Recursively convert Decimal -> float for clean JSON output."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_ddb(v) for v in obj]
    return obj


def _put(item):
    _table().put_item(Item=_to_ddb(item))
    return item


def _get(pk, sk):
    r = _table().get_item(Key={"PK": pk, "SK": sk})
    return _from_ddb(r.get("Item"))


def _scan(filter_expr="", names=None, values=None):
    t = _table()
    kwargs = {}
    if filter_expr:
        kwargs["FilterExpression"] = filter_expr
    if names:
        kwargs["ExpressionAttributeNames"] = names
    if values:
        kwargs["ExpressionAttributeValues"] = values
    items = []
    while True:
        r = t.scan(**kwargs) if kwargs else t.scan()
        items.extend(r.get("Items", []))
        if "LastEvaluatedKey" in r:
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        else:
            break
    return _from_ddb(items)


# ---------- Products ----------
def put_product(source_product_id, title="", image="", category="", active=True):
    pid = f"p-{source_product_id}"
    pk = f"PRODUCT#{pid}"
    item = {"PK": pk, "SK": pk, "id": pid, "source_product_id": str(source_product_id),
            "title": title, "image": image, "category": category, "active": active,
            "created": _ts()}
    return _put(item)


def get_product(pid):
    return _get(f"PRODUCT#{pid}", f"PRODUCT#{pid}")


def list_products(active_only=True):
    items = _scan("begins_with(PK, :p)", {}, {":p": "PRODUCT#"})
    if active_only:
        items = [i for i in items if i.get("active", True)]
    return items


def delete_product(pid):
    _table().delete_item(Key={"PK": f"PRODUCT#{pid}", "SK": f"PRODUCT#{pid}"})


# ---------- Feed cache (avoid slow live AliExpress calls on every list) ----------
def get_feed_cache(key="default"):
    return _get(f"CACHE#feed", f"CACHE#feed#{key}")


def put_feed_cache(key, products, ttl_seconds=300):
    item = {"PK": f"CACHE#feed", "SK": f"CACHE#feed#{key}",
            "products": products, "cached_at": _ts(), "ttl": _ts() + ttl_seconds}
    return _put(item)


def feed_cache_fresh(key="default", max_age=300):
    c = get_feed_cache(key)
    if not c:
        return None
    if _ts() - c.get("cached_at", 0) > max_age:
        return None
    return c.get("products", [])


# ---------- Orders ----------
def put_order(order_id, buyer_sub="", items=None, address=None, status="pending",
              supplier_order_id="", tracking="", total=0.0, currency="CAD"):
    pk = f"ORDER#{order_id}"
    item = {"PK": pk, "SK": pk, "id": order_id, "buyer_sub": buyer_sub,
            "items": items or [], "address": address or {}, "status": status,
            "supplier_order_id": supplier_order_id, "tracking": tracking,
            "total": total, "currency": currency, "created": _ts()}
    return _put(item)


def get_order(order_id):
    return _get(f"ORDER#{order_id}", f"ORDER#{order_id}")


def list_orders(buyer_sub=""):
    items = _scan("begins_with(PK, :p)", {}, {":p": "ORDER#"})
    if buyer_sub:
        items = [i for i in items if i.get("buyer_sub") == buyer_sub]
    return items


def update_order(order_id, **fields):
    item = get_order(order_id)
    if not item:
        return None
    for k, v in fields.items():
        item[k] = v
    _put(item)
    return item


# ---------- Messages (buyer↔supplier relay) ----------
def put_message(order_id, sender, text):
    mid = f"m-{_ts()}-{abs(hash(text)) % 10000}"
    pk = f"MSG#{order_id}"
    item = {"PK": pk, "SK": mid, "id": mid, "order_id": order_id,
            "sender": sender, "text": text, "created": _ts()}
    return _put(item)


def list_messages(order_id):
    items = _scan("begins_with(PK, :p)", {}, {":p": f"MSG#{order_id}"})
    items.sort(key=lambda x: x.get("created", 0))
    return items
