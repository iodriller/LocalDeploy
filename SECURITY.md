# Security Policy

## Local-Only Boundary

LocalDeploy is designed to call only local inference backends:

- `http://localhost:11434` for Ollama
- `http://localhost:8080` for llama.cpp

The server rejects non-local backend URLs in code. Do not remove this guard without an explicit security review.

## Sensitive Files

Do not commit:

- `.env`
- `config.json`
- local GGUF/model files
- benchmark outputs containing private prompts
- screenshots or uploaded images from private workflows

## Reporting

If this repository is published, report security issues through the private security channel for that repository. Do not include private prompts, images, tokens, or model files in public issues.
