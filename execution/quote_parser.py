from __future__ import annotations

from typing import Any, Dict, Iterable, Optional
import math

from execution.models import QuoteSnapshot


LTP_KEYS = ("ltp", "last_price", "lastprice", "last_traded_price", "lasttradedprice")
BID_KEYS = ("bid", "bid_price", "best_bid", "bestbidprice")
ASK_KEYS = ("ask", "ask_price", "best_ask", "bestaskprice")
BID_QTY_KEYS = ("bid_qty", "bid_quantity", "best_bid_qty", "bestbidquantity", "quantity")
ASK_QTY_KEYS = ("ask_qty", "ask_quantity", "best_ask_qty", "bestaskquantity", "quantity")
VOLUME_KEYS = ("volume", "volume_traded_today", "total_traded_volume", "totaltradedvolume", "vol")
OI_KEYS = ("oi", "open_interest", "openinterest")


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _lower_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in d.items()}


def _first_numeric(mapping: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    low = _lower_dict(mapping)
    for key in keys:
        if key in low:
            v = _to_float(low[key])
            if v is not None:
                return v
    return None


def _key_match(mapping: Dict[Any, Any], wanted: str):
    """Return the value whose key stringifies to *wanted*.

    Dhan SDK versions have returned security-id keys as both strings and ints.
    Treat those forms as equivalent rather than falling back to symbol lookup.
    """
    wanted = str(wanted).strip()
    for key, value in mapping.items():
        if str(key).strip() == wanted:
            return value
    return None


def _find_symbol_node(payload: Any, symbol: str) -> Any:
    """Locate a quote node in heterogeneous broker response shapes.

    Supports:
      {security_id: {...}}
      {"data": {"NSE_FNO": {security_id: {...}}}}
      wrappers such as quotes/quote/response, and integer security-id keys.
    """
    if not isinstance(payload, dict):
        return payload

    direct = _key_match(payload, symbol)
    if direct is not None:
        return direct

    for wrapper in ("data", "quotes", "quote", "response"):
        node = payload.get(wrapper)
        if not isinstance(node, dict):
            continue

        direct = _key_match(node, symbol)
        if direct is not None:
            return direct

        # Common Dhan shape: data -> NSE_FNO -> security_id -> quote
        for child in node.values():
            if not isinstance(child, dict):
                continue
            direct = _key_match(child, symbol)
            if direct is not None:
                return direct
            # One additional defensive level for SDK wrappers.
            for grandchild in child.values():
                if isinstance(grandchild, dict):
                    direct = _key_match(grandchild, symbol)
                    if direct is not None:
                        return direct

    if len(payload) == 1:
        only = next(iter(payload.values()))
        if isinstance(only, dict):
            found = _find_symbol_node(only, symbol)
            if found is not only:
                return found
        return only
    return payload


def _extract_depth(node: Dict[str, Any]):
    low = _lower_dict(node)
    depth = low.get("depth") or low.get("market_depth") or low.get("marketdepth")
    if not isinstance(depth, dict):
        return None, None, None, None
    depth_low = _lower_dict(depth)
    buys = depth_low.get("buy") or depth_low.get("bids") or []
    sells = depth_low.get("sell") or depth_low.get("asks") or []
    bid = bid_qty = ask = ask_qty = None
    if isinstance(buys, list) and buys:
        top = buys[0]
        if isinstance(top, dict):
            bid = _first_numeric(top, ("price", "bid_price", "bid"))
            bid_qty = _first_numeric(top, ("quantity", "qty", "bid_qty", "bid_quantity"))
    if isinstance(sells, list) and sells:
        top = sells[0]
        if isinstance(top, dict):
            ask = _first_numeric(top, ("price", "ask_price", "ask"))
            ask_qty = _first_numeric(top, ("quantity", "qty", "ask_qty", "ask_quantity"))
    return bid, ask, bid_qty, ask_qty


def parse_quote_response(payload: Any, symbol: str) -> QuoteSnapshot:
    node = _find_symbol_node(payload, symbol)
    shape = type(node).__name__

    if isinstance(node, (int, float, str)):
        return QuoteSnapshot(symbol=symbol, ltp=_to_float(node), raw_shape=shape)
    if not isinstance(node, dict):
        return QuoteSnapshot(symbol=symbol, raw_shape=shape)

    ltp = _first_numeric(node, LTP_KEYS)
    bid = _first_numeric(node, BID_KEYS)
    ask = _first_numeric(node, ASK_KEYS)
    bid_qty = _first_numeric(node, BID_QTY_KEYS)
    ask_qty = _first_numeric(node, ASK_QTY_KEYS)
    volume = _first_numeric(node, VOLUME_KEYS)
    oi = _first_numeric(node, OI_KEYS)

    d_bid, d_ask, d_bid_qty, d_ask_qty = _extract_depth(node)
    bid = d_bid if d_bid is not None else bid
    ask = d_ask if d_ask is not None else ask
    bid_qty = d_bid_qty if d_bid_qty is not None else bid_qty
    ask_qty = d_ask_qty if d_ask_qty is not None else ask_qty

    return QuoteSnapshot(
        symbol=symbol,
        ltp=ltp,
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        volume=volume,
        open_interest=oi,
        raw_shape=shape,
    )
