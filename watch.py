#!/usr/bin/env python3
"""Watch free LLM model catalogs and data-retention wording, and explain what changed.

Sources (all public, no keys):
  - models.dev/api.json            -> per-provider model list + pricing
  - openrouter.ai/api/v1/models    -> OpenRouter pricing (only when "openrouter" is configured)
  - opencode.ai console/models doc -> the prose that states zero-retention / 30-day /
                                      "improve the model"

Runs on a GitHub Actions cron; state.json in the repo is the diff baseline.
The alert the owner receives is the rendered summary, never a list of event codes.

Two rules keep this honest:
  - nothing is claimed that the run did not fetch; a value the source does not
    state is reported as "unstated";
  - a source that could not be fetched never produces events, so a dead page
    cannot be read as "every model disappeared".
"""
import hashlib
import html
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request

MODELS_DEV = "https://models.dev/api.json"
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
OPENCODE_DOCS = ("https://opencode.ai/v2/docs/console/models/", "https://opencode.ai/docs/zen/")
OPENROUTER_PRIVACY_DOCS = "https://openrouter.ai/docs/guides/privacy/provider-logging"
UA = {"User-Agent": "model-watch/2.0 (+https://github.com)"}

STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
ISSUE_PREFIX = "[model-watch]"
WRAP = 88

PROVIDER_LABEL = {"opencode": "OpenCode", "openrouter": "OpenRouter"}

# Why an OpenRouter zero price is not enough on its own. Stated here so the
# exclusion is documented in the code, in the tests and to the owner.
OPENROUTER_EXCLUSIONS = (
    "OpenRouter rows count as free chat models only when all three hold: the id "
    "is not an openrouter/* router alias (a router is not a model), the model's "
    "output modalities are exactly [\"text\"] (audio-output models are not chat "
    "models), and the id is not a stealth/* promo slot. A price of \"0\" on its "
    "own is never enough."
)

# Severity of a retention class, least private last. "unstated" is unknown
# rather than worse, so a newly documented model with no statement is silent.
CLASS_SEVERITY = {"zero-retention": 0, "retained": 1, "logged": 2, "trains-or-logs": 3}


# ---------------------------------------------------------------- fetching

FETCHED = []


def fetch(url, timeout=60):
    """Fetch one URL, recording it in fetch order (AC-31 audit)."""
    FETCHED.append(url)
    log_path = os.environ.get("MODEL_WATCH_FETCH_LOG")
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(url + "\n")
        except OSError:
            pass
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


def normalise(model_id):
    """Identity key for a model id: case-insensitive, '.', '-' and '_' equivalent.

    This is what makes a config watch entry (`opencode/glm-5.3-flash-free`), a
    state free id (`glm-5.3-flash-free`) and a state policy key
    (`glm-5-3-flash-free`) the same model (AC-41). A literal string compare
    silently files two of the owner's three watched models under the wrong group.
    """
    value = str(model_id).strip().lower()
    if "/" in value:
        value = value.split("/", 1)[1]
    return re.sub(r"[._\-]+", "-", value)


def provider_of(entry):
    return entry.split("/", 1)[0] if "/" in entry else ""


def model_of(entry):
    return entry.split("/", 1)[1] if "/" in entry else entry


# ---------------------------------------------------------------- sources

