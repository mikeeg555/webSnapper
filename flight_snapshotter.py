#!/usr/bin/env python3
"""Capture periodic screenshots from a Flightradar24 view for timelapse creation."""

from __future__ import annotations

import argparse
import random
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path



DEFAULT_URL = "https://www.flightradar24.com/52.81,-117.08/6"


@dataclass(frozen=True)
class Config:
    url: str
    output_dir: Path
    min_minutes: float
    max_minutes: float
    page_load_timeout_ms: int
    settle_seconds: float
    width: int
    height: int
    headless: bool


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description=(
            "Take recurring screenshots of a Flightradar24 map view every 5-10 minutes "
            "(or your custom interval)."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Map URL to capture.")
    parser.add_argument(
        "--output-dir",
        default="snapshots/flightradar24",
        help="Directory to save screenshots.",
    )
    parser.add_argument(
        "--min-minutes",
        type=float,
        default=5.0,
        help="Minimum minutes to wait between snapshots.",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=10.0,
        help="Maximum minutes to wait between snapshots.",
    )
    parser.add_argument(
        "--page-load-timeout",
        type=int,
        default=60000,
        help="Page load timeout in milliseconds.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=8.0,
        help="Extra seconds to wait after page load before screenshot.",
    )
    parser.add_argument("--width", type=int, default=1920, help="Browser width in pixels.")
    parser.add_argument("--height", type=int, default=1080, help="Browser height in pixels.")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with a visible browser window (default is headless).",
    )

    args = parser.parse_args()

    if args.min_minutes <= 0 or args.max_minutes <= 0:
        parser.error("--min-minutes and --max-minutes must be greater than zero.")
    if args.max_minutes < args.min_minutes:
        parser.error("--max-minutes must be greater than or equal to --min-minutes.")

    return Config(
        url=args.url,
        output_dir=Path(args.output_dir),
        min_minutes=args.min_minutes,
        max_minutes=args.max_minutes,
        page_load_timeout_ms=args.page_load_timeout,
        settle_seconds=args.settle_seconds,
        width=args.width,
        height=args.height,
        headless=not args.headed,
    )


class StopLoop(Exception):
    """Signal to stop the snapshot loop."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def choose_wait_seconds(config: Config) -> float:
    return random.uniform(config.min_minutes * 60.0, config.max_minutes * 60.0)


def run(config: Config) -> None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: playwright. Install with `pip install playwright` and `python -m playwright install chromium`."
        ) from exc

    config.output_dir.mkdir(parents=True, exist_ok=True)

    should_stop = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True
        print(f"Received signal {signum}; finishing current cycle then exiting...", flush=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Saving snapshots to: {config.output_dir.resolve()}", flush=True)
    print(f"Target URL: {config.url}", flush=True)
    print(
        f"Interval: {config.min_minutes:g}-{config.max_minutes:g} minutes (randomized)",
        flush=True,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=config.headless)
        context = browser.new_context(viewport={"width": config.width, "height": config.height})
        page = context.new_page()

        cycle = 0
        try:
            while True:
                if should_stop:
                    raise StopLoop

                cycle += 1
                stamp = utc_stamp()
                out_path = config.output_dir / f"fr24_{stamp}.png"

                print(f"[{cycle}] Loading page...", flush=True)
                try:
                    page.goto(config.url, wait_until="networkidle", timeout=config.page_load_timeout_ms)
                except PlaywrightTimeoutError:
                    print(
                        f"[{cycle}] Warning: page load timed out after "
                        f"{config.page_load_timeout_ms} ms; capturing anyway.",
                        flush=True,
                    )

                if config.settle_seconds > 0:
                    time.sleep(config.settle_seconds)

                page.screenshot(path=str(out_path), full_page=False)
                print(f"[{cycle}] Saved {out_path}", flush=True)

                wait_seconds = choose_wait_seconds(config)
                wake_at = time.time() + wait_seconds
                print(
                    f"[{cycle}] Sleeping for {wait_seconds/60:.2f} minutes "
                    f"(until {datetime.fromtimestamp(wake_at).strftime('%Y-%m-%d %H:%M:%S')}).",
                    flush=True,
                )

                while time.time() < wake_at:
                    if should_stop:
                        raise StopLoop
                    time.sleep(min(1.0, wake_at - time.time()))
        except StopLoop:
            print("Stopped.", flush=True)
        finally:
            context.close()
            browser.close()


def main() -> int:
    config = parse_args()
    run(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
