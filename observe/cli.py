"""observe: live map of incidents in Ho Chi Minh City from social posts and news.

  observe collect          search Facebook + Google News, append new posts to data/posts.jsonl
  observe pending          print posts that have no extraction yet (to analyse)
  observe build            extractions -> geocoded events + hot areas -> map/events.geojson
  observe serve            serve the map at http://127.0.0.1:8000

Run `collect` about every 30 minutes; after `build`, hot areas get their own
searches on the next `collect`.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import httpx

from .areas import compute_areas, load_areas, save_areas
from .collect import run_collect
from .config import ConfigError, Settings, load_settings
from .events import build_events
from .export import to_geojson
from .extraction import parse_extractions
from .geocode import CachedGeocoder, GeocodeError, PlacesGeocoder
from .google_auth import GoogleAuth, GoogleAuthError
from .posts import PostStore
from .sources import SourceError

log = logging.getLogger("observe")


def _local(dt: datetime | None, settings: Settings) -> str:
    return dt.astimezone(settings.timezone).strftime("%H:%M %d/%m") if dt else "?"


def cmd_collect(args, settings: Settings) -> int:
    store = PostStore(settings.posts_file)
    known = store.load()
    areas = list(load_areas(settings.areas_file).values()) if not args.no_hot else []
    since = datetime.now(timezone.utc) - timedelta(minutes=settings.lookback_minutes)

    with httpx.Client(follow_redirects=True) as http:
        sources: list = []
        if not args.no_facebook:
            from .sources.facebook import FacebookSource

            try:
                sources.append(
                    FacebookSource(
                        settings.fb_cookies_file,
                        settings.fb_state_file,
                        settings.fb_proxy,
                        settings.fb_pages_per_query,
                        settings.fb_request_delay_s,
                        settings.fb_recent_filter,
                    )
                )
            except SourceError as e:
                log.error("Facebook disabled: %s", e)
        if settings.enable_google_news and not args.no_news:
            from .sources.google_news import GoogleNewsSource

            sources.append(GoogleNewsSource(http))
        if not sources:
            print("no sources enabled", file=sys.stderr)
            return 1

        hot = [a for a in areas if a.state == "hot"]
        print(f"sources: {', '.join(s.name for s in sources)} | hot areas: {len(hot)} | cooling: {len(areas) - len(hot)}")
        report = run_collect(
            sources,
            set(known),
            settings.city_suffix,
            areas,
            since,
            settings.fb_max_queries_per_run,
        )

    store.append(report.new_posts)
    for src, qs in report.queries_run.items():
        print(f"  {src}: {len(qs)} queries")
    print(
        f"new posts: {len(report.new_posts)} (skipped {report.duplicates} already seen, "
        f"{report.off_topic} without event words) -> {settings.posts_file}"
    )
    for err in report.errors:
        print(f"  ! {err}")
    return 1 if report.errors and not report.new_posts else 0


def cmd_pending(args, settings: Settings) -> int:
    posts = PostStore(settings.posts_file).load()
    done: set[str] = set()
    if settings.extractions_file.exists():
        result = parse_extractions(settings.extractions_file.read_text(encoding="utf-8").splitlines())
        done = {e.post_id for e in result.events} | result.rejected_post_ids
    pending = [p for p in posts.values() if p.id not in done]
    pending.sort(key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc))
    for p in pending[: args.limit or None]:
        text = " ".join(p.text.split())
        print(
            json.dumps(
                {
                    "post_id": p.id,
                    "time": _local(p.created_at, settings),
                    "source": p.source,
                    "author": p.author,
                    "query": p.query,
                    "text": text[:700],
                },
                ensure_ascii=False,
            )
        )
    print(f"# {len(pending)} pending of {len(posts)} posts", file=sys.stderr)
    return 0


def cmd_build(args, settings: Settings) -> int:
    posts = PostStore(settings.posts_file).load()
    if not settings.extractions_file.exists():
        print(f"no extractions yet: write {settings.extractions_file} first (see `observe pending`)", file=sys.stderr)
        return 1
    extractions = parse_extractions(settings.extractions_file.read_text(encoding="utf-8").splitlines())
    for err in extractions.errors:
        print(f"  ! extractions {err}")
    for w in extractions.warnings:
        print(f"  ~ extractions {w}")

    now = datetime.now(timezone.utc)
    with httpx.Client() as http:
        try:
            auth = GoogleAuth(settings.google_service_account_file, settings.google_api_key)
            geocoder = CachedGeocoder(PlacesGeocoder(http, auth), settings.geocode_cache_file)
        except (GoogleAuthError, GeocodeError) as e:
            print(f"geocoder unavailable: {e}", file=sys.stderr)
            return 1
        try:
            result = build_events(posts.values(), extractions, geocoder, now)
        finally:
            geocoder.flush()  # keep paid lookups even if the build fails part-way

    areas = compute_areas(result.events, now, load_areas(settings.areas_file))
    save_areas(settings.areas_file, areas)
    fc = to_geojson(result.events, areas, now)
    settings.map_dir.mkdir(parents=True, exist_ok=True)
    tmp = settings.geojson_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(settings.geojson_file)

    live = [e for e in result.events if e.expires_at > now]
    print(f"events: {len(live)} live, {len(result.events) - len(live)} expired | rejected posts: {len(extractions.rejected_post_ids)}")
    for e in sorted(live, key=lambda e: e.last_seen, reverse=True):
        print(
            f"  [{e.type}/{e.severity}] {e.place} — {e.summary} "
            f"({e.report_count} report(s), last {_local(e.last_seen, settings)})"
        )
    for u in result.unlocated:
        print(f"  ? {u.post_id}: {u.reason} (query: {u.query})")
    for pid in result.unknown_post_ids:
        print(f"  ? extraction for unknown post {pid}")
    for a in areas:
        print(f"  area {a.cell} {a.state} heat={a.heat} next queries: {a.queries}")
    print(f"map data -> {settings.geojson_file}")
    return 0


def cmd_serve(args, settings: Settings) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(settings.map_dir))
    # Bound to localhost only: the page shows scraped post text and author names.
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"map: http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="observe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=".env", help="env file (default .env)")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="search sources and store new posts")
    c.add_argument("--no-facebook", action="store_true")
    c.add_argument("--no-news", action="store_true")
    c.add_argument("--no-hot", action="store_true", help="skip hot-area searches")
    c.set_defaults(fn=cmd_collect)

    p = sub.add_parser("pending", help="print posts without an extraction")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_pending)

    b = sub.add_parser("build", help="geocode extractions and write the map data")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("serve", help="serve the map page locally")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    try:
        settings = load_settings(args.env)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO; keep the output readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
