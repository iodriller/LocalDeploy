# Contributing

This project is intentionally small and local-first. Keep changes focused on safe local inference, Windows ergonomics, and compatibility with local HTTP clients.

## Local Checks

From the repository root:

```powershell
.\scripts\smoke_test.ps1
pytest -q
```

The smoke test covers Python syntax, `config.example.json`, PowerShell parse checks, import-time API validation, and optional local HTTP routes if the API is already running. It does not require Ollama models to be pulled.

For the offline-egress guarantee:

```powershell
python scripts\egress_selftest.py
```

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