def _as_price(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_zero(value):
    return value is not None and value == 0


def catalog(models_dev, provider_ids):
    """provider -> {model_id: {name, input, output, released, modalities}}.

    Prices are normalised to USD per 1M tokens, which is models.dev's own unit.
    """
    out = {}
    for pid in provider_ids:
        prov = models_dev.get(pid) or {}
        models = {}
        for mid, m in (prov.get("models") or {}).items():
            cost = m.get("cost") or {}
            models[mid] = {
                "name": m.get("name") or mid,
                "input": _as_price(cost.get("input")),
                "output": _as_price(cost.get("output")),
                "released": m.get("release_date"),
                "modalities": (m.get("architecture") or {}).get("output_modalities"),
            }
        out[pid] = models
    return out


def openrouter_models():
    """OpenRouter's own catalog, normalised to the same price unit.

    OpenRouter quotes per token, so multiply by 1e6 to compare with models.dev.
    """
    data = json.loads(fetch(OPENROUTER_MODELS)).get("data") or []
    out = {}
    for m in data:
        pricing = m.get("pricing") or {}
        prompt, completion = _as_price(pricing.get("prompt")), _as_price(pricing.get("completion"))
        out[m["id"]] = {
            "name": m.get("name") or m["id"],
            "input": None if prompt is None else prompt * 1_000_000,
            "output": None if completion is None else completion * 1_000_000,
            "released": None,
            "modalities": (m.get("architecture") or {}).get("output_modalities"),
        }
    return out


def is_free(pid, mid, model):
    """Is this row a free model the owner could actually use?

    OpenRouter needs the extra exclusions (AC-11); a "0" price string alone is
    never enough. OpenCode rows come from models.dev, where 0/0 means free.
    """
    if pid == "openrouter":
        if mid.startswith("openrouter/"):
            return False
        if mid.startswith("stealth/"):
            return False
        if model.get("modalities") != ["text"]:
            return False
    return _is_zero(model.get("input")) and _is_zero(model.get("output"))


def free_ids(pid, models):
    return sorted(mid for mid, m in models.items() if is_free(pid, mid, m))


# ---------------------------------------------------------------- policy prose

# A line is a policy entry only when it STRUCTURALLY names a model and asserts a
# retention or free-model statement (AC-13). This is a shape rule, not a
# blacklist of today's junk strings: it rejects an empty fragment, a bare
# cross-reference, a heading, an instruction to the reader and an env var,
# because none of them names a model and asserts a statement.
ASSERTION_MARKERS = (
    "free on opencode",
    "available on opencode",
    "zero-retention",
    "zero retention",
    "retains it for",
    "retains them for",
    "does not use your data",
    "for model training",
    "collect feedback",
    "improve the model",
    "logged",
)

# Words that open an instruction or a fragment, never a model name.
NON_NAME_WORDS = frozenset(
    """
    a an the this that these those it its to see set contact if use run note read
    and or for of in on at by with from you your we our they their please free
    models model more less here there below above next previous
    """.split()
)

ENTRY_VERBS = r"\b(?:is|are|has|was|were|now)\b"


def looks_like_model_name(phrase):
    phrase = phrase.strip().strip(".,;:")
    if not phrase or len(phrase) > 60:
        return False
    if not re.match(r"^[A-Za-z0-9]", phrase):
        return False
    if "=" in phrase or "_" in phrase:
        return False
    tokens = phrase.split()
    if not tokens or len(tokens) > 6:
        return False
    if tokens[0].lower() in NON_NAME_WORDS:
        return False
    # A model name carries a capital, a digit or a dotted version.
    return any(re.search(r"[A-Z0-9.]", t) for t in tokens)


def is_policy_entry(line):
    line = " ".join(line.split())
    if len(line) < 30:
        return False
    low = line.lower()
    if not any(marker in low for marker in ASSERTION_MARKERS):
        return False
    head = re.split(ENTRY_VERBS, line, maxsplit=1)[0]
    return looks_like_model_name(head)


def extract_policy(lines):
    """slug -> prose, for every line that is a real model statement."""
    policies = {}
    for line in lines:
        if not is_policy_entry(line):
            continue
        head = re.split(ENTRY_VERBS, line, maxsplit=1)[0].strip()
        key = slug(head)
        if key:
            policies[key] = " ".join(line.split())
    return policies


def opencode_policy_text():
    """Find the page and the prose block that actually states model retention."""
    for url in OPENCODE_DOCS:
        try:
            raw = fetch(url)
        except Exception:
            continue
        lines = [l.strip() for l in page_text(raw).split("\n")]
        starts = [i for i, l in enumerate(lines) if l == "Free models"]
        if not starts:
            continue
        stop_words = ("Contact us", "Auto-reload", "Auto-recharge", "Monthly limits", "Deprecated models")
        best = {}
        for start in starts:
            body = []
            for line in lines[start + 1:]:
                if not line:
                    continue
                if line in stop_words:
                    break
                body.append(line)
            found = extract_policy(body)
            if len(found) > len(best):
                best = found
        if best:
            return url, best
    return None, {}


# A negation cue immediately before a clause reverses it ("does not use your
# data for model training"). Scoped to a window because one sentence can hold a
# negated training clause AND a retention window that is not negated.
_NEGATION = re.compile(
    r"(?:does|do|did|will|would|shall|can|could|may)\s+not\b|\bnot\b|\bnever\b|\bno\b|"
    r"\bwithout\b|\bcannot\b")

# Training / model-improvement clauses. A provider that trains on what you send
# has a worse privacy posture than one that keeps a copy, so this is evaluated
# BEFORE the zero-retention phrase: "follows a zero-retention policy and may use
# your data for model training" is a training statement, not a clean one.
TRAIN_PATTERNS = (
    r"\b(?:may|might|will|would|can|could|also)\s+(?:also\s+)?(?:use|uses)\s+(?:your|the|all|this)\s+"
    r"(?:data|inputs?|prompts?|content)\s+(?:to\s+)?(?:train|improve)",
    r"\b(?:use|uses|used)\s+(?:your|the|all|this)\s+(?:data|inputs?|prompts?|content)\s+"
    r"(?:to\s+train|for\s+(?:model\s+)?training|to\s+improve)",
    r"\b(?:may|might|will|would|can|could)\s+(?:also\s+)?(?:train|learn)\s+(?:on|from|using)\s+"
    r"(?:your|the)\s+(?:data|inputs?|prompts?|content)",
    r"\b(?:trains?|trained|training)\s+(?:on|from|using)\s+(?:your|the)\s+(?:data|inputs?|prompts?|content)",
    r"\bfor\s+(?:model\s+)?training\b",
    r"\bto\s+(?:train|improve)\s+(?:the|its|our|their)\s+(?:model|models|products?|service)",
    r"\b(?:train|improve)\s+(?:the|its|our|their)\s+(?:model|models|products?|service)\b",
    r"\bcollect(?:s|ing)?\s+feedback\b",
)

# Retention windows, in days. Only days are converted; a statement in hours or
# months is quoted as-is rather than guessed at.
_RETAIN_VERB = (r"(?:keeps?|kept|stores?|stored|holds?|held|retains?|retained|"
                r"are\s+kept|is\s+kept|are\s+stored|is\s+stored|are\s+retained|is\s+retained|"
                r"logs?|logged|archives?|archived)")
RETAIN_PATTERNS = (
    re.compile(_RETAIN_VERB + r"[^.;]{0,48}?\bfor\s+(\d+)\s+days?\b"),
    re.compile(r"\b(\d+)[-\s]days?\s+(?:retention|storage|log)\b"),
    re.compile(r"\bdeletes?[^.;]{0,48}?\bafter\s+(\d+)\s+days?\b"),
    re.compile(_RETAIN_VERB + r"[^.;]{0,48}?\bwithin\s+(\d+)\s+days?\b"),
)


def _negated(low, start):
    """True when a negation cue governs this position.

    The window stops at the previous clause boundary, so "does not use your data
    for model training and keeps your prompts for 30 days" has its training
    clause negated without also suppressing the retention window that follows.
    """
    window = low[:start]
    for boundary in (". ", "; ", ", ", " and ", " but "):
        cut = window.rfind(boundary)
        if cut != -1:
            window = window[cut + len(boundary):]
    return bool(_NEGATION.search(window))


def _first_unanchored(patterns, low):
    """First pattern match that is not negated by the words before it."""
    for pattern in patterns:
        for m in re.finditer(pattern, low):
            if not _negated(low, m.start()):
                return m
    return None


def retention_window_days(low):
    """The stated retention window in days, or None."""
    matches = []
    for pattern in RETAIN_PATTERNS:
        for m in re.finditer(pattern, low):
            if not _negated(low, m.start()):
                try:
                    matches.append(int(m.group(1)))
                except (TypeError, ValueError):
                    continue
    return max(matches) if matches else None


def classify(prose):
    """The data-protection class of one model's statement.

    Order is deliberate: a training clause is checked FIRST, so a statement that
    both claims zero retention and reserves the right to train is reported as
    trains-or-logs rather than as the better class.
    """
    low = " ".join((prose or "").lower().split())
    if _first_unanchored(TRAIN_PATTERNS, low):
        return "trains-or-logs"
    if re.search(r"zero[-\s]retention", low):
        return "zero-retention"
    days = retention_window_days(low)
    if days is not None:
        return "retained-%sd" % days
    if re.search(r"\blogged\b|\blogs\b|\blogging\b", low):
        return "logged"
    return "unstated"


def class_clause(cls, tense="present"):
    """The class as a clause the owner can read, in the tense that fits."""
    past = tense == "past"
    if cls == "zero-retention":
        return ("kept no copy of your data (zero retention)" if past
                else "keeps no copy of your data (zero retention)")
    m = re.match(r"retained-(\d+)d$", cls or "")
    if m:
        return ("kept your data for %s days" if past
                else "keeps your data for %s days") % m.group(1)
    if cls == "trains-or-logs":
        return ("may have used your data to train or improve the model" if past
                else "may use your data to train or improve the model")
    if cls == "logged":
        return "logged your requests" if past else "logs your requests"
    return ("did not say what it does with your data" if past
            else "does not say what it does with your data")


def severity(cls):
    if cls in CLASS_SEVERITY:
        return CLASS_SEVERITY[cls]
    if re.match(r"retained-(\d+)d$", cls or ""):
        return CLASS_SEVERITY["retained"]
    return None


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def changed_sentence(before, after):
    """The sentence in `after` that `before` does not contain.

    A wording change is often an ADDED clause ("... It may disclose it to law
    enforcement on request."). Naming the class-carrying sentence there would
    point the owner at the one line that did not change, so a changed sentence
    that makes a privacy promise wins.
    """
    old = {" ".join(s.lower().split()) for s in _sentences(before)}
    new = [s for s in _sentences(after) if " ".join(s.lower().split()) not in old]
    for sentence in new:
        if PRIVACY_TERMS.search(sentence.lower()):
            return sentence
    if new:
        return new[0]
    # A clause rewritten in place leaves no new sentence behind; the sentence
    # that differs from its counterpart is the one that changed.
    was = _sentences(before)
    for index, sentence in enumerate(_sentences(after)):
        if index >= len(was) or " ".join(sentence.lower().split()) != " ".join(was[index].lower().split()):
            return sentence
    return ""


def carrier_sentence(text, cls):
    """The one sentence in `text` that carries the retention statement."""
    sentences = _sentences(text)
    if not sentences:
        return ""
    if cls == "zero-retention":
        keys = ("zero-retention", "zero retention")
    elif cls and cls.startswith("retained-"):
        keys = ("retains", "retained", " days")
    elif cls == "trains-or-logs":
        keys = ("improve the model", "collect feedback", "train")
    elif cls == "logged":
        keys = ("logged",)
    else:
        keys = ()
    for sentence in sentences:
        low = sentence.lower()
        if any(k in low for k in keys):
            return sentence
    return sentences[0]


def policy_name(text, fallback):
    """The model's own name, as the fetched prose states it."""
    head = re.split(ENTRY_VERBS, " ".join((text or "").split()), maxsplit=1)[0].strip()
    return head.strip(".,;:") or fallback


# Words that make a sentence part of the privacy promise. Kept as a named
# constant so the same test that forbids marketing churn can assert against it.
PRIVACY_TERMS = re.compile(
    r"zero[-\s]retention|retains?|retained|keeps?|keep|stored?|store|holds?|held|"
    r"logs?|logged|archives?|\bdays?\b|train|training|improve the model|"
    r"collect feedback|privacy|your data|your prompts|your inputs?|"
    r"disclos\w*|shar\w*|third[\s-]part\w*|law enforcement|subpoena|"
    r"sell|sold|transfer\w*|legal (?:request|process)|aggregate")


def retention_sentences(text):
    """The sentences in `text` that carry a retention/privacy statement.

    Diffing the whole line would fire on a reworded marketing clause ("free for
    a limited time" -> "free for a short time"), which is noise. Diffing only
    these sentences catches a reworded privacy promise at ANY class - including
    a new disclosure or sharing clause that leaves the class alone - which is
    the whole point of the tool, and stays silent on everything else.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    kept = []
    for sentence in sentences:
        if PRIVACY_TERMS.search(sentence.lower()):
            kept.append(" ".join(sentence.lower().split()))
    return kept


def policy_block_digest(policies):
    """Digest of the retention-relevant prose only, never the whole page.

    A digest restricted to the section that states retention cannot move when a
    navigation or marketing edit lands, which is what made DOC CHANGED noise
    (AC-19). It is recorded as a secondary signal; no event derives from it.
    """
    joined = "\n".join("%s: %s" % (k, policies[k]) for k in sorted(policies))
    return hashlib.sha256(re.sub(r"\s+", " ", joined).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- collection

def collect(config, previous=None):
    """Build the snapshot for this run. Returns (snap, catalog, meta)."""
    previous = previous or {}
    providers = [p for p in (config.get("providers") or []) if p]
    meta = {"catalog_ok": False, "policy_ok": False, "providers_ok": {}}

    try:
        models_dev = json.loads(fetch(MODELS_DEV))
        meta["catalog_ok"] = True
    except Exception:
        models_dev = {}

    cat = catalog(models_dev, [p for p in providers if p != "openrouter"]) if models_dev else {}
    for pid in providers:
        cat.setdefault(pid, {})
        meta["providers_ok"][pid] = bool(cat.get(pid)) if pid != "openrouter" else False

    if "openrouter" in providers:
        try:
            cat["openrouter"] = openrouter_models()
            meta["providers_ok"]["openrouter"] = True
        except Exception:
            cat["openrouter"] = {}
            meta["providers_ok"]["openrouter"] = False

    snap = {"free": {}, "policy": {}, "docs": {}}
    for pid in providers:
        snap["free"][pid] = free_ids(pid, cat.get(pid) or {})
        snap["policy"][pid] = {}

    if "opencode" in providers:
        doc_url, prose = opencode_policy_text()
        if prose:
            meta["policy_ok"] = True
            snap["policy"]["opencode"] = {
                key: {"text": text, "class": classify(text)} for key, text in prose.items()
            }
            snap["docs"][doc_url] = policy_block_digest(prose)

    # OpenRouter's privacy page is only ever read when the owner asked for
    # OpenRouter. This is the write that used to run unconditionally (AC-09).
    if "openrouter" in providers:
        try:
            page = page_text(fetch(OPENROUTER_PRIVACY_DOCS))
            snap["docs"][OPENROUTER_PRIVACY_DOCS] = hashlib.sha256(
                re.sub(r"\s+", " ", page).encode("utf-8")
            ).hexdigest()[:16]
        except Exception:
            pass

    # A source that failed must not look like "everything disappeared": keep the
    # previous baseline for it instead of writing an empty one (INV-10).
    if not meta["catalog_ok"] and previous.get("free"):
        snap["free"] = previous["free"]
    else:
        for pid in providers:
            if not meta["providers_ok"].get(pid) and (previous.get("free") or {}).get(pid):
                snap["free"][pid] = previous["free"][pid]
    if not meta["policy_ok"] and previous.get("policy"):
        snap["policy"] = previous["policy"]
        snap["docs"] = previous.get("docs") or {}

    return snap, cat, meta


# ---------------------------------------------------------------- diffing

def watch_entries(config):
    return [w for w in (config.get("watch") or []) if provider_of(w) and model_of(w)]


def watched_entry(config, pid, mid):
    """The owner's own watch-list spelling for this model, or None."""
    key = normalise(mid)
    for entry in watch_entries(config):
        if provider_of(entry) == pid and normalise(model_of(entry)) == key:
            return entry
    return None


def _price(cat, pid, mid):
    models = cat.get(pid) or {}
    model = models.get(mid)
    if not model:
        for key, value in models.items():
            if normalise(key) == normalise(mid):
                model = value
                break
    if not model:
        return None
    return (model.get("input"), model.get("output"))


def _display_name(config, pid, mid, prose):
    """The owner's watch-list spelling if watched, else the name the page states."""
    entry = watched_entry(config, pid, mid)
    if entry:
        return model_of(entry)
    return policy_name(prose, mid)


def _free_event(kind, pid, mid, cat, config, price_before, price_after):
    entry = watched_entry(config, pid, mid)
    return {
        "kind": kind, "provider": pid, "model": mid,
        "watched": bool(entry),
        "display": model_of(entry) if entry else mid,
        "price_before": price_before, "price_after": price_after,
    }


def diff(old, new, cat, config, meta=None):
    """Turn two snapshots into structured events. Never fabricates one."""
    meta = meta or {}
    old = old or {}
    events = []
    provider_ok = meta.get("providers_ok") or {}

    if meta.get("catalog_ok", True):
        for pid, free in (new.get("free") or {}).items():
            if provider_ok.get(pid) is False:
                continue
            before_all = old.get("free") or {}
            if pid not in before_all:
                # No baseline for this provider yet: record it silently rather
                # than announce every existing model as new.
                continue
            # Compared on normalised identity, exactly like watched_entry():
            # a punctuation re-spelling of an unchanged id is the same model,
            # so it must not read as both a new free model and a lost one.
            before_by_key, now_by_key = {}, {}
            for mid in before_all.get(pid) or []:
                before_by_key.setdefault(normalise(mid), mid)
            for mid in free or []:
                now_by_key.setdefault(normalise(mid), mid)
            for key in sorted(set(now_by_key) - set(before_by_key)):
                mid = now_by_key[key]
                events.append(_free_event("free_added", pid, mid, cat, config, None, _price(cat, pid, mid)))
            for key in sorted(set(before_by_key) - set(now_by_key)):
                mid = before_by_key[key]
                events.append(_free_event("free_removed", pid, mid, cat, config, (0.0, 0.0), _price(cat, pid, mid)))

    if meta.get("policy_ok", True):
        for pid, models in (new.get("policy") or {}).items():
            before = (old.get("policy") or {}).get(pid) or {}
            if not before:
                continue
            for key in sorted(set(models) & set(before)):
                was, now = before.get(key) or {}, models.get(key) or {}
                class_changed = was.get("class") != now.get("class")
                # A retention promise can be reworded without changing its
                # class ("retains it for 30 days" -> "keeps your prompts for 30
                # days"), and that is exactly the edit the tool exists to catch.
                # Only the retention-bearing sentences are compared, so a
                # reworded marketing clause stays silent (INV-18).
                wording_changed = (retention_sentences(was.get("text"))
                                   != retention_sentences(now.get("text")))
                if not class_changed and not wording_changed:
                    continue
                events.append({
                    "kind": "policy_changed", "provider": pid, "model": key,
                    "watched": bool(watched_entry(config, pid, key)),
                    "display": _display_name(config, pid, key, now.get("text")),
                    "class_before": was.get("class"), "class_after": now.get("class"),
                    "text_before": was.get("text") or "", "text_after": now.get("text") or "",
                    "class_changed": class_changed, "wording_changed": wording_changed,
                })
            for key in sorted(set(models) - set(before)):
                now = models.get(key) or {}
                rank = severity(now.get("class"))
                if rank is None or rank <= CLASS_SEVERITY["zero-retention"]:
                    continue
                events.append({
                    "kind": "policy_new_bad", "provider": pid, "model": key,
                    "watched": bool(watched_entry(config, pid, key)),
                    "display": _display_name(config, pid, key, now.get("text")),
                    "class_before": None, "class_after": now.get("class"),
                    "text_before": "", "text_after": now.get("text") or "",
                })
            for key in sorted(set(before) - set(models)):
                still_free = any(
                    normalise(fid) == normalise(key) for fid in (new.get("free") or {}).get(pid) or []
                )
                if not still_free:
                    continue
                was = before.get(key) or {}
                events.append({
                    "kind": "policy_removed", "provider": pid, "model": key,
                    "watched": bool(watched_entry(config, pid, key)),
                    "display": _display_name(config, pid, key, was.get("text")),
                    "class_before": was.get("class"), "class_after": None,
                    "text_before": was.get("text") or "", "text_after": "",
                })

    if meta.get("catalog_ok", True):
        for entry in watch_entries(config):
            pid, mid = provider_of(entry), model_of(entry)
            if pid not in cat:
                continue
            present = any(normalise(m) == normalise(mid) for m in (cat.get(pid) or {}))
            was_free = any(normalise(f) == normalise(mid) for f in (old.get("free") or {}).get(pid) or [])
            free_now = any(normalise(f) == normalise(mid) for f in (new.get("free") or {}).get(pid) or [])
            # An empty provider catalog cannot distinguish "delisted" from
            # "the catalog did not load", so it is never read as a delisting.
            if not present and was_free and (cat.get(pid) or {}):
                # One disappearance is ONE item. The free-set diff already saw
                # this model leave the free list; keep the delisting (which is
                # the truthful cause) and drop the duplicate free_removed.
                key = normalise(mid)
                events = [e for e in events if not (
                    e["kind"] == "free_removed" and e["provider"] == pid
                    and normalise(e["model"]) == key)]
                events.append(_free_event("watched_delisted", pid, mid, cat, config, None, None))
            elif present and was_free and not free_now:
                already = any(
                    e["kind"] == "free_removed" and e["provider"] == pid
                    and normalise(e["model"]) == normalise(mid) for e in events
                )
                if not already:
                    events.append(
                        _free_event("watched_not_free", pid, mid, cat, config, (0.0, 0.0), _price(cat, pid, mid))
                    )

    return events


# ---------------------------------------------------------------- rendering

def fmt_price(value):
    if value is None:
        return "unstated"
    if value == 0:
        return "$0"
    if value == int(value):
        return "$%d/M" % int(value)
    return "$%.2f/M" % value


def provider_label(pid):
    return PROVIDER_LABEL.get(pid, pid)


def wrap(text, width=WRAP):
    """Wrap prose for a phone. Never splits a hyphenated word like zero-retention."""
    return textwrap.fill(" ".join(str(text).split()), width=width, break_on_hyphens=False)


def is_retention(event):
    return event["kind"] in ("policy_changed", "policy_new_bad", "policy_removed")


def is_free_event(event):
    return event["kind"] in ("free_added", "free_removed", "watched_delisted", "watched_not_free")


def _headline(events):
    paid = [e for e in events if e["watched"] and e["kind"] in
            ("free_removed", "watched_delisted", "watched_not_free")]
    retention = [e for e in events if e["watched"] and is_retention(e)]
    watched_free = [e for e in events if e["watched"] and e["kind"] == "free_added"]
    other_retention = [e for e in events if not e["watched"] and is_retention(e)]
    other_free = [e for e in events if not e["watched"] and is_free_event(e)]

    if paid:
        names = ", ".join(e.get("display") or e["model"] for e in paid)
        if len(paid) == 1:
            if all(e["kind"] == "watched_delisted" for e in paid):
                return "One model you watch is no longer listed by its provider: %s." % names
            return "One model you watch is no longer free: %s." % names
        if all(e["kind"] == "watched_delisted" for e in paid):
            return "%d models you watch are no longer listed by their providers: %s." % (len(paid), names)
        if all(e["kind"] != "watched_delisted" for e in paid):
            return "%d models you watch are no longer free: %s." % (len(paid), names)
        return "%d models you watch are no longer free or no longer listed: %s." % (len(paid), names)
    if retention:
        names = ", ".join(e.get("display") or e["model"] for e in retention)
        if len(retention) == 1:
            return "A model you watch changed its data-retention wording: %s." % names
        return "%d models you watch changed their data-retention wording: %s." % (len(retention), names)
    if watched_free:
        names = ", ".join(e.get("display") or e["model"] for e in watched_free)
        if len(watched_free) == 1:
            return "One model you watch is free again: %s." % names
        return "%d models you watch are free again: %s." % (len(watched_free), names)
    if other_retention:
        return ("A provider changed its data-retention wording - this is the kind of "
                "change this tool exists to catch.")
    if other_free:
        if len(other_free) == 1:
            return "One free model changed, and it is not on your watch list."
        return "%d free models changed, none of them on your watch list." % len(other_free)
    return "Nothing changed since the last check."


def _retention_item(event):
    pid = event["provider"]
    name = event.get("display") or event["model"]
    lines = []
    if event["kind"] == "policy_changed":
        lines.append("%s (%s) changed what its provider says about your data."
                     % (name, provider_label(pid)))
        if event.get("class_changed", True):
            lines.append("Before, it %s." % class_clause(event.get("class_before"), "past"))
            lines.append("Now it %s." % class_clause(event.get("class_after")))
        else:
            # A reworded promise at the same class is a wording change, not a
            # downgrade: saying "still keeps your data for 30 days" and quoting
            # both sentences is the truthful report.
            lines.append("What it covers has not changed - it still %s - but the "
                         "wording has." % class_clause(event.get("class_after")))
        lines.append("Before: \"%s\"" % wrap(event.get("text_before") or "unstated"))
        lines.append("Now: \"%s\"" % wrap(event.get("text_after") or "unstated"))
        carrier = carrier_sentence(event.get("text_after"), event.get("class_after"))
        if not event.get("class_changed", True):
            # A wording-only change is reported by the sentence that actually
            # changed; the class-carrying sentence may be the one left intact.
            carrier = changed_sentence(event.get("text_before"),
                                       event.get("text_after")) or carrier
        if carrier:
            lines.append(wrap("The sentence carrying the change: \"%s\"" % carrier))
    elif event["kind"] == "policy_new_bad":
        lines.append("%s (%s) is newly documented, and its provider %s."
                     % (name, provider_label(pid), class_clause(event.get("class_after"))))
        lines.append("It says: \"%s\"" % wrap(event.get("text_after") or "unstated"))
    else:
        lines.append("%s (%s) is no longer described on the provider's data-retention page."
                     % (name, provider_label(pid)))
        lines.append("It is still free, so the retention promise you could rely on is no longer stated.")
        if event.get("text_before"):
            lines.append("It used to say: \"%s\"" % wrap(event["text_before"]))
    lines.append("Worth rereading the provider's data page before you send it anything private.")
    return lines


def _free_item(event):
    pid = event["provider"]
    name = event.get("display") or event["model"]
    label = provider_label(pid)
    after = event.get("price_after") or (None, None)
    lines = []
    if event["kind"] == "free_added":
        if event["watched"]:
            lines.append("%s is a model you watch, and it is free again on %s." % (name, label))
            lines.append("It is free to use right now.")
            lines.append("Nothing to do unless you had stopped using it because it cost money.")
        else:
            lines.append("%s is a new free model on %s." % (name, label))
            lines.append("It is free to use right now.")
            lines.append("Nothing to do unless you want to try it.")
    elif event["kind"] in ("free_removed", "watched_not_free"):
        lines.append("%s is no longer free." % name)
        if event.get("price_before"):
            lines.append("It was $0 in and $0 out at the last check; it now costs %s in, %s out."
                         % (fmt_price(after[0]), fmt_price(after[1])))
        else:
            lines.append("It now costs %s in, %s out." % (fmt_price(after[0]), fmt_price(after[1])))
        if event["watched"]:
            lines.append("You have it on your watch list, so this is likely to affect your setup.")
        else:
            lines.append("It is not on your watch list, so this probably does not affect your setup.")
    else:
        lines.append("%s is no longer listed by %s." % (name, label))
        lines.append("You have it on your watch list, so this is likely to affect your setup.")
    return lines


def render(events, config=None):
    """The text the owner receives. The summary IS the alert."""
    config = config or {}
    events = events or []
    watched = [e for e in events if e["watched"]]
    retention_other = [e for e in events if not e["watched"] and is_retention(e)]
    free_other = [e for e in events if not e["watched"] and is_free_event(e)]

    lines = [_headline(events), ""]

    lines.append("Models you watch (%d)" % len(watched))
    if watched:
        for event in watched:
            lines.extend(_retention_item(event) if is_retention(event) else _free_item(event))
            lines.append("")
    else:
        lines.append("Nothing here - none of the models you watch changed.")
        lines.append("")

    lines.append("Data retention and privacy wording (%d)" % len(retention_other))
    if retention_other:
        for event in retention_other:
            lines.extend(_retention_item(event))
            lines.append("")
    else:
        watched_retention = [e for e in watched if is_retention(e)]
        if watched_retention:
            n = len(watched_retention)
            lines.append("Nothing here - the %d retention change%s on a model you watch %s listed above."
                         % (n, "" if n == 1 else "s", "is" if n == 1 else "are"))
        else:
            lines.append("Nothing here - no provider changed what it says about your data.")
        lines.append("")

    lines.append("Other free models (%d)" % len(free_other))
    if free_other:
        for event in free_other:
            lines.extend(_free_item(event))
            lines.append("")
    else:
        lines.append("Nothing here - no other free model appeared or disappeared.")
        lines.append("")

    text = "\n".join(lines).rstrip("\n")
    # AC-11: when OpenRouter is configured the alert SAYS what it excludes, so
    # the owner can see why a "$0" row was not reported as free. A quiet run
    # says nothing at all, so it is never padded with this.
    if events and "openrouter" in (config.get("providers") or []):
        text += "\n\nAbout OpenRouter: %s" % wrap(OPENROUTER_EXCLUSIONS)
    mention = (config.get("mention") or "").strip()
    if events and mention:
        text += "\n\n@%s" % mention
    return text


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

def load_config(path=CONFIG_FILE):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_state(path=STATE_FILE):
    """A missing, empty or corrupt baseline is an empty baseline, not a crash."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(snap, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
        fh.write("\n")


def run(config, old):
    """One full cycle: collect, diff, render. No side effects on disk."""
    snap, cat, meta = collect(config, old)
    return snap, cat, meta, diff(old, snap, cat, config, meta)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    no_notify = "--no-notify" in args

    config = load_config()
    old = load_state()
    snap, cat, meta, events = run(config, old)
    summary = render(events, config)
    print(summary)

    if dry_run:
        print("")
        print("[dry-run] URLs fetched:")
        for url in FETCHED:
            print(url)
        print("[dry-run] not notifying, not writing state")
        return 0

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    if events and not no_notify:
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if token and repo:
            print("issue:", notify_issue(summary, token, repo))
        hook = os.environ.get("ALERT_WEBHOOK_URL") or config.get("webhook")
        if hook:
            try:
                print(notify_webhook(summary, hook))
            except Exception as exc:
                print("webhook failed: %s" % exc)

    write_state(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
