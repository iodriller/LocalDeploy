# Contributing

This project is intentionally small and local-first. Keep changes focused on safe local inference, Windows ergonomics, and compatibility with local HTTP clients.

## Local Checks

From the repository root:

```powershell
.\scripts\smoke_test.ps1
pytest -q
```

Dependencies come from `requirements-dev.txt` (what `scripts/start.ps1` and CI use), or
equivalently an editable install: `pip install -e .[dev]`. The package itself is defined
in `pyproject.toml`; bump `localdeploy/__init__.py::__version__` and add a `CHANGELOG.md`
section together (a test enforces they match).

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
