# Screenshots

Captured from a real run of the UI (seeded with a few synthetic completed benchmark runs
so the page renders populated, not empty) — not mockups.

## Setup & Deploy

Hardware detection, live VRAM, fit budget, model deploy, and the model list, all on one screen.

![Setup & Deploy tab (dark)](screenshots/setup-deploy.png)

The whole UI also ships with a light theme (toggle in the top bar):

![Setup & Deploy tab (light)](screenshots/setup-deploy-light.png)

## Model catalog

One search across the Ollama library and Hugging Face GGUF repos — source is a column,
size chips are fit-checked pulls, and ⚖ jumps to the quant advisor pre-filled.

![Model catalog](screenshots/model-catalog.png)

## Chat playground

A real streamed reply from a local model — the meta line separates model-load time
(`first token`) from generation speed (`tok/s`).

![Chat playground](screenshots/chat-playground.png)

## Benchmark & Compare

The built-in 25-test bench, profile picker with pulled/not-pulled status per model, and
run history.

![Benchmark & Compare tab](screenshots/benchmark-compare.png)

## Animated demo

The README's demo GIF ([docs/assets/demo.gif](assets/demo.gif)) is captured the same way,
as a short scripted tour with a visible cursor.

## Regenerating these

Screenshots and the demo GIF are captured deterministically with Playwright — no network
access required, since the UI is seeded with synthetic localStorage history rather than a
live benchmark run. (They look best on a machine with a GPU and a few pulled models, but
degrade gracefully without them.)

```powershell
pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/capture_screenshots.py
python scripts/capture_demo_gif.py
```

This overwrites the PNGs in `docs/screenshots/` and `docs/assets/demo.gif`. Re-run after
any UI layout change so these stay current with the actual app.
