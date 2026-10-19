# model-watch

Pings you on GitHub when a free model appears, a free model you use becomes paid,
or a provider quietly changes what it says about retention/training.

Runs on GitHub Actions every 6 hours. No API keys, no dependencies, no server.

## What it watches

| Source | Used for |
|---|---|
| `models.dev/api.json` | model catalogs + pricing for 232 providers |
| `openrouter.ai/api/v1/models` | OpenRouter pricing (fresher than models.dev) |
| `opencode.ai/v2/docs/console/models/` | the prose that states zero-retention / 30-day / trains-on-your-data |
| `openrouter.ai/docs/guides/privacy/provider-logging` | OpenRouter data-policy wording |

The retention signal for OpenCode Zen models only exists in prose, so the script
classifies each free-model sentence into `zero-retention`, `retained-Nd`,
`trains-or-logs`, `logged`, or `unstated`, and diffs that classification plus a
hash of the whole doc.

## Alerts it produces

```
NEW FREE  opencode/glm-6-flash-free
no longer free  openrouter/poolside/laguna-s-2.1:free
NO LONGER FREE  opencode/kimi-k2.5-free  <-- you use this
POLICY CHANGED  opencode/beam-free  zero-retention -> retained-30d
    now says: Beam Free is free ... retains it for 30 days for safety reasons.
DOC CHANGED  https://opencode.ai/v2/docs/console/models/  (reread it)
WATCHED MODEL IS NOT FREE  opencode/claude-sonnet-5
WATCHED MODEL DELISTED  opencode/grok-code  <-- you use this
```

"you use this" comes from the `watch` list in `config.json`.

## Setup (5 minutes)

1. Create an empty GitHub repo and push these files:

```
watch.py
config.json
state.json          # first baseline, already generated
.github/workflows/model-watch.yml
```

2. Edit `config.json`:

```json
{
  "providers": ["opencode", "openrouter"],
  "watch": ["opencode/kimi-k2.5-free"]
}
```

3. Repo → Settings → Actions → General → Workflow permissions →
   **Read and write permissions**. The workflow commits the new baseline and
   opens issues; without this it fails.

4. Actions tab → `model-watch` → **Run workflow**. First run against the
   committed `state.json` is silent unless something changed since it was made.

Alerts land as comments on one rolling issue titled `[model-watch] changes`.
GitHub emails you for issue comments on your own repo, so no extra wiring.

## Optional: phone push instead of / alongside GitHub issues

`ntfy.sh` needs no account:

1. Install the ntfy app, subscribe to a topic you invent, e.g. `mymodelwatch-9f3a`.
2. Repo → Settings → Secrets and variables → Actions → New secret
   `ALERT_WEBHOOK_URL` = `https://ntfy.sh/mymodelwatch-9f3a`.

Any other webhook works too — Discord/Slack JSON (`content`/`text`) is sent
automatically when the URL is not an ntfy one.

## Run locally

```
python3 watch.py            # real run, rewrites state.json
python3 watch.py --dry-run  # print alerts, touch nothing
```

Windows: `watch.py` runs as-is with `python watch.py` in that folder.
