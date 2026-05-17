# GitHub Setup

This project is ready to be pushed as a GitHub repository. Machine-local files are ignored by default:

- `.env`
- `config.json`
- `.venv/`
- GGUF/model files
- benchmark outputs

## First Commit

```powershell
cd "C:\for fun\LocalDeploy"
git init
git add .
git status
git commit -m "Initial LocalDeploy project"
```

## Create A GitHub Remote

Using GitHub CLI:

```powershell
gh repo create LocalDeploy --private --source . --remote origin --push
```

Or create an empty repository on GitHub, then run:

```powershell
git remote add origin https://github.com/<owner>/LocalDeploy.git
git branch -M main
git push -u origin main
```

Keep the repository private unless you replace the conservative `LICENSE` file with the open-source license you actually want.

## CI

The included GitHub Actions workflow validates:

- Python syntax
- JSON examples
- PowerShell script parsing
- FastAPI route import
- Safety validation for oversized requests

It does not pull models and does not call cloud inference APIs.
