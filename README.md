# observe (prototype)

A live map of incidents in Ho Chi Minh City: flooding, accidents, fires, road
closures, fallen trees and similar, built from Facebook posts and Google News.

## How it works

```
collect  → city-wide keyword searches ("ngập TPHCM", "tai nạn TPHCM", …)
           + focused searches for hot areas → data/posts.jsonl
pending  → posts that nobody has reviewed yet
(review) → for each post, one line in data/extractions.jsonl: is it an event, what, where
build    → geocode places (Google Places API, results cached), group nearby reports
           of the same type, find hot areas → map/events.geojson + data/areas.json
serve    → map page at http://127.0.0.1:8000
```

**Extraction is manual in this prototype.** Posts are reviewed by hand, or by an
assistant reading `observe pending`, and an LLM will do it later. Each line in
`data/extractions.jsonl` looks like one of these:

```json
{"post_id": "fb:123", "is_event": true, "type": "flood", "place": "đường Nguyễn Hữu Cảnh", "district": "Bình Thạnh", "summary": "Ngập sâu 40cm", "severity": "high"}
{"post_id": "fb:456", "is_event": false}
```

- **Types:** `flood`, `accident`, `fire`, `closure`, `congestion`, `fallen_tree`, `collapse`, `other`
- **Severity:** `low`, `medium` (the default) or `high`

**Grouping reports into events:** two reports count as the same event when they have the
same type, are within 400 m of each other, and are less than 2 h apart. An event
stays on the map for a set time after its latest report: 3 h for floods, 2 h for
accidents, 6 h for closures.

**Hot areas:** each ~2.2 km grid cell is checked for recent reports.
- A cell becomes hot when it has ≥ 3 distinct posts in the last 2 hours, or ≥ 2 posts
  from different sources.
- The next `collect` searches the hot cell's place names first, e.g. `ngập Nguyễn Hữu Cảnh`.
- At most 3 areas are hot at once. An area that cools down gets searched one more
  run, then drops out.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env   # then fill it in
```

- **Google:** a service account (or an API key) whose project has the
  **Places API (New)** enabled. The classic Geocoding API won't work here, because
  it only accepts API keys.
- **Facebook:** cookies of a **throwaway** account, exported as JSON with the
  Cookie-Editor extension and saved to `secrets/facebook_cookies.json`. Don't log
  out of that account in the browser: logging out invalidates the cookies.

## Run

```bash
.venv/bin/observe collect      # every ~30 min
.venv/bin/observe pending      # review these, append to data/extractions.jsonl
.venv/bin/observe build
.venv/bin/observe serve
```

## Prototype limits

- **Google terms:** the map uses OpenStreetMap tiles through Leaflet. Google's terms
  require Places results to be shown on a Google map, so a public version must
  switch to the Google Maps JavaScript API, which needs a browser API key.
- **Facebook recent-posts filter:** it's experimental, since Facebook doesn't
  document it. If searches start failing, set `FB_RECENT_FILTER=0`.
- **Account limits:** one Facebook account has a per-run request cap
  (`FB_MAX_QUERIES_PER_RUN` × `FB_PAGES_PER_QUERY`). Hot-area queries run first,
  so the cap cuts base queries before hot-area ones.

## Tests

```bash
.venv/bin/pytest -q
```

They cover extraction parsing, the event pipeline (with a fake geocoder) and the
GeoJSON export.
