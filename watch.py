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


# The provider names as identities, so a section label that IS a provider
# ("OpenCode: Prompts are retained ...") is refused by looks_like_model_name()
# without a hand-written list of heading words. Built from PROVIDER_LABEL, the
# same table the renderer uses, so it cannot drift from the providers shipped.
_PROVIDER_IDENTITIES = frozenset(
    slug(value) for value in PROVIDER_LABEL.values()) | frozenset(
    slug(value) for value in PROVIDER_LABEL)


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
# The assertion is judged by SHAPE, not by a list of today's sentences: a line
# is an entry when it opens with a model name and asserts something ABOUT that
# model - a privacy posture, or that it is free/available. A phrase allowlist
# dropped a real entry whose free clause was merely reworded ("is free on
# OpenCode for a limited time" -> "Free to use on OpenCode for a limited time")
# and every entry whose prose opened without a copula, which turned a rewording
# into a false "no longer described" alert.
_MODEL_PREDICATE = re.compile(
    r"\b(?:is|are|was|were|be|'s|\u2019s)\s+(?:\w+\s+){0,3}free\b"          # is free, is a stealth model that's free
    r"|\bfree\s+to\s+(?:use|try)\b"                                            # Free to use
    r"|\bfree\s+(?:on|for)\s+(?:opencode|a\s+limited\s+time)\b"               # free on OpenCode
    r"|\b(?:is|are|was)\s+(?:\w+\s+){0,2}(?:available|offered)\b")            # is available on OpenCode

# Words that open an instruction or a fragment, never a model name.
NON_NAME_WORDS = frozenset(
    """
    a an the this that these those it its to see set contact if use run note read
    and or for of in on at by with from you your we our they their please free
    models model more less here there below above next previous
    """.split()
)

ENTRY_VERBS = r"\b(?:is|are|has|was|were|now)\b"

# THE ONE RULE for a head that is accepted on its shape (AC-13). A token reads
# as part of a NAME when it joins a capital onto a preceding lower-case letter
# ("MiniMax", "DeepSeek"), which is how product names are written and how
# headings are not. Numbers do NOT qualify: a digit, a dotted version or a year
# ("3.1", "4.2", "v1.2", "2026.11") is how a page numbers its own headings.
#
# This replaced several successive shape exceptions - any digit, then any dotted
# version, then "version but not a bare year" - each of which closed the heading
# that had been demonstrated and admitted the next one (rounds 9, 10, 11, 12).
# A head that carries no internal capital is still accepted when the run already
# knows it as a model identity, so a plain-worded or versioned real name is not
# lost (the catalog, the free list and the previous snapshot are that evidence).
_NAME_CAPITAL = re.compile(r"(?<=[a-z])[A-Z]")


def known_identity(phrase, known):
    """True when `phrase` is an identity the run already knows as a model.

    The free-models section appends an availability word to the name the model
    carries elsewhere ("Jev 1.13 Free" in the free list, "Jev 1.13" as the
    entry's own head), so a known id is also matched with that word removed.
    Still pure corroboration: the match can only come from an identity this run
    already holds, never from a word list.
    """
    if not known or not phrase:
        return False
    candidates = {slug(phrase), normalise(phrase)}
    expanded = set(candidates)
    for candidate in candidates:
        for suffix in ("-free-tier", "-free"):
            if candidate.endswith(suffix):
                expanded.add(candidate[: -len(suffix)])
    return bool(expanded & known) or bool(
        {c + "-free" for c in expanded} & known)


# The grammatical boundary between a SUBJECT and the claim made about it. A
# model name is followed by one of these; a section label is followed by more
# noun phrase ("Retention Free tier users' data is retained ..."). This is a
# closed class of function words, not a list of names or topics, so it neither
# admits nor rejects a model by vocabulary.
_PREDICATE_OPENERS = frozenset(
    """
    is are was were has have had do does did can could may might will would
    shall should must costs cost comes come remains remain offers offer
    follows follow uses use provides provide supports support includes include
    retains retain keeps keep stores store logs log shares share sends send
    transfers transfer sells sell discloses disclose free
    """.split())


