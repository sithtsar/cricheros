"""Rebuild analysis.db + CSVs from the raw cache. Idempotent; safe to rerun."""

import csv
import datetime
import json
import sqlite3
from pathlib import Path

from .fetch import load_registry

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()

DDL = {
    "players": """CREATE TABLE players(
        player_id INTEGER PRIMARY KEY, name TEXT, profile_photo TEXT, dob TEXT, city_id INTEGER,
        city_name TEXT, batting_hand TEXT, bowling_style TEXT, playing_role TEXT,
        batter_category TEXT, bowler_category TEXT, player_skill TEXT, player_statement TEXT,
        is_pro INTEGER, total_matches INTEGER, total_runs INTEGER, total_wickets INTEGER,
        total_views INTEGER, fetched_at TEXT)""",
    "player_career": """CREATE TABLE player_career(
        player_id INTEGER PRIMARY KEY, fetched_at TEXT,
        batting_matches INTEGER, batting_innings INTEGER, batting_not_out INTEGER,
        batting_runs INTEGER, batting_highest TEXT, batting_avg REAL, batting_sr REAL,
        batting_thirties INTEGER, batting_fifties INTEGER, batting_hundreds INTEGER,
        batting_fours INTEGER, batting_sixes INTEGER, batting_ducks INTEGER,
        matches_won INTEGER, matches_lost INTEGER,
        bowling_matches INTEGER, bowling_innings INTEGER, bowling_overs TEXT, bowling_maidens INTEGER,
        bowling_wickets INTEGER, bowling_runs INTEGER, bowling_best TEXT, bowling_3w INTEGER,
        bowling_5w INTEGER, bowling_economy REAL, bowling_sr REAL, bowling_avg REAL,
        bowling_wides INTEGER, bowling_noballs INTEGER, bowling_dots INTEGER,
        bowling_fours INTEGER, bowling_sixes INTEGER,
        fielding_matches INTEGER, fielding_catches INTEGER, fielding_caught_behind INTEGER,
        fielding_run_outs INTEGER, fielding_stumpings INTEGER, fielding_assisted_run_outs INTEGER,
        fielding_bye_runs INTEGER,
        captain_matches INTEGER, captain_toss_won INTEGER, captain_win_pct REAL, captain_loss_pct REAL)""",
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

# map cricheroes stat row titles -> column suffix per group
COLS = {
    "matches": "matches",
    "innings": "innings",
    "not_out": "not_out",
    "runs": "runs",
    "highest_runs": "highest",
    "avg": "avg",
    "sr": "sr",
    "30s": "thirties",
    "50s": "fifties",
    "100s": "hundreds",
    "4s": "fours",
    "6s": "sixes",
    "ducks": "ducks",
    "overs": "overs",
    "maidens": "maidens",
    "wickets": "wickets",
    "best_bowling": "best",
    "3_wickets": "3w",
    "5_wickets": "5w",
    "economy": "economy",
    "wides": "wides",
    "noballs": "noballs",
    "dot_balls": "dots",
    "catches": "catches",
    "caught_behind": "caught_behind",
    "run_outs": "run_outs",
    "stumpings": "stumpings",
    "assisted_run_outs": "assisted_run_outs",
    "bye_runs_(wk)": "bye_runs",
    "toss_won": "toss_won",
    "win_per": "win_pct",
    "loss_per": "loss_pct",
}
PREFIX = {
    "batting": "batting_",
    "bowling": "bowling_",
    "fielding": "fielding_",
    "captain": "captain_",
}


def _stat_rows(stats: dict, grp: str) -> dict:
    out = {"matches_won": None, "matches_lost": None}
    for r in stats.get(grp) or []:
        t = str(r["title"]).lower().replace(" ", "_").replace("_runs_(wk)", "_bye_runs")
        if t == "won":
            out["matches_won"] = r["value"]
            continue
        if t == "loss":
            out["matches_lost"] = r["value"]
            continue
        col = COLS.get(t)
        if not col:
            continue
        v = r["value"]
        if col in ("avg", "sr", "economy", "win_pct", "loss_pct"):
            try:
                v = float(str(v).rstrip("%"))
            except ValueError:
                v = None
        out[PREFIX[grp] + col] = v
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
    con = sqlite3.connect(ROOT / "analysis.db")
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
        cur.execute(
            "INSERT OR REPLACE INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                prof.get("name"),
                prof.get("profile_photo"),
                prof.get("dob"),
                prof.get("city_id"),
                prof.get("city_name"),
                prof.get("batting_hand"),
                prof.get("bowling_style"),
                prof.get("playing_role"),
                prof.get("batter_category"),
                prof.get("bowler_category"),
                prof.get("player_skill"),
                prof.get("player_statement"),
                1 if prof.get("is_pro") else 0,
                prof.get("total_matches"),
                prof.get("total_runs"),
                prof.get("total_wickets"),
                prof.get("total_views"),
                NOW,
            ),
        )
        n["players"] += 1
        if (pdir / "stats.json").exists():
            stats = json.loads((pdir / "stats.json").read_text()).get("data") or {}
            row = {"player_id": pid, "fetched_at": NOW}
            for grp, _pfx in PREFIX.items():
                row.update(_stat_rows(stats.get("statistics") or {}, grp))
            cur.execute(
                f"INSERT OR REPLACE INTO player_career ({', '.join(row)}) VALUES "
                f"({', '.join('?' * len(row))})",
                list(row.values()),
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
        with open(ROOT / f"{tbl}.csv", "w", newline="") as f:
            cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tbl})")]
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(cur.execute(f"SELECT * FROM {tbl}"))
    con.close()
    n["flat"] = str(export_flat(load_registry()))
    return n
