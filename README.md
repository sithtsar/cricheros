# cricheroes scraper

Fetches cricket player data from cricheroes.com into a sqlite database + CSVs
you can analyze. Incremental: it only fetches what it does not already have, and
a killed run resumes where it stopped.

## Run it

```bash
uv sync                       # install once
uv run ch add --file data/urls.txt   # add the 30 known players, fetch, build db
```

One player:

```bash
uv run ch add https://cricheroes.com/player-profile/16993605/aashutosh-ajay-sharma/matches
```

Urls can also come from any .txt / .tsv / .csv file (first url on each line) or
stdin:

```bash
uv run ch add --file myplayers.csv
printf "%s\n" "https://chshare.link/player/abc" | uv run ch add -
```

## Commands

| command                  | what it does                                     |
|--------------------------|--------------------------------------------------|
| `ch add <url...>`        | add player(s), fetch their data, rebuild db      |
| `ch add --file FILE`     | same, urls read from FILE (`-` = stdin)          |
| `ch fetch`               | refetch whatever is missing for all players      |
| `ch normalize`           | rebuild db + CSVs from the raw cache             |
| `ch list`                | show registered players                          |

## Everything lives in data/

```
data/urls.txt                        the 30 known player urls (edit + re-add to grow)
data/registry/players.json           master list of registered players
data/raw/<player_id>/                raw api responses, one file per thing
data/raw/scorecards/<match_id>.json  full scorecard per match
data/players.json                    combined dump, one object per player
data/analysis.db                     sqlite: 4 tables
data/*.csv                           same 4 tables as csv
```

All of it (except `logs/`) is committed to git — the data travels with the repo.

## The 4 tables (sqlite: `sqlite3 data/analysis.db`)

- `players` — who: name, dob, city, batting hand, bowling style, totals
- `player_career` — career stats: runs, avg, sr, wickets, econ, catches, captaincy
- `matches` — one row per match: date, ground, tournament, teams, result
- `match_performances` — the detailed grain: each player's runs/wickets in each
  match. This is the table for "is he improving" style questions.

Join examples:

```sql
SELECT name, batting_runs FROM player_career JOIN players USING(player_id)
ORDER BY batting_runs DESC LIMIT 10;

SELECT p.name, round(avg(mp.runs),1) recent_avg
FROM match_performances mp JOIN players p USING(player_id) JOIN matches m USING(match_id)
WHERE m.start_datetime >= '2026-06-01'
GROUP BY p.name HAVING count(*) > 3 ORDER BY recent_avg DESC;
```

DuckDB reads the sqlite file directly if you prefer columnar:
`duckdb -c "ATTACH 'data/analysis.db'"`.

## How it works (short version)

cricheroes.com is a javascript site behind Cloudflare. Its own app talks to a
private api (`api.cricheroes.in`) — this tool talks to that api directly. Each
response is saved to `data/raw/` the moment it arrives, so nothing is ever
fetched twice. Fetching is parallel (12 workers by default).

Failures that will never succeed (abandoned matches, players with no matches)
leave a `.fail` marker file and are skipped next time. Delete the marker to
retry.

## Development

```bash
uvx ruff check src/ && uvx ty check src/cricheroes_cli
```

Treat `data/analysis.db` and the CSVs as derived — delete them and
`uv run ch normalize` rebuilds them from `data/raw/`.