# WebSnapper: Flightradar24 Timelapse Capture

This project contains a Python app that takes recurring snapshots of a Flightradar24 map page and saves images to a subfolder for later cropping/timelapse generation.

## What it does

- Opens a Chromium browser with Playwright.
- Loads your Flightradar24 URL.
- Saves a screenshot.
- Waits a **random** time between 5 and 10 minutes (configurable).
- Repeats until stopped.

Default URL:

- `https://www.flightradar24.com/52.81,-117.08/6`

Default output folder:

- `snapshots/flightradar24/`

## Setup

1. Create and activate a virtual environment (recommended):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install playwright
   python -m playwright install chromium
   ```

## Run

```bash
python flight_snapshotter.py
```

#### Fixed interval example

Run with a fixed 6-minute interval (no randomization) and a 10s page load timeout:

```powershell
python flight_snapshotter.py --fixed-interval 6 --wait-until load --page-load-timeout 10000
```

### Useful options

```bash
python flight_snapshotter.py \
  --min-minutes 5 \
  --max-minutes 10 \
  --output-dir snapshots/flightradar24 \
  --width 1920 \
  --height 1080
```

Other flags:

- `--url <URL>`: choose a different Flightradar24 map location/view.
- `--headed`: run with a visible browser window instead of headless mode.
- `--page-load-timeout <ms>`: timeout for page loading.
- `--settle-seconds <seconds>`: extra wait after loading before capture.

## Troubleshooting

If you see an error like `Executable doesn't exist ... chrome-headless-shell` when starting the script, Playwright browser binaries are not installed yet. Run:

```bash
python -m playwright install chromium
```

Then re-run `python flight_snapshotter.py`.

## Stopping

Press `Ctrl+C` to stop gracefully after the current cycle.

## Next step: crop + timelapse

Once you have snapshots, you can later crop just the map region and encode a video (e.g., with `ffmpeg`).
