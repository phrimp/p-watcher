# observe (prototype)

Polls Google's live traffic for one A→B route. When a stretch is red, it works out
which streets the red stretch is on and searches Facebook and Google News for recent
posts about them.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Fill in `.env` (see `.env.example`):

- **Google:** `GOOGLE_SERVICE_ACCOUNT_FILE` (already points to `secrets/smartsolarvn-secret.json`)
  or `GOOGLE_MAPS_API_KEY`. The project must have the Routes API enabled.
- **Facebook:** log in to a **throwaway** account in a browser, then use the Cookie-Editor
  extension (Export → JSON) to export its cookies. Save them as `secrets/facebook_cookies.json`.

`.env`, `secrets/` and `runs/` are git-ignored.

## Run

```bash
# one check
.venv/bin/observe "Ngã tư Hàng Xanh, Bình Thạnh" "Sân bay Tân Sơn Nhất"

# off-peak testing: treat orange (SLOW) as jams too, and search main streets if nothing is red
.venv/bin/observe "A" "B" --speeds TRAFFIC_JAM,SLOW --briefing

# Routes API only, no scraping
.venv/bin/observe "A" "B" --no-facebook --no-news

# watch: re-poll every 5 min for 6 polls, searching only newly appeared jams
.venv/bin/observe "A" "B" --watch 300 --max-polls 6
```

Origin and destination can be addresses or `lat,lng`. Each run's full result is saved
to `runs/run-*.json`.

## Tests

```bash
.venv/bin/pytest -q
```
