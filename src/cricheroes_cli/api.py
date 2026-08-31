"""Thin client for the cricheroes private API."""

import secrets
import time
import requests
import threading

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
)
API = "https://api.cricheroes.in/api/v1"


class APIError(RuntimeError):
    pass


_local = threading.local()


def session() -> requests.Session:
    """One session per thread (the API keys udid per session)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(
            {
                "api-key": "cr!CkH3r0s",
                "device-type": UA,
                "udid": secrets.token_hex(16),
                "User-Agent": UA,
                "Content-Type": "application/json",
            }
        )
        _local.session = s
    return s


def get(
    ep: str, params: dict | None = None, retries: int = 2, backoff: float = 2.0
) -> dict:
    last: APIError = APIError("unknown failure")
    for i in range(retries + 1):
        try:
            r = session().get(f"{API}/{ep}", params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            if body.get("status"):
                return body
            last = APIError(f"{ep}: {body.get('error')}")
        except (requests.RequestException, ValueError) as e:
            last = APIError(f"{ep}: {e}")
        time.sleep(backoff * (i + 1))
    raise last
