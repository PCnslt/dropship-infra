"""Shared HTTP helpers for all Passage Lambda services."""
import json
from decimal import Decimal


def _jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    return o


CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,OPTIONS",
}


def resp(status, body):
    return {"statusCode": status, "headers": {**CORS, "Content-Type": "application/json"},
            "body": json.dumps(_jsonable(body))}


def ok(body): return resp(200, body)
def err(msg, status=400): return resp(status, {"error": msg})
