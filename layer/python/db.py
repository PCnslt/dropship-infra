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
import boto3

TABLE = os.environ.get("TABLE_NAME", "dropship-catalog")


def _ts():
    return int(time.time())


def _client():
    return boto3.client("dynamodb")


def _table():
    return boto3.resource("dynamodb").Table(TABLE)


def _put(item):
    _table().put_item(Item=item)
    return item


def _get(pk, sk):
    r = _table().get_item(Key={"PK": pk, "SK": sk})
    return r.get("Item")


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
    return items


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
