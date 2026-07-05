## Summary

-

## Validation

- [ ] `.\scripts\smoke_test.ps1`
- [ ] `pytest -q`
- [ ] `python scripts\egress_selftest.py`

## Local Safety

- [ ] No cloud inference API or SDK was added.
- [ ] Backend calls remain localhost-only.
- [ ] `.env`, `config.json`, and model files are not committed.
