"""Resolve cricheroes player links (incl. chshare.link shortlinks) to player ids."""

import re
import sys
from pathlib import Path

import requests

ID_RE = re.compile(r"/player-profile/(\d+)/")
SHARE_RE = re.compile(r'"url":"(https://cricheroes\.com/player-profile/\d+/[^"]+)"')


def read_links(src: str) -> list[str]:
    """Read links from a .txt/.tsv/.csv file, or stdin when src == '-'".

    One link per line; files with a header or extra columns are handled by
    grabbing the first http(s) URL found on each line.
    """
    lines = sys.stdin if src == "-" else Path(src).open()
    links: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"https?://\S+", line)
        links.append(m.group(0).rstrip(",;") if m else line.split("\t")[0].split(",")[0].strip())
    if src == "-":
        return [x for x in links if x]
    # drop a header row if the file has one (first line that is not a url)
    if links and "cricheroes" not in links[0] and "chshare" not in links[0] and "http" not in links[0]:
        links = links[1:]
    return [x for x in links if x]


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