def opens_with_predicate(text):
    """True when `text` begins with the claim rather than more noun phrase."""
    tokens = " ".join(str(text or "").split()).split()
    return bool(tokens) and tokens[0].strip(".,;:").lower() in _PREDICATE_OPENERS


def head_stands_as_subject(line, head):
    """True when `head` is the subject of a clause, not a cut-short noun phrase.

    Shape alone cannot separate "Foo Free" from "Retention Free": both are two
    ordinary capitalised words, and a heading set in bold in the same paragraph
    as its prose renders as "Retention Free tier users' data is retained ...".
    What separates them is what follows. A name is followed by the end of its
    own sentence or by the claim made about it; a section label is followed by
    more noun phrase, so the head the tokeniser chose is only the front of the
    real subject.
    """
    text = " ".join(str(line or "").split())
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    if not first.startswith(head):
        return False
    rest = first[len(head):].strip()
    if not rest:
        return True
    if rest[0] in ".!?;":
        return True
    return rest.split()[0].strip(".,;:").lower() in _PREDICATE_OPENERS


def looks_like_model_name(phrase, labelled=False, known=None, subject=False):
    """True when `phrase` names a model rather than a piece of section prose.

    Shape alone was not enough: any single capitalised common word satisfied
    "carries a capital", so "Update: data is retained for 30 days." keyed as
    the model "update" and fired a retention alert naming a model the source
    never states (MW-EXTRACTOR-ADMITS-SECTION-PROSE, AC-13/AC-25).

    The rule is ONE structural test in both directions and needs no vocabulary:
    a head is a model when the run already knows the phrase as a model identity
    (the catalog, the free list or the previous snapshot), or when some token in
    it carries an internal capital. Nothing else qualifies. Identity data is
    what lets a plain-worded or versioned real name ("Beam Free", "Jev 1.13")
    survive; the internal capital is what accepts a name the run has not seen
    yet ("MiniMax M2", "DeepSeek V5") without also accepting a heading.
    Continuity is safe because the previous snapshot is part of that data, so a
    model already tracked never stops being known.

    A digit, a dotted version and a year are all excluded on purpose. Each was
    tried as the shape that separates a name from a heading, and each admitted
    the next heading shape: "Section 3:" then "3.1 Retention terms:" then
    "Changelog v1.2:" (MW-EXTRACTOR-LABEL-VERSION-ROUTE, rounds 9-12). A number
    is how a page numbers its headings, so it cannot be what marks a name.
    """
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
    # The head ran on into the sentence's real subject ("Retention Policy
    # Prompts are retained ..."), so the reader's data is the subject and the
    # line states a posture, not a model. Judged BEFORE the accept routes: a
    # numbered heading ("Section 3 Requests are retained ...") carries a digit,
    # so a digit-based route let it through on the very vocabulary this rule
    # already treats as not-a-name. Only the LAST token counts: a real name may
    # carry one of these words mid-name ("Log Runner Free"), never as its
    # subject.
    if tokens[-1].lower().strip("'\u2019s") in _DATA_SUBJECTS:
        return False
    # Both routes, one rule: a known identity, or an internal capital. Applied
    # identically to a colon label and to a name followed by its own clause, so
    # a heading shape cannot survive on the route the last fix did not touch.
    # Identity is judged BEFORE the provider veto because it is the only
    # evidence that a phrase the shape rules dislike is nevertheless a model.
    if known_identity(phrase, known):
        return True
    # A provider's own name is never one of its models. "OpenCode: Prompts are
    # retained for 30 days." is a section label, and it satisfied the internal
    # capital that accepts a new name, so it keyed as a model called OpenCode
    # and would fire a retention alert naming something the page never states
    # (AC-13/AC-25). The same name also opens page SECTIONS - "OpenCode Console
    # provides a list of models ...", "OpenCode Go provides a subscription ..."
    # - and every one of those carries the provider's own internal capital, so
    # the veto has to cover a head that OPENS with a provider, not only a head
    # that IS one. The identities come from PROVIDER_LABEL - this run's own
    # providers - so no heading word is written down here, and a real model that
    # opens with the provider word is still kept once the run knows it.
    if slug(tokens[0]) in _PROVIDER_IDENTITIES:
        return False
    if any(_NAME_CAPITAL.search(t) for t in tokens):
        return True
    # The head ran on into the sentence's real subject ("Retention Policy
    # Prompts are retained ..."), so the subject is the reader's data and the
    # line states a posture, not a model. Unreachable after the last-token veto
    # above for every shape measured, and kept so a later relaxation of that
    # veto cannot silently reintroduce the defect.
    if any(t.lower().strip("'\u2019s") in _DATA_SUBJECTS for t in tokens):
        return False
    # Neither of those accepted the phrase. A plain-worded head can still be a
    # real name the run has not seen yet ("North Mini Code Free"), so it is kept
    # only when it is SHAPED like a proper name AND the line makes it the
    # subject of its own clause. A colon label cannot use this route: a heading
    # and an entry share the colon shape, so the label form must be corroborated
    # by identity or an internal capital.
    if labelled or len(tokens) == 1:
        return False
    # A determiner opens a noun phrase about the page's whole catalogue - "All
    # models", "Some models", "Each model", "All free models on this page" -
    # never a name, and the head reader already skips them for that reason.
    # Without this the subject test alone admitted every one of them, and each
    # fired a retention alert naming a model the source never states
    # (AC-13/AC-25).
    if tokens[0].lower() in _DETERMINERS:
        return False
    # Every word of a proper name is a name word. Prose is not: "free", "models",
    # "on this page" are ordinary lower-case words, and any one of them means the
    # head ran on into the sentence instead of naming something.
    if not all(_name_token(t) for t in tokens):
        return False
    return bool(subject)


