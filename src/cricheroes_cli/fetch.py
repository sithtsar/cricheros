"""Incremental, resumable, parallel fetcher.

Every response lands on disk the moment it arrives, so a killed run resumes
where it left off and nothing is ever refetched. Raw files are the source of
truth; the database is derived from them.

Layout under data/:
  raw/{player_id}/profile.json, stats.json, matches_p{n}.json   (per player)
  raw/scorecards/{match_id}.json                                 (per match)
  registry/players.json                                          (the known set)
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import api

log = logging.getLogger("fetch")
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
REGISTRY = ROOT / "data" / "registry" / "players.json"


def load_registry() -> list[dict]:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return []


def save_registry(rows: list[dict]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(rows, indent=2))


def add_players(links: list[str], resolve) -> list[dict]:
    registr = {r["player_id"]: r for r in load_registry()}
    added = []
    for link in links:
        url, pid = resolve(link)
        if pid not in registr:
            registr[pid] = {"source_link": link, "profile_link": url, "player_id": pid}
            added.append(registr[pid])
            log.info("added %s (%s)", pid, url)
    save_registry(list(registr.values()))
    return added


def _save(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))


def fetch_profile_stats(pid: int) -> None:
    for ep, name in (
        ("player/get-player-profile-web/", "profile"),
        ("player/get-player-statistic/", "stats"),
    ):
        out = RAW / str(pid) / f"{name}.json"
        if out.exists():
            continue
        _save(out, api.get(f"{ep}{pid}"))
        log.info("player %s: %s saved", pid, name)


def fetch_matches(pid: int) -> list[int]:
    """Page through a player's matches, resuming after any already-saved page."""
    pdir = RAW / str(pid)
    if (pdir / "matches.fail").exists():
        return []
    pages = sorted(
        pdir.glob("matches_p*.json"), key=lambda p: int(p.stem.split("_p")[1])
    )
    pageno = 1
    if pages:
        last = json.loads(pages[-1].read_text())
        if not (last.get("page") or {}).get("next"):
            return _match_ids(pdir)  # complete already
        pageno = int(pages[-1].stem.split("_p")[1]) + 1
    if pageno == 1:
        log.info("player %s: pages from %s", pid, pageno)
    while True:
        body = api.get(
            f"player/get-player-match/{pid}",
            params={"pageno": pageno, "datetime": int(time.time() * 1000)},
        )
        _save(pdir / f"matches_p{pageno}.json", body)
        if not (body.get("page") or {}).get("next"):
            break
        pageno += 1
    return _match_ids(pdir)


def _match_ids(pdir: Path) -> list[int]:
    ids = []
    for p in pdir.glob("matches_p*.json"):
        ids += [m["match_id"] for m in json.loads(p.read_text()).get("data") or []]
    return sorted(set(ids))


def fetch_scorecard(mid: int) -> None:
    out = RAW / "scorecards" / f"{mid}.json"
    if out.exists() or (RAW / "scorecards" / f"{mid}.fail").exists():
        return
    _save(out, api.get(f"scorecard/v2/get-scorecard/{mid}"))
    log.info("scorecard %s saved", mid)


def fetch_scorecard_fail(mid: int, err: Exception) -> None:
    """Record a consistent failure so future runs skip it until deleted."""
    (RAW / "scorecards" / f"{mid}.fail").write_text(str(err))
    log.warning("scorecard %s marked failed: %s", mid, err)


def all_match_ids() -> list[int]:
    ids = []
    for pdir in RAW.iterdir():
        if pdir.is_dir() and pdir.name.isdigit():
            ids += _match_ids(pdir)
    return sorted(set(ids))


def fetch(players: int = 12, scorecard_workers: int = 12) -> dict:
    reg = load_registry()
    pids = [r["player_id"] for r in reg]
    log.info("registry: %d players", len(pids))

    # profiles + stats + match pages: parallel across players
    with ThreadPoolExecutor(max_workers=players) as ex:
        futs = {ex.submit(fetch_profile_stats, pid): pid for pid in pids}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                log.error("player %s: %s", futs[fut], e)
        futs = {ex.submit(fetch_matches, pid): pid for pid in pids}
        for fut in as_completed(futs):
            try:
                n = fut.result()
                log.info("player %s: %d matches", futs[fut], len(n))
            except Exception as e:  # noqa: BLE001
                (RAW / str(futs[fut]) / "matches.fail").write_text(str(e))
                log.error("player %s matches: %s (marked failed)", futs[fut], e)

    # scorecards: parallel across unique match ids (the big, resumable step)
    mids = all_match_ids()
    done = sum(1 for m in mids if (RAW / "scorecards" / f"{m}.json").exists())
    log.info(
        "scorecards: %d unique matches, %d cached, fetching %d",
        len(mids),
        done,
        len(mids) - done,
    )
    with ThreadPoolExecutor(max_workers=scorecard_workers) as ex:
        futs = {ex.submit(fetch_scorecard, m): m for m in mids}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                fetch_scorecard_fail(futs[fut], e)
            if i % 100 == 0:
                log.info("scorecards %d/%d", i, len(mids))
    return {"players": len(pids), "matches": len(mids)}
