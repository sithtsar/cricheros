"""Resolve cricheroes player links (incl. chshare.link shortlinks) to player ids."""

import re
import requests

ID_RE = re.compile(r"/player-profile/(\d+)/")
SHARE_RE = re.compile(r'"url":"(https://cricheroes\.com/player-profile/\d+/[^"]+)"')


def resolve(raw: str) -> tuple[str, int]:
    """Return (canonical_url, player_id) for a url or bare id."""
    raw = raw.strip()
    if raw.isdigit():
        return f"https://cricheroes.com/player-profile/{raw}/", int(raw)
    m = ID_RE.search(raw)
    if m:
        return raw, int(m[1])
    r = requests.get(raw, timeout=30)  # chshare.link embeds the target in __NEXT_DATA__
    m = SHARE_RE.search(r.text)
    if not m:
        raise ValueError(f"cannot resolve {raw}")
    url = m[1].replace("\\u002F", "/")
    m = ID_RE.search(url)
    if not m:
        raise ValueError(f"no player id in {url}")
    return url, int(m[1])