def _name_token(token):
    """True for a token that can belong to a model name.

    A name token carries a capital, a digit or a dotted version. A predicate
    word ("may", "costs", "can", "is") carries none of them, which is what
    lets the head be found without listing predicates: an allowlist of copulas
    made "GLM-5.3-Flash Free may be used at no cost ..." head the whole first
    sentence, which no longer looked like a name, so the entry was dropped and
    the next run reported the model as "no longer described" while the page
    still said it verbatim.
    """
    return bool(re.search(r"[A-Z0-9.]", token)) or "-" in token


# A determiner opens a noun phrase but is never part of the name itself, so it
# is consumed with the head and then dropped. Without that, "All requests are
# retained for 30 days" headed on the bare determiner.
_DETERMINERS = frozenset(
    "the all any each every some most many few both no your our their its his her "
    "this that these those".split())

# The nouns the retention rule treats as the reader's data. A line that opens
# with one of these states a posture about the data; it does not name a model,
# so it cannot be a policy entry for one ("Requests are retained for 30 days"
# is not a model called "Requests").
_DATA_SUBJECTS = frozenset(
    "data input inputs prompt prompts content request requests conversation "
    "conversations message messages query queries upload uploads submission "
    "submissions chat chats log logs information usage record records copy "
    "copies".split())


