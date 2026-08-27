from __future__ import annotations

"""Native Dhan execution/market-data adapter.

Sprint 3.3 deliberately separates TradeHull's useful historical-data/authentication
layer from execution-market-data calls.  The strategy/execution path talks to this
adapter using exchange security IDs and never asks TradeHull to resolve option names.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set, Tuple
import os


@dataclass(frozen=True)
class NativeDhanStatus:
    available: bool
    source: str
    detail: str = ""


class NativeDhanClient:
    """Best-effort official/native Dhan client wrapper.

    Bootstrap order:
      1. Explicit access token (config/env) -> instantiate official ``dhanhq`` SDK.
      2. Reuse an already-authenticated native Dhan SDK object embedded anywhere
         inside the TradeHull object graph.

    No trading symbol resolution occurs in this class; quote requests are always
    ``exchange_segment -> security_id``.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str = "",
        tradehull_root: Any = None,
    ):
        self.client_id = str(client_id or "").strip()
        self._client = None
        self._source = "UNAVAILABLE"
        self._detail = ""

        token = str(access_token or os.getenv("DHAN_ACCESS_TOKEN", "")).strip()
        if token:
            try:
                self._client = self._build_official_client(self.client_id, token)
                self._source = "OFFICIAL_SDK_ACCESS_TOKEN"
                return
            except Exception as exc:
                self._detail = f"official SDK init failed: {exc}"

        if tradehull_root is not None:
            found, path = self._discover_native_client(tradehull_root)
            if found is not None:
                self._client = found
                self._source = "TRADEHULL_AUTHENTICATED_NATIVE_DHAN"
                self._detail = path
                return

        if not self._detail:
            self._detail = "No DHAN_ACCESS_TOKEN and no native quote_data client discovered in TradeHull session"

    @staticmethod
    def _build_official_client(client_id: str, access_token: str):
        # DhanHQ-py v2.1+ uses DhanContext; older v2 builds accepted two strings.
        try:
            from dhanhq import DhanContext, dhanhq
            return dhanhq(DhanContext(client_id, access_token))
        except (ImportError, TypeError):
            from dhanhq import dhanhq
            return dhanhq(client_id, access_token)

    @staticmethod
    def _is_native_quote_client(obj: Any) -> bool:
        if obj is None:
            return False
        # Official dhanhq client exposes quote_data directly in current SDK.
        if callable(getattr(obj, "quote_data", None)):
            return True
        market_feed = getattr(obj, "market_feed", None)
        return market_feed is not None and callable(getattr(market_feed, "quote_data", None))

    @classmethod
    def _discover_native_client(cls, root: Any, max_depth: int = 3) -> Tuple[Optional[Any], str]:
        """Search a small object graph without exposing secrets or traversing huge frames.

        TradeHull versions have changed the private attribute holding the Dhan client.
        Rather than hard-code ``.Dhan``/``.dhan``, locate an object by capability.
        """
        seen: Set[int] = set()
        queue = [(root, "tradehull", 0)]
        blocked_types = (str, bytes, int, float, bool, list, tuple, set, dict)

        while queue:
            obj, path, depth = queue.pop(0)
            oid = id(obj)
            if oid in seen:
                continue
            seen.add(oid)

            if obj is not root and cls._is_native_quote_client(obj):
                return obj, path
            if depth >= max_depth or isinstance(obj, blocked_types):
                continue

            try:
                attrs = vars(obj)
            except Exception:
                continue
            for name, value in attrs.items():
                # Never inspect raw credential/token values.
                low = str(name).lower()
                if any(secret in low for secret in ("token", "secret", "pin", "password", "totp")):
                    continue
                if value is None:
                    continue
                # Avoid pandas/numpy/request sessions and other broad object graphs.
                module = getattr(type(value), "__module__", "")
                if module.startswith(("pandas", "numpy", "requests", "urllib3")):
                    continue
                queue.append((value, f"{path}.{name}", depth + 1))
        return None, ""


    def diagnostic_info(self) -> Dict[str, Any]:
        """Return a secret-safe capability summary for the discovered native object."""
        if self._client is None:
            return {
                "available": False,
                "source": self._source,
                "detail": self._detail,
                "client_type": None,
                "candidate_methods": [],
            }
        cls = type(self._client)
        methods = []
        try:
            for name in dir(self._client):
                low = str(name).lower()
                if any(key in low for key in ("quote", "market", "depth", "feed")):
                    try:
                        if callable(getattr(self._client, name, None)):
                            methods.append(str(name))
                    except Exception:
                        continue
        except Exception:
            pass
        market_feed = getattr(self._client, "market_feed", None)
        feed_methods = []
        if market_feed is not None:
            try:
                for name in dir(market_feed):
                    low = str(name).lower()
                    if any(key in low for key in ("quote", "market", "depth", "feed")):
                        try:
                            if callable(getattr(market_feed, name, None)):
                                feed_methods.append(f"market_feed.{name}")
                        except Exception:
                            continue
            except Exception:
                pass
        return {
            "available": True,
            "source": self._source,
            "detail": self._detail,
            "client_type": f"{cls.__module__}.{cls.__qualname__}",
            "candidate_methods": sorted(set(methods + feed_methods)),
        }

    @property
    def status(self) -> NativeDhanStatus:
        return NativeDhanStatus(self._client is not None, self._source, self._detail)

    def quote_data(self, securities: Dict[str, Iterable[int]]):
        if self._client is None:
            raise RuntimeError(self._detail or "Native Dhan client unavailable")
        normalized = {
            str(segment): [int(float(sec)) for sec in ids]
            for segment, ids in securities.items()
            if ids
        }
        if not normalized:
            return {}

        fn = getattr(self._client, "quote_data", None)
        if callable(fn):
            return fn(normalized)

        market_feed = getattr(self._client, "market_feed", None)
        fn = getattr(market_feed, "quote_data", None) if market_feed is not None else None
        if callable(fn):
            return fn(normalized)

        raise RuntimeError("Discovered native Dhan object does not expose quote_data")
