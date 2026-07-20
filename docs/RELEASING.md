# Releasing LocalDeploy

This project builds a wheel and source archive in CI. Publishing to PyPI needs one account-level setup step that cannot be stored in the repository.

## First PyPI publish

The `localdeploy` project does not exist on PyPI yet. Before publishing a GitHub release, sign in to PyPI and add a pending trusted publisher at <https://pypi.org/manage/account/publishing/> with these values:

| Field | Value |
|---|---|
| PyPI project name | `localdeploy` |
| GitHub owner | `iodriller` |
| GitHub repository | `LocalDeploy` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

The GitHub `pypi` environment already exists. A pending publisher does not reserve the package name until the first successful upload, so finish the first release soon after creating it.

The v0.5.0 upload failed because no matching PyPI publisher had been registered. The build itself succeeded. Do not reuse that version on PyPI. Prepare and publish the next version instead.

## Prepare a release

1. Update `localdeploy/__init__.py::__version__` and add the matching section to `CHANGELOG.md`.
2. Run the local checks from a clean checkout.
3. Merge the release change to `main` and wait for CI.
4. Create a GitHub release whose tag is exactly `v<package-version>`, for example `v0.6.0`.

Local checks:

```powershell
python -m ruff check .
pytest -q
node --test tests/js/frontend-modules.test.mjs
python scripts\egress_selftest.py
python -m build
python -m twine check dist/*
```

The Publish to PyPI workflow checks that the tag and package version match, builds both distributions, and uses OpenID Connect to request a short-lived PyPI token. No long-lived PyPI token should be added to GitHub secrets.

After the workflow completes, verify the package from a clean environment outside the repository:

```powershell
python -m venv release-check
.\release-check\Scripts\python.exe -m pip install localdeploy==0.6.0
.\release-check\Scripts\localdeploy.exe --version
```

Use the released version in place of `0.6.0`.

## Settings to enable when the repository becomes public

GitHub does not expose some public-repository security settings while this repository is private. After changing visibility, enable these settings before announcing the project:

- Private vulnerability reporting under Settings / Security / Advanced Security.
- Secret scanning and push protection when GitHub offers them for the repository.
- A ruleset for `main` that requires the CI workflow and blocks force pushes and deletion.
- Automatically delete head branches after pull requests merge.

The security policy explains the supported private reporting route.
