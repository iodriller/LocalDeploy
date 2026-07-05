# Examples

Minimal samples for calling the LocalDeploy API. They assume the server is running at `http://127.0.0.1:8000` (start it with `.\scripts\start.ps1`).

| File | What it shows |
|---|---|
| [curl_chat.sh](curl_chat.sh) | Native `/chat` and OpenAI-compatible `/v1/chat/completions` via `curl`. |
| [python_client.py](python_client.py) | Native `/chat`, profile listing, and OpenAI SDK usage from Python. |
| [vision_chat.ps1](vision_chat.ps1) | Sending an image to `/vision` from PowerShell. |

The native `/chat` API is the simplest entry point. The OpenAI-compatible endpoint is for apps that already speak `/v1/chat/completions` — no rewrites needed, just point them at `http://127.0.0.1:8000/v1`.

See [../docs/API_OPTIONS.md](../docs/API_OPTIONS.md) for the full request schema and [../docs/MODELS.md](../docs/MODELS.md) for the model catalog.
