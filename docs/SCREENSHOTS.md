# Screenshots

Captured from a real run of the UI (seeded with a few synthetic completed benchmark runs
so the page renders populated, not empty) — not mockups.

## Setup & Deploy

Hardware detection, fit budget, model deploy, and the model list, all on one screen.

![Setup & Deploy tab](screenshots/setup-deploy.png)

## Benchmark & Compare

The built-in 25-test bench, profile picker with pulled/not-pulled status per model, and
run history.

![Benchmark & Compare tab](screenshots/benchmark-compare.png)

## Regenerating these

Screenshots are captured deterministically with Playwright — no Ollama, GPU, or network
access required, since the UI is seeded with synthetic localStorage history rather than a
live benchmark run:

```powershell
pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/capture_screenshots.py
```

This overwrites the PNGs in `docs/screenshots/`. Re-run it after any UI layout change so
these stay current with the actual app.
