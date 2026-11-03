"""observe: check an A->B route for jams and look for posts about them.

Examples:
  observe "Chợ Bến Thành, Quận 1" "Sân bay Tân Sơn Nhất"
  observe 10.7725,106.6980 10.8185,106.6588 --speeds TRAFFIC_JAM,SLOW
  observe "A" "B" --briefing          # also search main route streets when nothing is red
  observe "A" "B" --watch 300         # re-poll every 5 min, search only new jams
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import ConfigError, Settings, load_settings
from .places import (
    Nominatim,
    build_queries,
    dedupe,
    main_streets,
    match_text,
    streets_for_steps,
)
from .routes import Route, RoutesClient, RoutesError
from .sources import Post, SourceError
from .traffic import Jam, attach_steps, find_jams, same_jam, speed_breakdown_m

log = logging.getLogger("observe")

SPEED_LABEL = {"NORMAL": "green", "SLOW": "orange", "TRAFFIC_JAM": "RED"}


@dataclass
class Target:
    """Something to search posts for: a jam, or the whole route (briefing)."""

    label: str
    streets: list[str]
    jam: Jam | None = None
    posts: list[Post] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def fmt_min(seconds: int) -> str:
    return f"{seconds / 60:.0f} min"


def fmt_time(dt: datetime | None, tz) -> str:
    return dt.astimezone(tz).strftime("%H:%M %d/%m") if dt else "unknown time"


def gmaps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps?q={lat:.5f},{lng:.5f}"


def print_route(route: Route) -> None:
    print(f"\nRoute: {route.description or '(no description)'}")
    print(
        f"  {route.distance_m / 1000:.1f} km, {fmt_min(route.duration_s)} with traffic "
        f"vs {fmt_min(route.static_duration_s)} without "
        f"(delay {fmt_min(max(route.delay_s, 0))}, ratio {route.congestion_ratio:.2f})"
    )
    breakdown = speed_breakdown_m(route)
    if breakdown:
        parts = [
            f"{SPEED_LABEL.get(k, k)} {v / 1000:.2f} km"
            for k, v in sorted(breakdown.items(), key=lambda kv: -kv[1])
        ]
        print("  traffic: " + ", ".join(parts))


def name_jam(jam: Jam, route: Route, geocoder: Nominatim | None) -> None:
    attach_steps(jam, route.steps)
    names = streets_for_steps(jam.steps)
    if geocoder is not None:
        info = geocoder.reverse(jam.midpoint)
        time.sleep(1.0)  # Nominatim policy: max 1 request/second
        if info:
            if info["road"]:
                names.insert(0, info["road"])
            jam.area = info["area"]
    jam.streets = dedupe(names)


def print_jam(i: int, jam: Jam) -> None:
    lat, lng = jam.midpoint
    print(
        f"\n  Jam #{i}: {jam.length_m / 1000:.2f} km ({'/'.join(sorted(jam.speeds))})"
        f"\n    streets: {', '.join(jam.streets) or '(unknown)'}"
        + (f"\n    area:    {jam.area}" if jam.area else "")
        + f"\n    map:     {gmaps_link(lat, lng)}"
    )


def collect(target: Target, sources: list, settings: Settings, since: datetime) -> None:
    queries = build_queries(target.streets, settings.fb_max_queries)
    if not queries:
        target.errors.append("no street names found, nothing to search")
        return
    seen_urls: set[str] = set()
    for src in sources:
        for q in queries:
            try:
                found = src.search(q, since)
            except SourceError as e:
                log.error("%s: %s", src.name, e)
                target.errors.append(f"{src.name}: {e}")
                break  # source-wide failure (e.g. expired login): skip its other queries
            for p in found:
                if p.url and p.url in seen_urls:
                    continue
                seen_urls.add(p.url)
                p.matched_streets, p.matched_terms = match_text(p.text, target.streets)
                target.posts.append(p)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    target.posts.sort(key=lambda p: (p.strong_match, p.created_at or epoch), reverse=True)


def print_target(target: Target, tz) -> None:
    print(f"\n  Posts for {target.label} (searched: {', '.join(target.streets)})")
    for err in target.errors:
        print(f"    ! {err}")
    if not target.posts:
        print("    no recent posts found")
        return
    for p in target.posts:
        tag = "MATCH" if p.strong_match else "weak "
        text = " ".join(p.text.split())
        if len(text) > 220:
            text = text[:217] + "..."
        print(
            f"    [{tag}] {p.source} · {fmt_time(p.created_at, tz)} · {p.author or '?'}"
            f"\n            {text}"
            f"\n            {p.url}"
        )


def dump(run_dir: Path, route: Route, jams: list[Jam], targets: list[Target]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    data = {
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "route": {
            "description": route.description,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "static_duration_s": route.static_duration_s,
            "speed_breakdown_m": speed_breakdown_m(route),
        },
        "jams": [
            {
                "length_m": round(j.length_m),
                "speeds": sorted(j.speeds),
                "midpoint": j.midpoint,
                "streets": j.streets,
                "area": j.area,
                "instructions": [s.instruction for s in j.steps],
            }
            for j in jams
        ],
        "targets": [
            {
                "label": t.label,
                "streets": t.streets,
                "errors": t.errors,
                "posts": [
                    {
                        "source": p.source,
                        "query": p.query,
                        "url": p.url,
                        "author": p.author,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                        "text": p.text,
                        "matched_streets": p.matched_streets,
                        "matched_terms": p.matched_terms,
                    }
                    for p in t.posts
                ],
            }
            for t in targets
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_sources(args, settings: Settings, http: httpx.Client) -> list:
    sources: list = []
    if not args.no_facebook:
        from .sources.facebook import FacebookSource

        try:
            sources.append(
                FacebookSource(
                    settings.fb_cookies_file,
                    settings.fb_state_file,
                    settings.fb_proxy,
                    settings.fb_results_per_query,
                    settings.fb_request_delay_s,
                )
            )
        except SourceError as e:
            log.error("Facebook disabled: %s", e)
    if settings.enable_google_news and not args.no_news:
        from .sources.google_news import GoogleNewsSource

        sources.append(GoogleNewsSource(http))
    return sources


def poll_once(args, settings, routes, geocoder, sources, known: list[Jam], first: bool) -> list[Jam]:
    route = routes.compute(args.origin, args.destination)
    print(f"\n=== {datetime.now(settings.timezone).strftime('%H:%M:%S')} ===")
    print_route(route)

    jams = find_jams(route, set(args.speeds), settings.min_jam_length_m)
    for j in jams:
        name_jam(j, route, geocoder)
    if jams:
        print(f"\n  {len(jams)} jam(s) detected:")
        for i, j in enumerate(jams, 1):
            print_jam(i, j)
    else:
        print(f"\n  No {'/'.join(args.speeds)} stretches >= {settings.min_jam_length_m} m.")

    cleared = [k for k in known if not any(same_jam(k, j) for j in jams)]
    for k in cleared:
        print(f"  cleared: jam on {', '.join(k.streets) or '?'}")

    targets: list[Target] = []
    for i, j in enumerate(jams, 1):
        if any(same_jam(j, k) for k in known):
            continue  # already searched in an earlier poll
        if j.streets:
            targets.append(Target(label=f"jam #{i}", streets=j.streets[:2], jam=j))
    if first and args.briefing and not targets:
        streets = main_streets(route)
        if streets:
            targets.append(Target(label="route briefing", streets=streets))

    if targets and sources:
        since = datetime.now(timezone.utc) - timedelta(minutes=settings.lookback_minutes)
        for t in targets:
            collect(t, sources, settings, since)
            print_target(t, settings.timezone)
    elif targets:
        print("\n  (no post sources enabled, skipping search)")

    out = dump(Path(args.runs_dir), route, jams, targets)
    print(f"\n  saved: {out}")
    return jams


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="observe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("origin", help='address or "lat,lng"')
    ap.add_argument("destination", help='address or "lat,lng"')
    ap.add_argument(
        "--speeds",
        default="TRAFFIC_JAM",
        type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
        help="speed categories that count as a jam (default TRAFFIC_JAM; add SLOW to test off-peak)",
    )
    ap.add_argument("--briefing", action="store_true", help="search main route streets if no jam")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="re-poll every N seconds")
    ap.add_argument("--max-polls", type=int, default=0, help="stop watching after N polls (0 = no limit)")
    ap.add_argument("--no-facebook", action="store_true")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--env", default=".env", help="env file (default .env)")
    ap.add_argument("--runs-dir", default="runs", help="where JSON results are saved")
    args = ap.parse_args(argv)

    try:
        settings = load_settings(args.env)
        settings.require_google_credentials()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.watch is not None and args.watch < 60:
        print("--watch must be at least 60 seconds (each poll is a paid Routes API call)", file=sys.stderr)
        return 2

    with httpx.Client(follow_redirects=True) as http:
        routes = RoutesClient(
            http,
            service_account_file=settings.google_service_account_file,
            api_key=settings.google_api_key,
            travel_mode=settings.travel_mode,
            language=settings.language,
            region=settings.region,
        )
        geocoder = (
            Nominatim(http, settings.nominatim_user_agent, settings.language)
            if settings.enable_nominatim
            else None
        )
        sources = build_sources(args, settings, http)
        print("sources: " + (", ".join(s.name for s in sources) or "none"))

        known: list[Jam] = []
        polls = 0
        try:
            while True:
                polls += 1
                try:
                    known = poll_once(args, settings, routes, geocoder, sources, known, polls == 1)
                except RoutesError as e:
                    log.error("poll %d failed: %s", polls, e)
                    if args.watch is None:
                        return 1
                if args.watch is None or (args.max_polls and polls >= args.max_polls):
                    return 0
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0


if __name__ == "__main__":
    sys.exit(main())
