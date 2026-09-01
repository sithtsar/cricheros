"""Rebuild analysis.db + CSVs from the raw cache. Idempotent; safe to rerun.
Slim schema: only Name + 5 stats (batting_matches, batting_runs, batting_sr,
bowling_wickets, bowling_economy). Any new player added via manifest will be
normalized to this same projection (NULL where no bowling/batting data).
"""

import csv
import datetime
import json
import sqlite3
from pathlib import Path

from .fetch import load_registry

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()

# Slim DDL: players holds identity, player_career holds the 5 requested stats
# (plus name denormalized for direct CSV match to user table).
# matches / match_performances are retained as derived detail but are NOT part
# of the "player schema" - they can be dropped if strict single-table needed.
DDL = {
    "players": """CREATE TABLE players(
        player_id INTEGER PRIMARY KEY, name TEXT, fetched_at TEXT)""",
    "player_career": """CREATE TABLE player_career(
        player_id INTEGER PRIMARY KEY, name TEXT,
        batting_matches INTEGER, batting_runs INTEGER, batting_sr REAL,
        bowling_wickets INTEGER, bowling_economy REAL,
        fetched_at TEXT)""",
    "matches": """CREATE TABLE matches(
        match_id INTEGER PRIMARY KEY, match_type TEXT, ball_type TEXT, status TEXT,
        start_datetime TEXT, city_id INTEGER, city_name TEXT, ground_id INTEGER, ground_name TEXT,
        tournament_id INTEGER, tournament_name TEXT, tournament_category TEXT,
        tournament_round_id INTEGER, tournament_round_name TEXT,
        team_a_id INTEGER, team_a_name TEXT, team_a_summary TEXT,
        team_b_id INTEGER, team_b_name TEXT, team_b_summary TEXT,
        toss_details TEXT, win_by TEXT, winning_team TEXT, match_result TEXT, is_dl INTEGER,
        is_super_over INTEGER, fetched_at TEXT)""",
    "match_performances": """CREATE TABLE match_performances(
        player_id INTEGER, match_id INTEGER, team_id INTEGER, team_name TEXT,
        batting_position INTEGER, runs INTEGER, balls INTEGER, fours INTEGER, sixes INTEGER,
        sr REAL, how_out TEXT, is_out INTEGER, minutes INTEGER,
        overs REAL, maidens INTEGER, wickets INTEGER, economy REAL, wides INTEGER,
        noballs INTEGER, dots INTEGER,
        PRIMARY KEY(player_id, match_id))""",
}


def _extract_slim(stats: dict) -> dict:
    """Extract only the 5 requested columns from cricheroes statistics dict."""
    out = {
        "batting_matches": None,
        "batting_runs": None,
        "batting_sr": None,
        "bowling_wickets": None,
        "bowling_economy": None,
    }
    if not stats:
        return out
    for grp in ("batting", "bowling"):
        for r in stats.get(grp) or []:
            title = str(r["title"]).strip().lower()
            val = r["value"]
            if grp == "batting":
                if title == "matches":
                    try: out["batting_matches"] = int(val) if val not in (None, "") else None
                    except: out["batting_matches"] = None
                elif title == "runs":
                    try: out["batting_runs"] = int(val) if val not in (None, "") else None
                    except: out["batting_runs"] = None
                elif title == "sr":
                    try: out["batting_sr"] = float(str(val)) if val not in (None, "") else None
                    except: out["batting_sr"] = None
            elif grp == "bowling":
                if title == "wickets":
                    try: out["bowling_wickets"] = int(val) if val not in (None, "") else None
                    except: out["bowling_wickets"] = None
                elif title == "economy":
                    try: out["bowling_economy"] = float(str(val)) if val not in (None, "") else None
                    except: out["bowling_economy"] = None
    return out


def _perf(pid: int, sc: dict) -> dict | None:
    perf = {
        "team_id": None,
        "team_name": None,
        "bat_order": None,
        "runs": None,
        "balls": None,
        "fours": None,
        "sixes": None,
        "sr": None,
        "how_out": None,
        "is_out": None,
        "minutes": None,
        "overs": None,
        "maidens": None,
        "wickets": None,
        "economy": None,
        "wides": None,
        "noballs": None,
        "dots": None,
    }
    teams = {}
    for side in ("team_a", "team_b"):
        t = sc.get(side) or {}
        if t.get("id") is not None:
            teams[t["id"]] = t.get("name")
        for scd in t.get("scorecard") or []:
            if scd is None or scd.get("team_id") is None:
                continue
            perf["team_id"] = scd["team_id"]
            perf["team_name"] = teams.get(scd["team_id"])
            for i, x in enumerate(scd.get("batting") or []):
                if x.get("player_id") == pid:
                    perf.update(
                        runs=x.get("runs"),
                        balls=x.get("balls"),
                        fours=x.get("4s"),
                        sixes=x.get("6s"),
                        sr=x.get("SR"),
                        how_out=x.get("how_to_out_short_name"),
                        is_out=x.get("is_out"),
                        minutes=x.get("minutes"),
                        bat_order=i + 1,
                    )
            for x in scd.get("bowling") or []:
                if x.get("player_id") == pid:
                    balls = x.get("balls")
                    perf.update(
                        overs=round(balls / 6, 1) if isinstance(balls, int) else None,
                        maidens=x.get("maidens"),
                        wickets=x.get("wickets"),
                        economy=x.get("economy_rate"),
                        wides=x.get("extra_type_run_wide"),
                        noballs=x.get("extra_type_run_noball"),
                        dots=x.get("0s"),
                    )
    return perf if perf["team_id"] is not None else None


