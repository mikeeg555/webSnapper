#!/usr/bin/env python3
"""Capture periodic screenshots from a Flightradar24 view for timelapse creation."""

from __future__ import annotations

import argparse
import random
import signal
import sys
import textwrap
import time
import re
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path



DEFAULT_URL = "https://www.flightradar24.com/50.00,1.70/5" #"https://www.flightradar24.com/52.81,-117.08/6"


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
    wait_until: str
    wait_for_selector: Optional[str]
    wait_for_selector_timeout_ms: int
    # Cookie auto-accept removed to avoid accidental clicks
    fixed_interval_minutes: Optional[float]
    follow_rotation: bool


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
        "--wait-until",
        choices=["load", "domcontentloaded", "networkidle", "commit"],
        default="networkidle",
        help="Navigation wait strategy passed to Playwright's `wait_until`.",
    )
    parser.add_argument(
        "--wait-for-selector",
        default=None,
        help="CSS selector to wait for after navigation (optional).",
    )
    parser.add_argument(
        "--wait-for-selector-timeout",
        type=int,
        default=5000,
        help="Timeout in milliseconds when waiting for selector.",
    )
    # Cookie auto-accept removed to avoid accidental clicks; run headless without auto-clicks.
    parser.add_argument(
        "--fixed-interval",
        type=float,
        default=None,
        help="Use a fixed interval (in minutes) between snapshots instead of randomizing.",
    )
    parser.add_argument(
        "--follow-rotation",
        action="store_true",
        help="Pan the map longitude between snapshots to follow Earth's rotation.",
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
        wait_until=args.wait_until,
        wait_for_selector=args.wait_for_selector,
        wait_for_selector_timeout_ms=args.wait_for_selector_timeout,
        fixed_interval_minutes=args.fixed_interval,
        follow_rotation=args.follow_rotation,
    )


class StopLoop(Exception):
    """Signal to stop the snapshot loop."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def choose_wait_seconds(config: Config) -> float:
    """Return the wait interval in seconds.

    If `config.fixed_interval_minutes` is set, use that exact interval (in minutes).
    Otherwise return a randomized value between min and max minutes.
    """
    if config.fixed_interval_minutes is not None:
        return float(config.fixed_interval_minutes) * 60.0
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
    if config.fixed_interval_minutes is not None:
        print(f"Interval: fixed {config.fixed_interval_minutes:g} minutes", flush=True)
    else:
        print(f"Interval: {config.min_minutes:g}-{config.max_minutes:g} minutes", flush=True)

    # Prepare rotation-following state if requested. We look for the first
    # latitude,longitude pair in the URL and plan to update the longitude
    # between snapshots according to Earth's rotation (0.25° per minute).
    follow_rotation_active = False
    coord_pattern = r"(?P<lat>[+-]?\d+(?:\.\d+)?),(?P<long>[+-]?\d+(?:\.\d+)?)"
    orig_url = config.url
    current_url = config.url
    if config.follow_rotation:
        m = re.search(coord_pattern, config.url)
        if m:
            try:
                current_lat = float(m.group("lat"))
                current_long = float(m.group("long"))
                # Preserve original coordinates so we can compute an absolute
                # rotation offset from the script start time. This prevents
                # drift from sampling jitter and keeps the terminator aligned
                # with real-world time.
                orig_lat_val = current_lat
                orig_long_val = current_long
                follow_rotation_active = True
            except Exception:
                print("Warning: failed to parse coordinates from URL; --follow-rotation disabled.", flush=True)
                follow_rotation_active = False
        else:
            print("Warning: no coordinate pair found in URL; --follow-rotation disabled.", flush=True)
            follow_rotation_active = False

    # We'll record the start time once the browser is launched and use
    # absolute elapsed time from that moment to compute the rotation.
    start_time: Optional[float] = None

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=config.headless)
            # Record the baseline time for absolute rotation calculations.
            start_time = time.time()
        except Exception as exc:  # Playwright raises implementation-specific Error class
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                raise SystemExit(
                    textwrap.dedent(
                        """
                        Playwright is installed, but Chromium binaries are missing.

                        Run the following command, then start this script again:

                            python -m playwright install chromium

                        (Or use: playwright install)
                        """
                    ).strip()
                ) from exc
            raise

        cycle = 0
        try:
            while True:
                if should_stop:
                    raise StopLoop

                cycle += 1
                # Create a fresh context and page for each cycle to avoid process leaks.
                context = browser.new_context(viewport={"width": config.width, "height": config.height})
                page = context.new_page()

                stamp = utc_stamp()
                out_path = config.output_dir / f"fr24_{stamp}_{cycle:04d}.png"

                # If following rotation, compute the absolute longitude for
                # the current wall-clock time relative to `start_time` so the
                # terminator stays aligned regardless of capture timing.
                if follow_rotation_active and start_time is not None:
                    elapsed_seconds = time.time() - start_time
                    elapsed_hours = elapsed_seconds / 3600.0
                    delta_deg = 15.0 * elapsed_hours
                    # Subtract delta from the original longitude to rotate
                    # westward (follow the terminator). Normalize to [-180,180].
                    current_long = ((orig_long_val - delta_deg + 180.0) % 360.0) - 180.0
                    current_url = re.sub(
                        coord_pattern,
                        f"{orig_lat_val:.2f},{current_long:.2f}",
                        orig_url,
                        count=1,
                    )

                print(f"[{cycle}] Loading page: {current_url}", flush=True)
                try:
                    page.goto(current_url, wait_until=config.wait_until, timeout=config.page_load_timeout_ms)
                except PlaywrightTimeoutError:
                    print(
                        f"[{cycle}] Warning: page load timed out after "
                        f"{config.page_load_timeout_ms} ms; capturing anyway.",
                        flush=True,
                    )

                if config.wait_for_selector:
                    try:
                        print(
                            f"[{cycle}] Waiting for selector '{config.wait_for_selector}'...",
                            flush=True,
                        )
                        page.wait_for_selector(
                            config.wait_for_selector, timeout=config.wait_for_selector_timeout_ms
                        )
                    except PlaywrightTimeoutError:
                        print(
                            f"[{cycle}] Warning: selector '{config.wait_for_selector}' not found within "
                            f"{config.wait_for_selector_timeout_ms} ms; capturing anyway.",
                            flush=True,
                        )

                if config.settle_seconds > 0:
                    time.sleep(config.settle_seconds)

                page.screenshot(path=str(out_path), full_page=False)
                print(f"[{cycle}] Saved {out_path}", flush=True)
                # Record the exact time we finished this snapshot.
                snapshot_time = time.time()

                # Close context immediately after capture to release resources.
                try:
                    context.close()
                except Exception as exc:
                    print(f"Warning: error closing context after capture: {exc}", flush=True)

                # Decide how long to wait until the next capture.
                wait_seconds = choose_wait_seconds(config)

                # Remember this snapshot's timestamp for the next cycle. The
                # actual longitude shift for the next navigation will be
                # computed from the time difference between that future start
                # and this `snapshot_time`.
                prev_snapshot_time = snapshot_time

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
            try:
                browser.close()
            except Exception as exc:  # Defensive: ignore errors during cleanup
                print(f"Warning: error closing browser: {exc}", flush=True)


def main() -> int:
    config = parse_args()
    run(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
