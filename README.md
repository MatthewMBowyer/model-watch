# model-watch

Tells you, on your phone, when a free model you use stops being free, or when a
provider quietly changes what it says about your data.

Runs on GitHub Actions every 6 hours. No API keys, no dependencies, no server,
no paid service.

**If you want to know what this tool does and what to do when an alert arrives,
read [OWNER_SUMMARY.md](OWNER_SUMMARY.md).** That page is written for you. The
rest of this file is setup notes for whoever maintains the repository.

## What it watches

By default it watches **OpenCode only**:

| Source | Used for |
|---|---|
| `models.dev/api.json` | model catalog and pricing |
| `opencode.ai/v2/docs/console/models/` | the prose that states zero-retention / retained-N-days / trains-on-your-data |

`config.json` `providers` is the single authority. OpenRouter is supported for a
config that lists it, but it is not watched by default: its catalog is full of
router aliases (`openrouter/*`), audio-output models and `stealth/*` promo slots
that are not chat models, and alerting on them produced unreadable noise. When
`openrouter` is listed, those three classes are excluded, the exclusion is
stated in `watch.py` as `OPENROUTER_EXCLUSIONS`, and the rendered alert carries
it as a trailing "About OpenRouter: ..." paragraph so the owner can see why a
`$0` row was not reported as free.

The retention signal only exists as prose, so each free-model sentence is
classified into `zero-retention`, `retained-<N><unit>` (unit one of `d`, `h`, `w`,
`mo`), `trains-or-logs`, `logged` or `unstated`, and that classification is diffed
on the privacy sentences as well as on the class.

## What an alert contains

One headline sentence, then three sections in a fixed order - models you watch,
data retention and privacy wording, other free models - each with a count and an
explicit "Nothing here" line when empty. Every item is prose: what the model is,
what changed, what to do. A retention change quotes the before and after
sentence rather than a hash.

The rendered summary *is* the notification: `notify_issue`, `notify_webhook` and
`GITHUB_STEP_SUMMARY` all receive the same text.

A run with no changes writes the summary to the Actions step summary and sends
nothing. There are no "no changes" comments.

## Setup (5 minutes)

1. Create an empty GitHub repo and push these files:

```
watch.py
config.json
state.json            # first baseline, already generated
OWNER_SUMMARY.md
tests/test_model_watch.py
.github/workflows/model-watch.yml
```

2. Edit `config.json`:

```json
{
  "providers": ["opencode"],
  "watch": ["opencode/kimi-k2.5-free"],
  "mention": "your-github-handle",
  "webhook": "https://ntfy.sh/your-topic"
}
```

3. Repo -> Settings -> Actions -> General -> Workflow permissions ->
   **Read and write permissions**. The workflow commits the new baseline and
   opens issues; without this it fails. On an **empty** new repo the default is
   read-only, so set it via the API:

```
gh api -X PUT repos/<you>/model-watch/actions/permissions/workflow \
  -f default_workflow_permissions=write
```

4. Trigger the first run: push any commit touching `watch.py`, `config.json` or
   the workflow file, or use Actions -> `model-watch` -> **Run workflow**.
   A schedule-only workflow cannot be dispatched until the file has run once.

Alerts land as comments on one rolling issue titled `[model-watch] changes`, and
on the ntfy topic in `webhook`. GitHub suppresses notifications for events
created with the workflow's own `GITHUB_TOKEN`, which is why ntfy is the channel
that actually reaches the phone.

**Anyone who knows the ntfy topic name can read the alerts.** Either keep the
repo private or move the URL into the `ALERT_WEBHOOK_URL` secret, which wins over
`config.json`. Never commit a token.

## Tests

Stdlib only, no network - every fetch is replaced by a fixture table:

```
python -m pytest tests/            # or:
python tests/test_model_watch.py
```

The suite covers the summary shape and group counts, the retention rules (class
change, newly documented worse model, model removed from the docs), junk page
furniture, OpenRouter exclusions, watched-model identity across the real
dot/dash id spellings, a watched model returning to the free list, notification
wiring, quiet-run behaviour, `--dry-run`, dead-upstream handling, rerun
determinism and stdlib-only.

## Running it locally

```
python3 watch.py --dry-run   # print the summary and the URLs read; touch nothing
python3 watch.py             # real run: rewrite state.json and notify
```

`MODEL_WATCH_FETCH_LOG=<path>` appends every URL fetched, one per line, in fetch
order. `--dry-run` also prints that list.

## Commit hygiene

The scheduled baseline commit touches only `state.json`, and the workflow's
`push` path filter (`watch.py`, `config.json`, the workflow file) keeps it from
re-triggering itself. Do not add `state.json` to that filter.

`state.json` is generated, never hand-edited: run `python3 watch.py` to
regenerate it after changing the extractor, or the first post-merge run will
report a backlog of false changes.
