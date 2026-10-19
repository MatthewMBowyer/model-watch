#!/usr/bin/env python3
"""Watch model catalogs for new free models and retention-policy downgrades.

Sources (all public, no keys):
  - models.dev/api.json            -> per-provider model list + pricing
  - openrouter.ai/api/v1/models    -> OpenRouter pricing
  - opencode.ai console/models doc -> the prose that states zero-retention /
                                     30-day retention / "improve the model"

Runs on a GitHub Actions cron; state.json in the repo is the diff baseline.
Alerts go to a GitHub issue in the same repo, optionally a webhook.
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

MODELS_DEV = "https://models.dev/api.json"
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
OPENCODE_DOCS = ("https://opencode.ai/v2/docs/console/models/", "https://opencode.ai/docs/zen/")
OPENROUTER_PRIVACY_DOCS = "https://openrouter.ai/docs/guides/privacy/provider-logging"
UA = {"User-Agent": "model-watch/1.0 (+https://github.com)"}

STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
ISSUE_PREFIX = "[model-watch]"


# ---------------------------------------------------------------- fetching

def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def page_text(page):
    text = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    return html.unescape(text)


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


# ---------------------------------------------------------------- sources

def catalog(models_dev, provider_ids):
    """provider -> {model_id: {name, input, output}} from models.dev."""
    out = {}
    for pid in provider_ids:
        prov = models_dev.get(pid) or {}
        models = {}
        for mid, m in (prov.get("models") or {}).items():
            cost = m.get("cost") or {}
            models[mid] = {
                "name": m.get("name") or mid,
                "input": cost.get("input"),
                "output": cost.get("output"),
                "released": m.get("release_date"),
            }
        out[pid] = models
    return out


def free_from_catalog(models, mode):
    """mode: 'modeldev' | 'openrouter' (pricing comes back as strings there)."""
    free = {}
    for mid, m in models.items():
        i, o = m.get("input"), m.get("output")
        if i is None or o is None:
            continue
        try:
            if float(i) == 0 and float(o) == 0:
                free[mid] = m
        except (TypeError, ValueError):
            continue
    return free


def opencode_policy_text():
    """Prose block under the 'Free models' heading, keyed by model slug."""
    for url in OPENCODE_DOCS:
        try:
            raw = fetch(url)
        except Exception:
            continue
        if "Free models" not in page_text(raw):
            continue
        lines = [l.strip() for l in page_text(raw).split("\n")]
        try:
            start = next(i for i, l in enumerate(lines) if l.strip() == "Free models")
        except StopIteration:
            continue
        stop_words = ("Contact us", "Auto-reload", "Monthly limits", "Deprecated models")
        body = []
        for line in lines[start + 1:]:
            if not line:
                continue
            if line in stop_words:
                break
            body.append(line)
        policies = {}
        for line in body:
            head = re.split(r"\bis\b|\bhas\b|\bnow\b", line, maxsplit=1)[0].strip()
            if head:
                policies[slug(head)] = line
        return url, policies
    return None, {}


def classify(prose):
    low = prose.lower()
    if "zero-retention" in low or "zero retention" in low:
        return "zero-retention"
    m = re.search(r"retains? (?:it|them|data)?\s*for (\d+) days", low)
    if m:
        return "retained-%sd" % m.group(1)
    negated = "does not use your data" in low or "not used for" in low or "not use your data" in low
    if not negated and re.search(r"improve (the model|nvidia products|our products)|collect feedback", low):
        return "trains-or-logs"
    if "logged" in low:
        return "logged"
    return "unstated"


def openrouter_endpoint_policies(model_id):
    """Per-endpoint data policy, when OpenRouter exposes it (currently rare)."""
    try:
        data = json.loads(fetch("https://openrouter.ai/api/v1/models/%s/endpoints" % model_id))
    except Exception:
        return {}
    policies = {}
    for ep in (data.get("data") or {}).get("endpoints") or []:
        policy = ep.get("data_policy")
        if policy:
            policies[ep.get("provider_name") or ep.get("name")] = policy
    return policies


def doc_hash(url):
    try:
        return hashlib.sha256(re.sub(r"\s+", " ", page_text(fetch(url))).encode()).hexdigest()[:16]
    except Exception:
        return None


# ---------------------------------------------------------------- diffing

def collect(config):
    models_dev = json.loads(fetch(MODELS_DEV))
    provider_ids = [p for p in config.get("providers", []) if p != "openrouter"]
    if "openrouter" in config.get("providers", []):
        provider_ids.append("openrouter")

    cat = catalog(models_dev, provider_ids)

    # OpenRouter pricing lives on its own API (models.dev copy can lag).
    if "openrouter" in cat:
        try:
            or_models = json.loads(fetch(OPENROUTER_MODELS)).get("data") or []
        except Exception:
            or_models = []
        for m in or_models:
            pricing = m.get("pricing") or {}
            cat["openrouter"][m["id"]] = {
                "name": m.get("name") or m["id"],
                "input": pricing.get("prompt"),
                "output": pricing.get("completion"),
                "released": None,
            }

    snap = {"free": {}, "policy": {}, "docs": {}}
    for pid, models in cat.items():
        snap["free"][pid] = sorted(free_from_catalog(models, pid))
        snap["policy"][pid] = {}
        for mid in models:
            policies = openrouter_endpoint_policies(mid) if pid == "openrouter" and mid in config.get("watch", []) else {}
            if policies:
                snap["policy"][pid][mid] = policies

    doc_url, prose = opencode_policy_text()
    if doc_url:
        snap["docs"][doc_url] = doc_hash(doc_url)
        snap["policy"]["opencode"] = {s: {"text": t, "class": classify(t)} for s, t in prose.items()}

    snap["docs"][OPENROUTER_PRIVACY_DOCS] = doc_hash(OPENROUTER_PRIVACY_DOCS)
    return snap, cat


def diff(old, new, cat, config):
    events = []
    for pid, free in new["free"].items():
        before = set((old.get("free") or {}).get(pid, []))
        now = set(free)
        for mid in sorted(now - before):
            events.append("NEW FREE  %s/%s" % (pid, mid))
        for mid in sorted(before - now):
            if "%s/%s" % (pid, mid) in config.get("watch", []):
                events.append("NO LONGER FREE  %s/%s  <-- you use this" % (pid, mid))
            else:
                events.append("no longer free  %s/%s" % (pid, mid))

    for pid, models in new["policy"].items():
        before = (old.get("policy") or {}).get(pid) or {}
        for key, val in models.items():
            prev = before.get(key)
            if prev is None:
                continue
            if isinstance(val, dict) and isinstance(prev, dict):
                if prev.get("class") != val.get("class"):
                    events.append(
                        "POLICY CHANGED  %s/%s  %s -> %s" % (pid, key, prev.get("class"), val.get("class"))
                    )
                    events.append("    now says: %s" % val.get("text"))

    for url, digest in new["docs"].items():
        prev = (old.get("docs") or {}).get(url)
        if prev and digest and prev != digest:
            events.append("DOC CHANGED  %s  (privacy/retention wording edited - reread it)" % url)

    for entry in config.get("watch", []):
        if "/" not in entry:
            continue
        pid, mid = entry.split("/", 1)
        if mid not in cat.get(pid, {}):
            if mid in (old.get("free") or {}).get(pid, []):
                events.append("WATCHED MODEL DELISTED  %s/%s  <-- you use this" % (pid, mid))
        elif mid not in (old.get("free") or {}).get(pid, []) and mid not in new["free"].get(pid, []):
            events.append("WATCHED MODEL IS NOT FREE  %s/%s" % (pid, mid))
    return events


# ---------------------------------------------------------------- notify

def gh_api(path, method="GET", payload=None, token=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": UA["User-Agent"],
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}")


def notify_issue(report, token, repo):
    issues = gh_api("/repos/%s/issues?state=open&per_page=100" % repo, token=token)
    target = next((i for i in issues if str(i.get("title", "")).startswith(ISSUE_PREFIX)), None)
    if target:
        gh_api("/repos/%s/issues/%d/comments" % (repo, target["number"]), "POST", {"body": report}, token)
        return "commented on #%d" % target["number"]
    created = gh_api("/repos/%s/issues" % repo, "POST", {"title": ISSUE_PREFIX + " changes", "body": report}, token)
    return "opened #%d" % created["number"]


def notify_webhook(report, url):
    if "ntfy" in url:
        req = urllib.request.Request(url, data=report.encode(), method="POST", headers=UA)
    else:
        req = urllib.request.Request(
            url, data=json.dumps({"content": report, "text": report}).encode(), method="POST",
            headers={**UA, "Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return "webhook %s" % resp.status


# ---------------------------------------------------------------- main

def main():
    dry_run = "--dry-run" in sys.argv
    with open(CONFIG_FILE) as fh:
        config = json.load(fh)
    try:
        with open(STATE_FILE) as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = {}

    snap, cat = collect(config)
    events = diff(old, snap, cat, config)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if events:
        report = ["%s found %d change(s)" % (ISSUE_PREFIX, len(events)), ""]
        report += ["- " + e for e in events if not e.startswith("    ")]
        report += [e for e in events if e.startswith("    ")]
        report += ["", "_generated %s_" % stamp]
        report = "\n".join(report)
        print(report)
        if dry_run:
            print("\n[dry-run] not notifying, not writing state")
            return
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if token and repo:
            print("issue:", notify_issue(report, token, repo))
        hook = os.environ.get("ALERT_WEBHOOK_URL")
        if hook:
            print(notify_webhook(report, hook))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(report + "\n")
    else:
        print("no changes at %s" % stamp)

    if not dry_run:
        with open(STATE_FILE, "w") as fh:
            json.dump(snap, fh, indent=1, sort_keys=True)
            fh.write("\n")


if __name__ == "__main__":
    main()