def policy_head(line):
    """The model name a policy line opens with.

    Taken from the FIRST SENTENCE, so a page may state the model and then start
    a new sentence ("GLM-5.3-Flash Free. Free to use on OpenCode for a limited
    time."). Within that sentence the head is the leading run of name-shaped
    tokens behind any determiners; the first token that is not one begins the
    assertion about the model, which is why the predicate need not be written
    down anywhere. A line whose head would be the reader's data names no model
    at all, and a head that is not name-shaped (the line opens with its claim
    rather than with the model) falls back to the copula split the older
    entries were keyed with.
    """
    first = re.split(r"(?<=[.!?])\s+", " ".join(line.split()), maxsplit=1)[0]
    # A colon label ends the head. "Beam Free: Prompts and other inputs ..." is
    # a real entry and "Update: data is retained for 30 days." is not, so the
    # label alone is the head either way and the difference is judged by
    # looks_like_model_name(), not by where the colon falls.
    label = re.match(r"^([^:]{1,60}):\s", first)
    if label:
        return label.group(1).strip().strip(".,;:")
    run = []
    for token in first.split():
        word = token.strip(".,;:").lower()
        if word in _DETERMINERS:
            if run:
                break
            continue
        if not run and word in _DATA_SUBJECTS:
            return ""
        if not _name_token(token):
            break
        run.append(token)
    if run:
        return " ".join(run).strip().strip(".,;:")
    return re.split(ENTRY_VERBS, first, maxsplit=1)[0].strip().strip(".,;:")


def asserts_about_model(line):
    """True when `line` makes a claim about the model it names."""
    return is_retention_sentence(line) or bool(_MODEL_PREDICATE.search(line.lower()))


def head_is_labelled(line):
    """True when the head is a section label rather than a model's own clause.

    "Update: data is retained for 30 days." and "Big Pickle: During its free
    period, ..." share a shape, so the colon alone decides nothing. It does
    tell the caller the head is a LABEL, which is judged more strictly than a
    name followed by its own sentence.
    """
    first = re.split(r"(?<=[.!?])\s+", " ".join(str(line or "").split()), maxsplit=1)[0]
    return bool(re.match(r"^[^:]{1,60}:\s", first))


def is_policy_entry(line, known=None):
    line = " ".join(line.split())
    if len(line) < 20:
        return False
    head = policy_head(line)
    if not looks_like_model_name(
            head, labelled=head_is_labelled(line), known=known,
            subject=head_stands_as_subject(line, head)):
        return False
    return asserts_about_model(line)


def extract_policy(lines, known=None):
    """slug -> prose, for every line that is a real model statement.

    A statement can wrap: a page that puts the model name on one line and its
    prose on the next is joined, so a real entry is not lost to line breaking.
    The continuation must itself assert something and must not open with
    another model name, so furniture cannot be glued onto an entry.

    `known` is the set of model identities this run already has (catalog ids,
    free ids, the previous snapshot). It is what lets a plain-worded model name
    survive the stricter label rule without any vocabulary list.
    """
    policies = {}
    lines = [" ".join(l.split()) for l in lines]
    for i, line in enumerate(lines):
        if not line:
            continue
        text = line
        head = policy_head(text)
        if not asserts_about_model(text) and looks_like_model_name(
                head, known=known, subject=head_stands_as_subject(text, head)):
            nxt = next((l for l in lines[i + 1:] if l), "")
            # The continuation must open with the claim itself. A heading whose
            # next line is more noun phrase ("Retention Policy" / "Prompts are
            # retained for 30 days.") would otherwise be glued into a head that
            # reads as a name the source never states (AC-25).
            if nxt and opens_with_predicate(nxt) \
                    and not looks_like_model_name(policy_head(nxt), known=known) \
                    and asserts_about_model(nxt):
                text = "%s %s" % (text, nxt)
        if not is_policy_entry(text, known=known):
            continue
        key = slug(policy_head(text))
        if key:
            policies[key] = text
    return policies


def opencode_policy_text(known=None):
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
            found = extract_policy(body, known=known)
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

# Retention windows. Days are the common case, but providers also state hours,
# weeks and months. Every one of those is a stated window, so none may fall
# through to `unstated`: that would print "it does not say what it does with
# your data" directly above the quote that says it.
_RETAIN_VERB = (r"(?:keeps?|kept|stores?|stored|holds?|held|retains?|retained|"
                r"are\s+kept|is\s+kept|are\s+stored|is\s+stored|are\s+retained|is\s+retained|"
                r"logs?|logged|archives?|archived)")
