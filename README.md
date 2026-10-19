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
   opens issues; without this it fails. On an **empty** new repo the default is
   read-only and the token lacks `administration`, so set it via the API:

```
gh api -X PUT repos/<you>/model-watch/actions/permissions/workflow \
  -f default_workflow_permissions=write
```

4. Trigger the first run: push any commit touching `watch.py`, `config.json`, or
   the workflow file, or use the Actions tab → `model-watch` → **Run workflow**.
   A schedule-only workflow cannot be dispatched until the file has run once.

Alerts land as comments on one rolling issue titled `[model-watch] changes`.
GitHub emails you for issue comments on your own repo, so no extra wiring.

## Verified end to end

- Two pushes ran green; the second emitted `NEW FREE opencode/glm-5.3-flash-free`
  and opened issue #2.
- Baseline commits touch only `state.json`, and a rerun produces a byte-identical
  snapshot, so there is no commit churn and no self-triggering loop.

## Where the ping actually comes from

GitHub suppresses notifications for events created with the workflow's own
`GITHUB_TOKEN` when you triggered that run, so issues opened by the job can be
silently ignored — no email, no notification. That is why the default channel
here is **ntfy**, not GitHub issues.

`config.json` carries a `webhook` key:

```json
"webhook": "https://ntfy.sh/modelwatch-mbowyer-2191230209"
```

Anyone who knows that topic name can read the alerts, so either keep the repo
private or move the URL to the `ALERT_WEBHOOK_URL` secret (secrets win over
config). Subscribe to the topic in the ntfy app or at
`https://ntfy.sh/modelwatch-mbowyer-2191230209` and alerts arrive on your phone.

GitHub issues are still written as a second channel and as the audit trail.

## Run locally

```
python3 watch.py            # real run, rewrites state.json
python3 watch.py --dry-run  # print alerts, touch nothing
```

Windows: `watch.py` runs as-is with `python watch.py` in that folder.