def export_flat(players: list[dict]) -> Path:
    """Regenerate the combined per-player dump (data/players.json) from raw."""
    out = []
    for r in players:
        pdir = RAW / str(r["player_id"])
        item = dict(r)
        for fname, key in (("profile.json", "get-player-profile-web"),
                           ("stats.json", "get-player-statistic")):
            f = pdir / fname
            if f.exists():
                item[key] = json.loads(f.read_text()).get("data") or {}
        matches = []
        for p in sorted(pdir.glob("matches_p*.json"),
                        key=lambda p: int(p.stem.split("_p")[1])):
            matches += json.loads(p.read_text()).get("data") or []
        if matches:
            item["get-player-match"] = matches
        out.append(item)
    dest = ROOT / "data" / "players.json"
    dest.write_text(json.dumps(out, indent=2))
    return dest


def normalize() -> dict:
    con = sqlite3.connect(ROOT / "data" / "analysis.db")
    cur = con.cursor()
    for tbl, ddl in DDL.items():
        cur.execute(f"DROP TABLE IF EXISTS {tbl}")
        cur.execute(ddl)

    n = {"players": 0, "career": 0, "matches": 0, "performances": 0}
    for pdir in sorted(
        (p for p in RAW.iterdir() if p.is_dir() and p.name.isdigit()),
        key=lambda p: int(p.name),
    ):
        if not pdir.is_dir() or not pdir.name.isdigit():
            continue
        pid = int(pdir.name)
        prof = json.loads((pdir / "profile.json").read_text()).get("data") or {}
        name = prof.get("name")
        cur.execute(
            "INSERT OR REPLACE INTO players VALUES (?,?,?)",
            (pid, name, NOW),
        )
        n["players"] += 1
        # stats -> slim 5 cols
        slim = {"batting_matches": None, "batting_runs": None, "batting_sr": None,
                "bowling_wickets": None, "bowling_economy": None}
        if (pdir / "stats.json").exists():
            stats = json.loads((pdir / "stats.json").read_text()).get("data") or {}
            slim = _extract_slim(stats.get("statistics") or {})
        # Kedar Pegdal etc may have no stats -> stays NULL
        cur.execute(
            "INSERT OR REPLACE INTO player_career VALUES (?,?,?,?,?,?,?,?)",
            (pid, name,
             slim["batting_matches"], slim["batting_runs"], slim["batting_sr"],
             slim["bowling_wickets"], slim["bowling_economy"],
             NOW),
        )
        n["career"] += 1

    scdir = RAW / "scorecards"
    for scf in sorted(scdir.glob("*.json")):
        sc = json.loads(scf.read_text()).get("data") or {}
        ta, tb = sc.get("team_a") or {}, sc.get("team_b") or {}
        cur.execute(
            """INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sc.get("match_id"),
                sc.get("match_type"),
                sc.get("ball_type"),
                sc.get("status"),
                sc.get("start_datetime"),
                sc.get("city_id"),
                sc.get("city_name"),
                sc.get("ground_id"),
                sc.get("ground_name"),
                sc.get("tournament_id") or None,
                sc.get("tournament_name"),
                sc.get("tournament_category"),
                sc.get("tournament_round_id") or None,
                sc.get("tournament_round_name"),
                ta.get("id") or None,
                ta.get("name"),
                ta.get("summary"),
                tb.get("id") or None,
                tb.get("name"),
                tb.get("summary"),
                sc.get("toss_details"),
                sc.get("win_by"),
                sc.get("winning_team"),
                sc.get("match_result"),
                1 if sc.get("is_dl") else 0,
                1 if sc.get("is_super_over") else 0,
                NOW,
            ),
        )
        n["matches"] += 1
        for pdir in RAW.iterdir():
            if not pdir.is_dir() or not pdir.name.isdigit():
                continue
            pid = int(pdir.name)
            perf = _perf(pid, sc)
            if perf is None:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO match_performances VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    sc.get("match_id"),
                    perf["team_id"],
                    perf["team_name"],
                    perf["bat_order"],
                    perf["runs"],
                    perf["balls"],
                    perf["fours"],
                    perf["sixes"],
                    perf["sr"],
                    perf["how_out"],
                    perf["is_out"],
                    perf["minutes"],
                    perf["overs"],
                    perf["maidens"],
                    perf["wickets"],
                    perf["economy"],
                    perf["wides"],
                    perf["noballs"],
                    perf["dots"],
                ),
            )
            n["performances"] += 1

    con.commit()
    for tbl in ("players", "player_career", "matches", "match_performances"):
        with open(ROOT / "data" / f"{tbl}.csv", "w", newline="") as f:
            cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tbl})")]
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(cur.execute(f"SELECT * FROM {tbl}"))
    con.close()
    n["flat"] = str(export_flat(load_registry()))
    return n
