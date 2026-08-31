"""CLI: ch add <url...> | ch fetch | ch normalize | ch list"""

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _log(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(ROOT / "logs" / "fetch.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ch", description="incremental cricheroes scraper")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser(
        "add", help="add player URLs (or ids) to the registry and fetch them"
    )
    a.add_argument("links", nargs="*", help="urls, or - to read urls from stdin")
    a.add_argument(
        "--file", action="append", default=[], metavar="PATH",
        help=".txt/.tsv/.csv file with urls (repeatable); '-' = stdin",
    )
    a.add_argument("--workers", type=int, default=12)

    f = sub.add_parser("fetch", help="incrementally fetch everything in the registry")
    f.add_argument("--players", type=int, default=12)
    f.add_argument("--scorecard-workers", type=int, default=12)
    f.add_argument(
        "--seed", help="seed raw cache from an old flat players.json (one-off)"
    )

    sub.add_parser("normalize", help="rebuild analysis.db + CSVs from the raw cache")
    sub.add_parser("list", help="show registry")

    args = p.parse_args(argv)
    _log(logging.DEBUG if args.verbose else logging.INFO)

    from . import fetch, normalize, urls

    if args.cmd == "add":
        links = list(args.links)
        if "-" in links or (not links and not sys.stdin.isatty()):
            links = [x for x in links if x != "-"] + urls.read_links("-")
        for src in args.file:
            links += urls.read_links(src)
        if not links:
            print("no urls given; pass urls as args, --file PATH, or pipe stdin")
            return 1
        added = fetch.add_players(links, urls.resolve)
        print(
            f"added {len(added)} new players (registry now "
            f"{len(fetch.load_registry())})"
        )
        if not added:
            print("nothing new; run 'ch fetch' to refresh existing players")
            return 0
        fetch.fetch(players=min(8, max(1, len(links))), scorecard_workers=args.workers)
        n = normalize.normalize()
        print(f"db rebuilt: {n}")
        return 0

    if args.cmd == "fetch":
        if args.seed:
            rows = json.loads(Path(args.seed).read_text())
            links = [
                r["profile_link"] if r.get("profile_link") else r["source_link"]
                for r in rows
            ]
            fetch.add_players(links, urls.resolve)
            for r in rows:
                prof = r.get("get-player-profile-web") or {}
                if not prof:
                    continue
                pdir = ROOT / "data" / "raw" / str(r["player_id"])
                pdir.mkdir(parents=True, exist_ok=True)
                (pdir / "profile.json").write_text(
                    json.dumps({"status": True, "data": prof})
                )
                if r.get("get-player-statistic"):
                    (pdir / "stats.json").write_text(
                        json.dumps({"status": True, "data": r["get-player-statistic"]})
                    )
                if r.get("get-player-match"):
                    (pdir / "matches_p1.json").write_text(
                        json.dumps(
                            {
                                "status": True,
                                "data": r["get-player-match"],
                                "page": {"next": "partial"},
                            }
                        )
                    )  # force re-pagination beyond page 1
            print(f"seeded {len(rows)} players from {args.seed}")
        stats = fetch.fetch(
            players=args.players, scorecard_workers=args.scorecard_workers
        )
        print(f"fetch done: {stats}")
        return 0

    if args.cmd == "normalize":
        print(f"db rebuilt: {normalize.normalize()}")
        return 0

    if args.cmd == "list":
        for r in fetch.load_registry():
            print(r["player_id"], r.get("source_link"))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
