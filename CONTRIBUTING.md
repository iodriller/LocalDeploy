# Contributing

This project is intentionally small and local-first. Keep changes focused on safe local inference, Windows ergonomics, and compatibility with local clients such as YBM.

## Local Checks

From the repository root:

```powershell
python -m py_compile api_server.py test_models.py
python -m json.tool config.example.json > $null
.\scripts\smoke_test.ps1
```

If the API server is running, the smoke test also checks local HTTP routes. It does not require Ollama models to be pulled.

## Pull Request Expectations

- Do not add cloud inference SDKs or hosted inference defaults.
- Keep backend URLs localhost-only unless there is a deliberate security review.
- Keep `.env`, `config.json`, GGUF files, and model weights out of Git.
- Add or update docs when changing request fields, profiles, or safety behavior.
- Prefer clear validation errors over silent clamping unless `allow_clamp=true` is explicitly requested.

## Code Style

- Python should stay dependency-light and readable.
- PowerShell scripts should work on Windows 10 PowerShell.
- Avoid broad refactors unless they directly support the change.