_RETAIN_UNIT = r"(days?|hours?|weeks?|months?)"
# Unit -> days, used only to pick the longest window when a statement names two.
_RETAIN_UNIT_DAYS = {"d": 1.0, "h": 1.0 / 24, "w": 7.0, "mo": 30.0}
# Providers hedge a window ("up to", "at most", "no more than") and sometimes put
# the number after the noun ("retention period is 30 days"). Every one of those
# is a stated window: missing them printed "it does not say what it does with
# your data" directly above the quote that says 30 days.
_QUALIFIER = (r"(?:(?:up\s+to|at\s+most|no\s+more\s+than|less\s+than|"
              r"not\s+more\s+than|a\s+maximum\s+of|maximum\s+of)\s+)?")
RETAIN_PATTERNS = (
    re.compile(_RETAIN_VERB + r"[^.;]{0,48}?\b(?:for|within)\s+" + _QUALIFIER
               + r"(\d+)\s+" + _RETAIN_UNIT + r"\b"),
    re.compile(r"\b(\d+)[-\s]" + _RETAIN_UNIT + r"\s+(?:retention|storage|log)\b"),
    re.compile(r"retention\s+(?:period|window)\s*[:\-=]?\s*(?:is|of)?\s*" + _QUALIFIER
               + r"(\d+)\s+" + _RETAIN_UNIT + r"\b"),
    re.compile(r"\b(?:deletes?|wipes?|erases?|removes?|purges?|discards?)"
               r"[^.;]{0,48}?\b(?:after|within)\s+" + _QUALIFIER
               + r"(\d+)\s+" + _RETAIN_UNIT + r"\b"),
)
_RETAIN_UNIT_SUFFIX = {"d": "d", "h": "h", "w": "w", "mo": "mo"}
_RETAIN_UNIT_WORD = {"d": "day", "h": "hour", "w": "week", "mo": "month"}
_RETAINED_CLASS = re.compile(r"retained-(\d+)(mo|d|h|w)$")

# A NEGATED window that is still a stated cap: "we do not retain data for more
# than 30 days" promises 30 days, not zero. Read as a cap before the no-retention
# rule, which would otherwise call it zero-retention and overstate the promise.
_CAPPED_WINDOW = re.compile(
    r"\b(?:not|never|no longer)\b[^.;]{0,24}?\b(?:keeps?|stores?|retains?|holds?)\b"
    r"[^.;]{0,32}?\b(?:more than|longer than|beyond|over)\s+(\d+)\s+" + _RETAIN_UNIT + r"\b")

