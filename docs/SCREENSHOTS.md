# Screenshots

These images come from the real UI. The capture script seeds a few synthetic benchmark runs so comparison views are populated; it does not replace the interface with a mockup.

## Setup and Deploy

![Setup and Deploy tab in dark mode](screenshots/setup-deploy.png)

![Setup and Deploy tab in light mode](screenshots/setup-deploy-light.png)

## Model catalog

![Model catalog](screenshots/model-catalog.png)

## Chat

![Chat playground](screenshots/chat-playground.png)

## Benchmark and Compare

![Benchmark and Compare tab](screenshots/benchmark-compare.png)

## Demo

The README uses [docs/assets/demo.gif](assets/demo.gif), captured by the same tooling.

## Regenerate the files

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/capture_screenshots.py
python scripts/capture_demo_gif.py
```

The commands overwrite the tracked images. Run them after a layout change and review the result before committing.
