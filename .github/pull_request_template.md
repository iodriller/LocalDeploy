## Summary

-

## Validation

- [ ] `python -m py_compile api_server.py test_models.py`
- [ ] `python -m json.tool config.example.json`
- [ ] `.\scripts\smoke_test.ps1`

## Local Safety

- [ ] No cloud inference API or SDK was added.
- [ ] Backend calls remain localhost-only.
- [ ] `.env`, `config.json`, and model files are not committed.