# An explicit promise NOT to keep a copy. This is a stated posture, not silence,
# so it must never be reported as `unstated`.
NO_RETAIN_PATTERNS = (
    r"\b(?:do|does|did|will|would)\s+not\s+(?:keep|store|retain|hold|save|log)\b"
    r"[^.;]{0,40}?\b(?:your|the|any|this)\s+(?:data|inputs?|prompts?|content|requests?)\b",
    r"\bnever\s+(?:keeps?|stores?|retains?|holds?|saves?|logs?)\b"
    r"[^.;]{0,40}?\b(?:your|the|any|this)\s+(?:data|inputs?|prompts?|content|requests?)\b",
    r"\b(?:no|zero)\s+(?:data\s+|prompt\s+)?(?:retention|storage|logging)\b",
    r"\b(?:discards?|deletes?|erases?|purges?)\s+(?:your|the|any)\s+"
    r"(?:data|inputs?|prompts?|content)\s+(?:immediately|at once|right away)\b",
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


def _unit_key(raw):
    raw = (raw or "").strip().lower()
    for key in ("month", "week", "hour", "day"):
        if raw.startswith(key):
            return {"month": "mo", "week": "w", "hour": "h", "day": "d"}[key]
    return "d"


def retention_window(low):
    """The stated retention window as (count, unit), or None.

    The longest stated window wins, compared on a day-equivalent scale so
    "2 weeks" outranks "10 days".
    """
    matches = []
    for pattern in RETAIN_PATTERNS:
        for m in re.finditer(pattern, low):
            if _negated(low, m.start()):
                continue
            try:
                count = int(m.group(1))
            except (TypeError, ValueError):
                continue
            unit = _unit_key(m.group(2) if m.lastindex and m.lastindex >= 2 else "")
            matches.append((count * _RETAIN_UNIT_DAYS[unit], count, unit))
    for m in _CAPPED_WINDOW.finditer(low):
        unit = _unit_key(m.group(2))
        count = int(m.group(1))
        matches.append((count * _RETAIN_UNIT_DAYS[unit], count, unit))
    if not matches:
        return None
    _, count, unit = max(matches)
    return count, unit


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
    window = retention_window(low)
    if window is not None:
        return "retained-%s%s" % (window[0], _RETAIN_UNIT_SUFFIX[window[1]])
    # A stated refusal to keep a copy ("we do not retain your data") is a
    # no-copy claim, not an absent one. Left as `unstated` the alert would print
    # "it does not say what it does with your data" over prose that plainly does.
    # A stated window outranks this, so "does not retain for more than 30 days"
    # keeps its window.
    if _first_unanchored(NO_RETAIN_PATTERNS, low):
        return "zero-retention"
    if re.search(r"\blogged\b|\blogs\b|\blogging\b|\barchives?\b|\barchived\b|"
                 r"\brecords?\b|\brecorded\b", low):
        return "logged"
    return "unstated"


def class_clause(cls, tense="present"):
    """The class as a clause the owner can read, in the tense that fits."""
    past = tense == "past"
    if cls == "zero-retention":
        return ("kept no copy of your data (zero retention)" if past
                else "keeps no copy of your data (zero retention)")
    m = _RETAINED_CLASS.match(cls or "")
    if m:
        count, unit = m.group(1), m.group(2)
        word = _RETAIN_UNIT_WORD[unit]
        if count != "1":
            word += "s"
        return ("kept your data for %s %s" if past
                else "keeps your data for %s %s") % (count, word)
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
    if _RETAINED_CLASS.match(cls or ""):
        return CLASS_SEVERITY["retained"]
    return None


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def changed_sentence(before, after):
    """The sentence in `after` that changed and carries a privacy promise.

    A wording change is often an ADDED clause ("... It may disclose it to law
    enforcement on request."). Naming the class-carrying sentence there would
    point the owner at the one line that did not change, so a changed sentence
    that makes a privacy promise wins. A sentence that changed but states no
    privacy posture is marketing copy: it is never named, because naming it
    would tell the owner the provider changed something about his data when it
    did not (AC-19/AC-25).
    """
    old = {" ".join(s.lower().split()) for s in _sentences(before)}
    new = [s for s in _sentences(after) if " ".join(s.lower().split()) not in old]
    for sentence in new:
        if is_retention_sentence(sentence):
            return sentence
    # A clause rewritten in place leaves no new sentence behind; the sentence
    # that differs from its counterpart is the one that changed.
    was = _sentences(before)
    for index, sentence in enumerate(_sentences(after)):
        if index >= len(was) or " ".join(sentence.lower().split()) != " ".join(was[index].lower().split()):
            return sentence if is_retention_sentence(sentence) else ""
    return ""


def carrier_sentence(text, cls):
    """The one sentence in `text` that carries the retention statement."""
    sentences = _sentences(text)
    if not sentences:
        return ""
    if cls == "zero-retention":
        # A refusal ("we do not retain your data", "no data retention") carries
        # the class as surely as the named policy does, and must out-rank a
        # marketing sentence that happens to come first.
        for sentence in sentences:
            low = " ".join(sentence.lower().split())
            if ("zero-retention" in low or "zero retention" in low
                    or _first_unanchored(NO_RETAIN_PATTERNS, low)):
                return sentence
        keys = ("zero-retention", "zero retention")
    elif cls and cls.startswith("retained-"):
        # Any stated window, in whatever unit the provider wrote it.
        for sentence in sentences:
            if retention_window(" ".join(sentence.lower().split())):
                return sentence
        keys = ("retains", "retained", " days")
    elif cls == "trains-or-logs":
        keys = ("improve the model", "collect feedback", "train")
    elif cls == "logged":
        # The prose says "logs", not "logged"; keying on the participle alone
        # fell through to the marketing sentence that happens to come first.
        keys = ("logged", "logs", "logging", "log your", "log the", "archives", "records")
    else:
        keys = ()
    for sentence in sentences:
        low = sentence.lower()
        if any(k in low for k in keys):
            return sentence
    for sentence in sentences:
        if is_retention_sentence(sentence):
            return sentence
    return ""


def policy_name(text, fallback):
    """The model's own name, as the fetched prose states it.

    Uses the same head rule the extractor keys on, so the name an alert shows
    the owner is the name the entry was found under. Splitting on a copula here
    printed a whole free clause as the model's name.
    """
    return policy_head(text or "") or fallback


# A sentence counts as a retention/privacy statement only when it STRUCTURALLY
# makes one: an act (retain/keep/store/hold/log/archive/delete/purge) or a
# disclosure act (disclose/share/sell/transfer) applied to the reader's data, or
# a training act on it. A keyword list - "day", "log", "keep", "share" on their
# own - is what made "Try it free for 7 days" and "Log in to see your usage"
# read as retention alerts. Requiring the act AND its object is the rule that
# excludes page furniture without a blacklist of today's junk strings.
_DATA_NOUN = (r"\b(?:data|inputs?|prompts?|content|requests?|conversations?|messages?|"
              r"queries|query|uploads?|submissions?|chats?|chat\s+logs?|"
              r"personal\s+information|personal\s+data|training\s+data)")
DATA_OBJECT = (r"(?:(?:your|their|the|any|all|this|that|such|"
               r"user'?s?|users'|customer'?s?|person'?s?|reader'?s?)\s+)?"
               + _DATA_NOUN)
# Every alternative is word-bounded. Without the trailing boundary "provide"
# matched inside the NOUN "provider", so "This provider supports prompt caching"
# read as a disclosure act applied to your data and fired a retention alert.
_RETENTION_ACT = (
    r"(?:\bretain(?:s|ed|ing)?|\bkeeps?|\bkept|\bkeeping|\bstores?|\bstored|\bstoring|"
    r"\bholds?|\bheld|\bholding|\blogs?|\blogged|\blogging|\barchiv(?:e|es|ed|ing)|"
    r"\brecord(?:s|ed|ing)?|\bsav(?:e|es|ed|ing)|\bpersist(?:s|ed|ing)?|\bdelet(?:e|es|ed|ing)|"
    r"\bwip(?:e|es|ed|ing)|\beras(?:e|es|ed|ing)|\bpurg(?:e|es|ed|ing)|"
    r"\bremov(?:e|es|ed|ing)|\bdiscard(?:s|ed|ing)?)\b")
_TRAIN_ACT = (r"(?:\btrain(?:s|ed|ing)?|\blearn(?:s|ed|ing)?|\bimprov(?:e|es|ed|ing)|"
              r"\bcollect(?:s|ed|ing)?\s+feedback)")
_DISCLOSURE_ACT = (r"(?:\bdisclos(?:e|es|ed|ing)|\bshar(?:e|es|ed|ing)|\bsell\b|\bsold\b|"
                   r"\btransfer(?:s|red|ring)?|\bprovid(?:e|es|ed|ing)|"
                   r"\bhand(?:s|ed)?\s+over|\bgiv(?:e|es|ing)|\bgiven)\b")
_LEGAL_OBJECT = (r"(?:law\s+enforcement|subpoena|legal\s+(?:request|process|order)|"
                 r"court\s+order|third[\s-]part(?:y|ies)|partners?|affiliat(?:e|es)|"
                 r"regulators?)")
# "retention policy/period" names the subject outright; with a stated window it
# is a retention sentence even where the object is a bare pronoun.
_RETENTION_SUBJECT = re.compile(
    r"retention\s+(?:policy|period|window)|data\s+retention|storage\s+period")


def _adjacent_acts(first, second, low, gap=64):
    """True when `first` and `second` occur in the same clause, either order."""
    pattern = r"(?:%s)[^.;]{0,%d}?(?:%s)|(?:%s)[^.;]{0,%d}?(?:%s)" % (
        first, gap, second, second, gap, first)
    return bool(re.search(pattern, low))


def is_retention_sentence(sentence):
    """True only for a sentence that structurally states a privacy posture.

    Used both to decide whether a wording edit is worth alerting on and to name
    the sentence that carries a change, so it must not admit page furniture.
    """
    low = " ".join(str(sentence or "").lower().split())
    if not low:
        return False
    if re.search(r"zero[-\s]retention|no\s+(?:data\s+)?(?:retention|storage|logging)", low):
        return True
    for patterns in (TRAIN_PATTERNS, NO_RETAIN_PATTERNS):
        if _first_unanchored(patterns, low):
            return True
    if _RETENTION_SUBJECT.search(low) and retention_window(low):
        return True
    if _adjacent_acts(_RETENTION_ACT, DATA_OBJECT, low):
        return True
    if _adjacent_acts(_DISCLOSURE_ACT, DATA_OBJECT, low):
        return True
    return _adjacent_acts(_DISCLOSURE_ACT, _LEGAL_OBJECT, low)


def retention_sentences(text):
    """The sentences in `text` that carry a retention/privacy statement.

    Diffing the whole line would fire on a reworded marketing clause ("free for
    a limited time" -> "free for a short time"), which is noise. Diffing only
    the sentences that structurally state a privacy posture catches a reworded
    promise at ANY class - including a new disclosure or sharing clause that
    leaves the class alone - and stays silent on everything else.
    """
    return [" ".join(s.lower().split()) for s in _sentences(text)
            if is_retention_sentence(s)]


def policy_block_digest(policies):
    """Digest of the retention-relevant prose only, never the whole page.

    A digest restricted to the section that states retention cannot move when a
    navigation or marketing edit lands, which is what made DOC CHANGED noise
    (AC-19). It is recorded as a secondary signal; no event derives from it.
    """
    joined = "\n".join("%s: %s" % (k, policies[k]) for k in sorted(policies))
    return hashlib.sha256(re.sub(r"\s+", " ", joined).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- collection

def policy_identities(cat, previous, providers):
    """Every model identity this run already knows, as keys.

    Used to corroborate a policy head that carries no version marker. The set
    is built from data the run holds - catalog ids, the previous snapshot's
    free and policy keys - so no model name is ever written into the code and a
    plain-worded model the source states is not rejected.
    """
    known = set()
    for pid in providers:
        for mid in (cat.get(pid) or {}):
            known.add(normalise(mid))
            known.add(slug(mid))
    for section in ("free", "policy"):
        for pid, entries in ((previous or {}).get(section) or {}).items():
            if isinstance(entries, dict):
                keys = entries.keys()
            else:
                keys = entries or []
            for key in keys:
                known.add(normalise(key))
                known.add(slug(key))
    return known


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
        doc_url, prose = opencode_policy_text(known=policy_identities(cat, previous, providers))
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
