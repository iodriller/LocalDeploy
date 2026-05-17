# Terminal Testing

Swagger UI shows placeholder values such as `"profile": "string"` and `max_output_tokens: 0`. Do not send those. Use a valid profile name and omit fields you do not need.

The easiest path is the terminal helper:

```powershell
cd path\to\LocalDeploy
.\scripts\chat.ps1 -Prompt "How are you?"
```

Interactive chat:

```powershell
.\scripts\chat.ps1
```

Useful commands inside interactive mode:

```text
:profiles
:profile gemma3_12b_ollama_safe
:tokens 512
:raw
:quit
```

Start the server if it is not already running:

```powershell
.\scripts\chat.ps1 -StartServer -Prompt "Say ready."
```

Use the 12B profile:

```powershell
.\scripts\chat.ps1 -Profile gemma3_12b_ollama_safe -Prompt "Answer in one paragraph: what are you best at?"
```

Show the full JSON response:

```powershell
.\scripts\chat.ps1 -Prompt "Give me a short test response." -Raw
```

List configured profiles:

```powershell
.\scripts\chat.ps1 -Profiles
```

## Minimal API JSON

For `/chat`, this is enough:

```json
{
  "profile": "gemma3_4b_ollama_safe",
  "prompt": "how are you",
  "safe_mode": true,
  "max_output_tokens": 256
}
```

You can also omit the profile and use the default:

```json
{
  "prompt": "how are you"
}
```

Do not send:

```json
{
  "profile": "string",
  "model": "string",
  "backend": "string",
  "max_output_tokens": 0,
  "context_limit": 0
}
```

Those are schema placeholders, not real settings.
