#!/usr/bin/env python3
"""Fixture suite for watch.py. Stdlib only, no network.

Runs two ways:

    python -m pytest tests/
    python tests/test_model_watch.py

Fixtures are synthetic state in the shapes the live sources actually produce, so
a test can drive collect(), diff() and render() with no network call. The prose
quoted in the retention fixtures is copied from the real OpenCode page as
captured on 2026-11-04 (see output/EVIDENCE_REPORT.md); it is not invented.
"""
import contextlib
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import watch  # noqa: E402

WATCH = [
    "opencode/glm-5.3-flash-free",
    "opencode/deepseek-v4.1-flash-free",
    "opencode/kimi-k2.5-free",
]
WATCH_MODELS = [entry.split("/", 1)[1] for entry in WATCH]

GLM_PROSE = (
    "GLM-5.3-Flash Free is free on OpenCode for a limited time. Its provider "
    "follows a zero-retention policy and does not use your data for model training."
)
KIMI_PROSE = (
    "Kimi K2.5 Free is free on OpenCode for a limited time. Its provider follows "
    "a zero-retention policy and does not use your data for model training."
)
BEAM_PROSE = (
    "Beam Free is free on OpenCode for a limited time. Its provider does not use "
    "your data for model training and retains it for 30 days for safety reasons."
)
TRAINS_PROSE = (
    "Flint 40B Free is free on OpenCode for a limited time. The team is using this "
    "time to collect feedback and improve the model."
)
# The real page has no model that states both zero-retention and trains-on-data,
# so the class-change fixtures reuse a real statement with only the model name
# changed. They are synthetic by construction and are not used as "real" evidence.
TRAINS_BEAM_PROSE = TRAINS_PROSE.replace("Flint 40B Free", "Beam Free")
TRAINS_GLM_PROSE = TRAINS_PROSE.replace("Flint 40B Free", "GLM-5.3-Flash Free")

# A page in the real shape: a "Free models" heading, model statements, then the
# furniture that used to become policy keys.
DOC_PAGE = (
    "<html><body><nav>Models Pricing Docs</nav>"
    "<h2>Free models</h2><ul><li>%s</li><li>%s</li><li>%s</li></ul>"
    "<p>To hide free models in OpenCode, set</p><p>OPENCODE_DISABLE_FREE_MODELS=1</p>"
    "<p>. See</p><p>free models</p><p>Contact us</p><p>if you have any questions.</p>"
    "</body></html>"
) % (GLM_PROSE, BEAM_PROSE, TRAINS_PROSE)

MODELS_DEV_JSON = json.dumps({
    "opencode": {"models": {
        "glm-5.3-flash-free": {"name": "GLM-5.3-Flash Free", "cost": {"input": 0, "output": 0},
                               "release_date": "2026-10-01"},
        "kimi-k2.5-free": {"name": "Kimi K2.5 Free", "cost": {"input": 0.2, "output": 0.8},
                           "release_date": "2026-09-01"},
        "beam-free": {"name": "Beam Free", "cost": {"input": 0, "output": 0},
                      "release_date": "2026-08-01"},
        "flint-40b-free": {"name": "Flint 40B Free", "cost": {"input": 0, "output": 0},
                           "release_date": "2026-09-15"},
        "north-mini-code-free": {"name": "North Mini Code Free",
                                  "cost": {"input": 0, "output": 0},
                                  "release_date": "2026-09-20"},
    }},
    "openrouter": {"models": {
        "cohere/north-mini-code:free": {"name": "North Mini Code", "cost": {"input": 0, "output": 0}},
    }},
})

OPENROUTER_JSON = json.dumps({"data": [
    {"id": "cohere/north-mini-code:free", "name": "Cohere: North Mini Code (free)",
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["text"]}},
    {"id": "openrouter/free", "name": "Free Models Router",
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["text"]}},
    {"id": "google/lyria-3-pro-preview", "name": "Google: Lyria 3 Pro Preview",
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["text", "audio"]}},
    {"id": "stealth/crouton-alpha", "name": "Crouton Alpha",
     "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["text"]}},
]})


@contextlib.contextmanager
def offline(pages=None, models_dev=MODELS_DEV_JSON):
    """Replace watch.fetch with a canned table so no test touches the network."""
    table = dict(pages or {})
    table.setdefault(watch.MODELS_DEV, models_dev)
    table.setdefault(watch.OPENROUTER_MODELS, OPENROUTER_JSON)
    table.setdefault(watch.OPENCODE_DOCS[0], DOC_PAGE)
    table.setdefault(watch.OPENROUTER_PRIVACY_DOCS, "<html>privacy wording</html>")
    seen = []
    original = watch.fetch

    def fake(url, timeout=60):
        seen.append(url)
        if url not in table:
            raise OSError("offline: %s not in fixture table" % url)
        return table[url]

    watch.fetch = fake
    try:
        yield seen
    finally:
        watch.fetch = original


# The identities a real run holds for the three models the fixture page states.
# A head is a model only when this data names it or it carries an internal
# capital, so every test that asserts on a plain-worded name must hand it in -
# exactly as collect() does.
GLM_KNOWN = frozenset({"glm-5-3-flash-free", "beam-free", "flint-40b-free"})


def live_prose():
    """opencode_policy_text() with the identity data collect() really supplies.

    The extractor now requires corroboration: a head is a model only when the
    run's identity data names it or it carries an internal capital. A bare
    opencode_policy_text() call, as an earlier version of these tests made it,
    supplies neither, so it measured an extraction path that never runs. This
    helper hands the real catalogue in, exactly as collect() does.
    """
    models = json.loads(MODELS_DEV_JSON)
    cat = watch.catalog(models, ["opencode"])
    return watch.opencode_policy_text(
        known=watch.policy_identities(cat, {}, ["opencode"]),
        observed=watch.observed_identities(cat, ["opencode"]))


def config(**over):
    base = {"providers": ["opencode"], "watch": list(WATCH), "mention": "MatthewMBowyer",
            "webhook": "https://ntfy.example.invalid/topic"}
    base.update(over)
    return base


def state(free=None, policy=None, docs=None):
    return {"free": free or {}, "policy": policy or {}, "docs": docs or {}}


def policy_map(**entries):
    """slug -> {text, class}; class derived by the real classifier."""
    return {k: {"text": v, "class": watch.classify(v)} for k, v in entries.items()}


def catalog(models):
    """provider -> model map in the shape catalog() returns."""
    return {"opencode": models}


def model(input_price=0.0, output_price=0.0, name=None, modalities=None):
    return {"name": name or "m", "input": input_price, "output": output_price,
            "released": None, "modalities": modalities or ["text"]}


# ------------------------------------------------------------------ headline

class HeadlineTests(unittest.TestCase):
    def test_quiet_run_headline_is_a_sentence(self):
        text = watch.render([], config())
        self.assertEqual(text.splitlines()[0], "Nothing changed since the last check.")

    def test_watched_model_going_paid_headline(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        events = watch.diff(old, new, cat, config())
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "One model you watch is no longer free: kimi-k2.5-free.",
        )

    def test_retention_headline_names_the_watched_model(self):
        old = state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": BEAM_PROSE})})
        events = watch.diff(old, new, catalog({}), config())
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "A model you watch changed its data-retention wording: glm-5.3-flash-free.",
        )

    def test_unwatched_retention_headline_is_the_catch_line(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"beam-free": TRAINS_PROSE})})
        events = watch.diff(old, new, catalog({}), config())
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "A provider changed its data-retention wording - this is the kind of "
            "change this tool exists to catch.",
        )

    def test_headline_is_exactly_one_sentence(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]}, policy={
            "opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        new = state(free={"opencode": []}, policy={
            "opencode": policy_map(**{"glm-5-3-flash-free": TRAINS_PROSE})})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        headline = text.splitlines()[0]
        self.assertEqual(len(re.findall(r"[.!?](\s|$)", headline)), 1, headline)
        for code in ("NEW FREE", "no longer free", "POLICY CHANGED", "DOC CHANGED"):
            self.assertFalse(headline.startswith(code), headline)


# ------------------------------------------------------------------ grouping

class GroupingTests(unittest.TestCase):
    def test_all_three_headings_always_present_in_order(self):
        text = watch.render([], config())
        heads = [l for l in text.splitlines()
                 if l.startswith(("Models you watch", "Data retention", "Other free models"))]
        self.assertEqual(len(heads), 3)
        self.assertTrue(heads[0].startswith("Models you watch (0)"))
        self.assertTrue(heads[1].startswith("Data retention and privacy wording (0)"))
        self.assertTrue(heads[2].startswith("Other free models (0)"))

    def test_empty_groups_say_so_explicitly(self):
        text = watch.render([], config())
        self.assertIn("Nothing here - none of the models you watch changed.", text)
        self.assertIn("Nothing here - no provider changed what it says about your data.", text)
        self.assertIn("Nothing here - no other free model appeared or disappeared.", text)

    def test_group_counts_equal_items_listed(self):
        old = state(
            free={"opencode": ["kimi-k2.5-free", "beam-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE,
                "flint-40b-free": TRAINS_PROSE,
            })},
        )
        new = state(
            free={"opencode": ["beam-free", "brand-new-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": TRAINS_PROSE,
                "flint-40b-free": GLM_PROSE,
            })},
        )
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8), "beam-free": model(),
                       "brand-new-free": model()})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        counts = dict(re.findall(r"^(Models you watch|Data retention and privacy wording|"
                                 r"Other free models) \((\d+)\)$", text, re.M))
        self.assertEqual(counts["Models you watch"], "2")
        self.assertEqual(counts["Data retention and privacy wording"], "1")
        self.assertEqual(counts["Other free models"], "1")

    def test_watched_events_are_only_in_group_a(self):
        old = state(
            free={"opencode": ["kimi-k2.5-free"]},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})},
        )
        new = state(
            free={"opencode": []},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": TRAINS_PROSE})},
        )
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        events = watch.diff(old, new, cat, config())
        self.assertEqual(len([e for e in events if e["watched"]]), 2)
        self.assertEqual(len([e for e in events if not e["watched"]]), 0)
        text = watch.render(events, config())
        self.assertIn("Models you watch (2)", text)
        self.assertIn("Data retention and privacy wording (0)", text)
        self.assertIn("Other free models (0)", text)

    def test_delisted_watched_model_keeps_count_and_listing_in_agreement(self):
        """INV-03 / INV-15: the stated count must equal the items actually listed,
        and the one model must be listed exactly once."""
        events = watch.diff(
            state(free={"opencode": ["kimi-k2.5-free"]}),
            state(free={"opencode": []}),
            catalog({"glm-5.3-flash-free": model()}),
            config(),
        )
        text = watch.render(events, config())
        group_a = text.split("Data retention and privacy wording")[0]
        self.assertEqual(sum(1 for l in group_a.splitlines()
                             if l.startswith("kimi-k2.5-free")), 1, group_a)
        self.assertIn("Models you watch (1)", text)
        self.assertEqual(len(events), 1)
        self.assertEqual(sum(1 for e in events if e["watched"]), 1)

    def test_group_b_count_line_mentions_the_watched_one_above(self):
        old = state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": BEAM_PROSE})})
        text = watch.render(watch.diff(old, new, catalog({}), config()), config())
        self.assertIn("Nothing here - the 1 retention change on a model you watch is listed above.",
                      text)


# ------------------------------------------------------------------ prose register

class ProseTests(unittest.TestCase):
    def test_watched_goes_paid_register_matches_the_owner_example(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        self.assertIn("kimi-k2.5-free is no longer free.", text)
        self.assertIn("It was $0 in and $0 out at the last check; it now costs "
                      "$0.20/M in, $0.80/M out.", text)
        self.assertIn("You have it on your watch list, so this is likely to affect your setup.",
                      text)

    def test_no_bare_event_codes_anywhere(self):
        old = state(free={"opencode": ["kimi-k2.5-free", "gone-free"]})
        new = state(free={"opencode": ["added-free"]})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8), "added-free": model()})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        for code in ("NEW FREE", "NO LONGER FREE", "POLICY CHANGED", "DOC CHANGED",
                     "WATCHED MODEL"):
            self.assertNotIn(code, text)

    def test_unstated_price_is_never_invented(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"kimi-k2.5-free": model(None, None)})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        self.assertIn("unstated", text)
        self.assertNotIn("$0.00", text)


# ------------------------------------------------------------------ retention

class RetentionTests(unittest.TestCase):
    def test_class_change_quotes_before_and_after_prose(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})})
        text = watch.render(watch.diff(old, new, catalog({}), config()), config())
        self.assertIn("Before, it kept no copy of your data (zero retention).", text)
        self.assertIn("Now it keeps your data for 30 days.", text)
        self.assertIn('Before: "%s"' % watch.wrap(GLM_PROSE), text)
        self.assertIn('Now: "%s"' % watch.wrap(BEAM_PROSE), text)
        self.assertIn("The sentence carrying the change:", text)

    def test_class_change_to_trains_quotes_both(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"beam-free": TRAINS_BEAM_PROSE})})
        text = watch.render(watch.diff(old, new, catalog({}), config()), config())
        self.assertIn("Now it may use your data to train or improve the model.", text)
        self.assertIn('Before: "%s"' % watch.wrap(GLM_PROSE), text)
        self.assertIn('Now: "%s"' % watch.wrap(TRAINS_BEAM_PROSE), text)

    def test_never_a_bare_hash(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})},
                    docs={"https://opencode.ai/v2/docs/console/models/": "aaaaaaaaaaaaaaaa"})
        new = state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})},
                    docs={"https://opencode.ai/v2/docs/console/models/": "bbbbbbbbbbbbbbbb"})
        text = watch.render(watch.diff(old, new, catalog({}), config()), config())
        self.assertNotIn("aaaaaaaaaaaaaaaa", text)
        self.assertNotIn("bbbbbbbbbbbbbbbb", text)

    def test_newly_documented_worse_model_fires(self):
        old = state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{
            "glm-5-3-flash-free": GLM_PROSE,
            "flint-40b-free": TRAINS_PROSE,
        })})
        events = watch.diff(old, new, catalog({}), config())
        self.assertEqual([e["kind"] for e in events], ["policy_new_bad"])
        text = watch.render(events, config())
        self.assertIn("Flint 40B Free (OpenCode) is newly documented", text)
        self.assertIn("may use your data to train or improve the model", text)
        self.assertIn('It says: "%s"' % watch.wrap(TRAINS_PROSE), text)

    def test_newly_documented_zero_retention_model_is_silent(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})})
        new = state(policy={"opencode": policy_map(**{
            "beam-free": BEAM_PROSE, "glm-5-3-flash-free": GLM_PROSE})})
        self.assertEqual(watch.diff(old, new, catalog({}), config()), [])

    def test_removed_from_docs_while_still_free_fires(self):
        old = state(
            free={"opencode": ["flint-40b-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE, "flint-40b-free": TRAINS_PROSE})},
        )
        new = state(
            free={"opencode": ["flint-40b-free"]},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})},
        )
        events = watch.diff(old, new, catalog({"flint-40b-free": model()}), config())
        self.assertEqual([e["kind"] for e in events], ["policy_removed"])
        text = watch.render(events, config())
        self.assertIn("Flint 40B Free (OpenCode) is no longer described", text)
        self.assertIn("It used to say:", text)

    def test_removed_from_docs_and_no_longer_free_is_not_a_policy_event(self):
        old = state(
            free={"opencode": ["flint-40b-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE, "flint-40b-free": TRAINS_PROSE})},
        )
        new = state(
            free={"opencode": []},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})},
        )
        events = watch.diff(old, new, catalog({"flint-40b-free": model(1.0, 2.0)}), config())
        self.assertEqual([e["kind"] for e in events], ["free_removed"])

    def test_exact_text_class_change_to_retained_30d(self):
        """AC-17: exact text, so a changed word fails the assertion."""
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})}),
            catalog({}), config(),
        )
        self.assertEqual(watch.render(events, config()), "\n".join([
            "A provider changed its data-retention wording - this is the kind of "
            "change this tool exists to catch.",
            "",
            "Models you watch (0)",
            "Nothing here - none of the models you watch changed.",
            "",
            "Data retention and privacy wording (1)",
            "Beam Free (OpenCode) changed what its provider says about your data.",
            "Before, it kept no copy of your data (zero retention).",
            "Now it keeps your data for 30 days.",
            'Before: "%s"' % watch.wrap(GLM_PROSE),
            'Now: "%s"' % watch.wrap(BEAM_PROSE),
            watch.wrap('The sentence carrying the change: "%s"'
                       % "Its provider does not use your data for model training and "
                         "retains it for 30 days for safety reasons."),
            "Worth rereading the provider's data page before you send it anything private.",
            "",
            "Other free models (0)",
            "Nothing here - no other free model appeared or disappeared.",
            "",
            "@MatthewMBowyer",
        ]))

    def test_exact_text_class_change_to_trains_or_logs(self):
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"beam-free": TRAINS_BEAM_PROSE})}),
            catalog({}), config(),
        )
        self.assertEqual(watch.render(events, config()), "\n".join([
            "A provider changed its data-retention wording - this is the kind of "
            "change this tool exists to catch.",
            "",
            "Models you watch (0)",
            "Nothing here - none of the models you watch changed.",
            "",
            "Data retention and privacy wording (1)",
            "Beam Free (OpenCode) changed what its provider says about your data.",
            "Before, it kept no copy of your data (zero retention).",
            "Now it may use your data to train or improve the model.",
            'Before: "%s"' % watch.wrap(GLM_PROSE),
            'Now: "%s"' % watch.wrap(TRAINS_BEAM_PROSE),
            watch.wrap('The sentence carrying the change: "%s"'
                       % "The team is using this time to collect feedback and improve the model."),
            "Worth rereading the provider's data page before you send it anything private.",
            "",
            "Other free models (0)",
            "Nothing here - no other free model appeared or disappeared.",
            "",
            "@MatthewMBowyer",
        ]))

    def test_exact_text_new_model_that_trains_on_your_data(self):
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE, "flint-40b-free": TRAINS_PROSE})}),
            catalog({}), config(),
        )
        self.assertEqual(watch.render(events, config()), "\n".join([
            "A provider changed its data-retention wording - this is the kind of "
            "change this tool exists to catch.",
            "",
            "Models you watch (0)",
            "Nothing here - none of the models you watch changed.",
            "",
            "Data retention and privacy wording (1)",
            "Flint 40B Free (OpenCode) is newly documented, and its provider "
            "may use your data to train or improve the model.",
            'It says: "%s"' % watch.wrap(TRAINS_PROSE),
            "Worth rereading the provider's data page before you send it anything private.",
            "",
            "Other free models (0)",
            "Nothing here - no other free model appeared or disappeared.",
            "",
            "@MatthewMBowyer",
        ]))

    def test_exact_text_model_removed_from_the_docs(self):
        events = watch.diff(
            state(free={"opencode": ["flint-40b-free"]},
                  policy={"opencode": policy_map(**{
                      "glm-5-3-flash-free": GLM_PROSE, "flint-40b-free": TRAINS_PROSE})}),
            state(free={"opencode": ["flint-40b-free"]},
                  policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            catalog({"flint-40b-free": model()}), config(),
        )
        self.assertEqual(watch.render(events, config()), "\n".join([
            "A provider changed its data-retention wording - this is the kind of "
            "change this tool exists to catch.",
            "",
            "Models you watch (0)",
            "Nothing here - none of the models you watch changed.",
            "",
            "Data retention and privacy wording (1)",
            "Flint 40B Free (OpenCode) is no longer described on the provider's "
            "data-retention page.",
            "It is still free, so the retention promise you could rely on is no longer stated.",
            'It used to say: "%s"' % watch.wrap(TRAINS_PROSE),
            "Worth rereading the provider's data page before you send it anything private.",
            "",
            "Other free models (0)",
            "Nothing here - no other free model appeared or disappeared.",
            "",
            "@MatthewMBowyer",
        ]))


class RetentionWordingTests(unittest.TestCase):
    """MW-RETENTION-PROSE-NOT-DIFFED and MW-CLASSIFIER-MISSES-COMMON-WORDING.

    The reviewer measured that a retention statement reworded at the SAME class
    alerted nothing, and that the natural training/retention phrasings were all
    classified `unstated`. These fixtures reproduce those exact wordings.
    """

    # The reviewer's own two edits, verbatim.
    BEAM_DISCLOSURE = BEAM_PROSE + " It may disclose it to law enforcement on request."
    GLM_SHARING = GLM_PROSE + " We may share your prompts with our partners."
    BEAM_KEEPS_30D = ("Beam Free is free on OpenCode for a limited time. "
                      "Its provider keeps your prompts for 30 days.")
    GLM_MIXED = ("GLM-5.3-Flash Free is free on OpenCode for a limited time. Its "
                 "provider follows a zero-retention policy and may use your data for "
                 "model training.")

    def test_same_class_retention_reword_is_not_silent(self):
        """The defect: 0 events before the fix, a quoted wording change after."""
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})}),
            state(policy={"opencode": policy_map(**{"beam-free": self.BEAM_DISCLOSURE})}),
            catalog({}), config(),
        )
        self.assertEqual([e["kind"] for e in events], ["policy_changed"])
        self.assertFalse(events[0]["class_changed"])
        self.assertTrue(events[0]["wording_changed"])
        text = watch.render(events, config())
        self.assertIn('Now: "%s"' % watch.wrap(self.BEAM_DISCLOSURE), text)
        self.assertIn('Before: "%s"' % watch.wrap(BEAM_PROSE), text)

    def test_same_class_reword_reports_wording_not_a_downgrade(self):
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})}),
            state(policy={"opencode": policy_map(**{"beam-free": self.BEAM_KEEPS_30D})}),
            catalog({}), config(),
        )
        text = watch.render(events, config())
        self.assertIn("What it covers has not changed - it still keeps your data for "
                      "30 days - but the wording has.", text)
        self.assertNotIn("Before, it kept", text)
        self.assertNotIn("Now it does not say what it does with your data", text)

    def test_sharing_clause_alone_fires(self):
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": self.GLM_SHARING})}),
            catalog({}), config(),
        )
        self.assertEqual([e["kind"] for e in events], ["policy_changed"])

    def test_same_class_reword_names_the_sentence_that_changed(self):
        """An added clause is the change; the class sentence did not move.

        Naming the class-carrying sentence here pointed the owner at the one
        line that was left untouched, which is the opposite of what AC-19 asks.
        """
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})}),
            state(policy={"opencode": policy_map(**{"beam-free": self.BEAM_DISCLOSURE})}),
            catalog({}), config(),
        )
        text = watch.render(events, config())
        self.assertIn('The sentence carrying the change: "%s"'
                      % "It may disclose it to law enforcement on request.", text)
        self.assertNotIn('The sentence carrying the change: "%s"'
                         % "Its provider does not use your data for model training and "
                           "retains it for 30 days for safety reasons.", text)

    def test_sharing_clause_names_the_added_sentence(self):
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": self.GLM_SHARING})}),
            catalog({}), config(),
        )
        self.assertIn('The sentence carrying the change: "%s"'
                      % "We may share your prompts with our partners.",
                      watch.render(events, config()))

    def test_marketing_reword_stays_silent(self):
        """INV-18 still holds: only the privacy sentences are compared."""
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})}),
            state(policy={"opencode": policy_map(**{
                "beam-free": BEAM_PROSE.replace("for a limited time", "for a short time")})}),
            catalog({}), config(),
        )
        self.assertEqual(events, [])

    def test_new_model_that_may_use_your_data_for_training_fires(self):
        """AC-15 defect (a): this wording classified `unstated` and stayed silent."""
        prose = ("Nova Free is free on OpenCode for a limited time. Its provider may "
                 "use your data for model training.")
        self.assertEqual(watch.classify(prose), "trains-or-logs")
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE, "nova-free": prose})}),
            catalog({}), config(),
        )
        self.assertEqual([e["kind"] for e in events], ["policy_new_bad"])

    def test_retention_window_is_never_reported_as_unstated(self):
        """AC-25 defect (b): the alert must not deny what the quoted text states."""
        for prose in (self.BEAM_KEEPS_30D,
                      "Beam Free is free on OpenCode. It stores your prompts for 30 days.",
                      "Beam Free is free on OpenCode. It follows a 30-day retention policy."):
            cls = watch.classify(prose)
            self.assertEqual(cls, "retained-30d", prose)
            events = watch.diff(
                state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})}),
                state(policy={"opencode": policy_map(**{"beam-free": prose})}),
                catalog({}), config(),
            )
            self.assertNotIn("does not say what it does with your data",
                             watch.render(events, config()))

    def test_mixed_statement_is_not_reported_as_zero_retention(self):
        """AC-25 defect (c): a worse posture must never read as the better class."""
        self.assertEqual(watch.classify(self.GLM_MIXED), "trains-or-logs")
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": self.GLM_MIXED})}),
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            catalog({}), config(),
        )
        self.assertEqual([e["kind"] for e in events], ["policy_changed"])
        self.assertEqual(events[0]["class_before"], "trains-or-logs")
        self.assertEqual(events[0]["class_after"], "zero-retention")

    def test_negated_training_clause_does_not_suppress_a_retention_window(self):
        self.assertEqual(
            watch.classify("It does not use your data for model training and retains "
                           "it for 30 days for safety reasons."),
            "retained-30d")
        self.assertEqual(
            watch.classify("Its provider follows a zero-retention policy and does not "
                           "use your data for model training."),
            "zero-retention")

    def test_common_training_phrasings_are_all_recognised(self):
        for prose in (
            "Its provider may use your data for model training.",
            "The provider will use your data to train its models.",
            "It trains on your data.",
            "The provider may train on your prompts.",
            "We may use your prompts to improve the model.",
            "It collects feedback and improves the model.",
        ):
            self.assertEqual(watch.classify(prose), "trains-or-logs", prose)

    def test_common_retention_phrasings_are_all_recognised(self):
        cases = {
            "Its provider keeps your prompts for 30 days.": "retained-30d",
            "It stores your prompts for 30 days.": "retained-30d",
            "We hold your data for 30 days.": "retained-30d",
            "Your prompts are kept for 30 days.": "retained-30d",
            "It follows a 30-day retention policy.": "retained-30d",
            "It follows a 30 days retention policy.": "retained-30d",
            "The provider deletes your data after 7 days.": "retained-7d",
        }
        for prose, expected in cases.items():
            self.assertEqual(watch.classify(prose), expected, prose)

    def test_retention_windows_in_other_units_are_recognised(self):
        """AC-25: a window in hours, weeks or months is still a stated window.

        Before this fix those phrasings classified `unstated`, so the alert
        printed "it still does not say what it does with your data" directly
        above the quote that says 12 hours.
        """
        cases = {
            "It keeps your prompts for 24 hours.": "retained-24h",
            "We store your data for 2 weeks.": "retained-2w",
            "It retains your data for 3 months.": "retained-3mo",
            "It follows a 2 weeks retention policy.": "retained-2w",
            "It deletes your data after 90 days.": "retained-90d",
            "It wipes your data after 72 hours.": "retained-72h",
            "It purges your prompts after 3 months.": "retained-3mo",
            "The data is stored for 48 hours.": "retained-48h",
            "It holds your inputs for 6 months.": "retained-6mo",
            "It is retained for 90 days.": "retained-90d",
        }
        for prose, expected in cases.items():
            self.assertEqual(watch.classify(prose), expected, prose)

    def test_window_in_hours_is_never_reported_as_unstated(self):
        """AC-25: never deny the window the quoted text states, in any unit."""
        events = watch.diff(
            state(policy={"opencode": policy_map(**{
                "beam-free": "Beam Free is free. It keeps your prompts for 24 hours."})}),
            state(policy={"opencode": policy_map(**{
                "beam-free": "Beam Free is free. It keeps your prompts for 12 hours."})}),
            catalog({}), config(),
        )
        text = watch.render(events, config())
        self.assertNotIn("does not say what it does with your data", text)
        self.assertIn("Before, it kept your data for 24 hours.", text)
        self.assertIn("Now it keeps your data for 12 hours.", text)
        self.assertIn('The sentence carrying the change: "It keeps your prompts for '
                      '12 hours."', text)

    def test_month_window_renders_in_months(self):
        """The class clause speaks the unit the provider used, not days."""
        self.assertEqual(watch.class_clause("retained-3mo"), "keeps your data for 3 months")
        self.assertEqual(watch.class_clause("retained-1mo"), "keeps your data for 1 month")
        self.assertEqual(watch.class_clause("retained-2w", "past"),
                         "kept your data for 2 weeks")
        self.assertEqual(watch.severity("retained-2w"), watch.CLASS_SEVERITY["retained"])

    def test_stated_refusal_to_keep_data_is_not_unstated(self):
        """AC-25: a stated no-copy promise is a posture, not silence.

        `unstated` renders "it does not say what it does with your data", which
        is false over prose that says it keeps nothing. AC-23's severity order
        also ranks zero-retention as the BEST class, so calling a refusal
        `unstated` made a genuine improvement look like an unknown.
        """
        cases = {
            "We do not retain your data.": "zero-retention",
            "We never store your prompts.": "zero-retention",
            "We do not keep any content.": "zero-retention",
            "It deletes your data immediately.": "zero-retention",
            "No data retention is applied.": "zero-retention",
        }
        for prose, expected in cases.items():
            self.assertEqual(watch.classify(prose), expected, prose)
            self.assertNotIn("does not say what it does with your data",
                             watch.render([{
                                 "kind": "policy_changed", "provider": "opencode",
                                 "model": "x-free", "watched": True,
                                 "text_before": "It keeps your data for 30 days.",
                                 "text_after": prose,
                                 "class_before": "retained-30d",
                                 "class_after": expected}], config()), prose)

    def test_negated_window_cap_is_read_as_the_cap_not_as_zero(self):
        """"not ... more than 30 days" promises 30 days, not zero.

        Reading it as zero-retention would overstate the provider's promise -
        the same fabrication class in the opposite direction.
        """
        prose = "We do not retain your data for more than 30 days."
        self.assertEqual(watch.classify(prose), "retained-30d")
        self.assertEqual(watch.retention_window(prose.lower()), (30, "d"))

    def test_unchanged_negated_training_clause_stays_retained(self):
        """A negated training clause must not become a training claim."""
        self.assertEqual(
            watch.classify("Its provider does not use your data for model training "
                           "and retains it for 30 days for safety reasons."),
            "retained-30d")

    def test_refusal_alert_names_the_refusal_sentence(self):
        """The named sentence must be the promise, not the sentence before it.

        A refusal sits after a marketing sentence, so falling back to the first
        sentence pointed the owner at the one line that did not change.
        """
        events = watch.diff(
            state(policy={"opencode": policy_map(**{
                "beam-free": "Beam Free is free for a limited time. "
                             "Its provider keeps your prompts for 30 days."})}),
            state(policy={"opencode": policy_map(**{
                "beam-free": "Beam Free is free for a limited time. "
                             "Its provider does not retain your data."})}),
            catalog({}), config(),
        )
        text = watch.render(events, config())
        self.assertIn('The sentence carrying the change: "Its provider does not '
                      'retain your data."', text)
        self.assertIn("Now it keeps no copy of your data", text)


class StructuralPrivacyRuleTests(unittest.TestCase):
    """MW-PROSE-DIFF-ABOVE-TERMS / MW-RETENTION-ALERT-FALSE-CLAIM.

    The reviewer measured that the shipped prose diff was a keyword list: any
    sentence containing "day", "keep", "log", "share" or "transfer" counted as a
    retention statement, so appended marketing copy fired
    "A provider changed its data-retention wording". These fixtures are that
    probe, plus the structural rule that replaces it.
    """

    # The reviewer's seven measured false positives, verbatim.
    CHURN = (
        "Try it free for 7 days.",
        "Share this model with a friend.",
        "Keep an eye on this page for updates.",
        "Log in to see your usage.",
        "Log in to see more.",
        "Availability is announced on the blog.",
        "Scroll down for the full list of days and times.",
        # The wider churn list the review measured 11 of 12 against: the same
        # class of non-retention copy, so the rule cannot regress to a keyword.
        "Sign up today and get started in minutes.",
        "Terms and conditions apply. See the pricing page.",
        "This model is available in 12 regions worldwide.",
        "Read the docs for setup instructions.",
        "Download the desktop app for macOS.",
        "Follow us on X for release notes.",
        "Pricing is shown per million tokens below.",
        "The table below lists every free model.",
        "We store your login session in a cookie.",
        "Cookies help us improve the experience.",
        "Data centres are located in the EU and US.",
        "Retention policies vary; see the provider's page.",
        "Model weights are open and available on Hugging Face.",
        "Billing runs monthly and can be cancelled any time.",
        "This page was updated on 1 November.",
    )

    def test_no_appended_marketing_sentence_fires_a_retention_alert(self):
        for sentence in self.CHURN:
            with self.subTest(sentence=sentence):
                self.assertFalse(watch.is_retention_sentence(sentence))
                edited = GLM_PROSE + " " + sentence
                events = watch.diff(
                    state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
                    state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": edited})}),
                    catalog({}), config(),
                )
                self.assertEqual(events, [])

    def test_retention_sentence_needs_the_act_and_its_object(self):
        """The general rule, stated: an act applied to the reader's data."""
        yes = (
            "Its provider keeps your prompts for 30 days.",
            "We log your requests.",
            "It archives your conversations.",
            "We may share your inputs with partners.",
            "It deletes your data after 7 days.",
            "The provider trains on your prompts.",
            "Its provider follows a zero-retention policy.",
            "We do not retain your data.",
            "The retention period is 30 days.",
        )
        no = self.CHURN + (
            "Models Pricing Docs Blog Careers.",
            "Contact us if you have any questions.",
            "OPENCODE_DISABLE_FREE_MODELS=1",
            "The model is free for a limited time.",
            "Two new models were added this week.",
            "Read the documentation before you start.",
        )
        for sentence in yes:
            self.assertTrue(watch.is_retention_sentence(sentence), sentence)
        for sentence in no:
            self.assertFalse(watch.is_retention_sentence(sentence), sentence)

    # The noun "provider" is not the verb "provide". The disclosure pattern
    # carried no trailing word boundary, so "provid(?:e|es|ed|ing)" matched
    # inside "provider" and every ordinary note about a provider read as a
    # disclosure act applied to the reader's data.
    PROVIDER_NOTES = (
        "This provider supports prompt caching.",
        "Requests are sent to the fastest available provider.",
        "The provider may rate limit requests.",
        "Requests are served by the nearest provider.",
        "The provider returns one response per request.",
        "The provider is available in three regions.",
        "The provider supports streaming responses.",
        "Prompts are tokenised before they reach the provider.",
        "The provider charges nothing for these models.",
        "This provider requires an account.",
        "Usage is metered per request by the provider.",
        "The provider offers a 200k context window.",
        "Responses are cached by the provider for speed.",
        "The provider is reachable over HTTPS only.",
        "Every request is routed to a provider.",
        "The provider rotates its endpoints weekly.",
        "Prompts may contain images, which the provider rejects.",
        "The provider lists these models as experimental.",
        "Your prompts are compiled before the provider sees them.",
        "The provider publishes its status on a separate page.",
    )

    def test_the_noun_provider_is_not_a_disclosure_act(self):
        """MW-RETENTION-RULE-PROVIDER-SUBSTRING: no alert from page churn."""
        for note in self.PROVIDER_NOTES:
            with self.subTest(note=note):
                self.assertFalse(watch.is_retention_sentence(note), note)

    def test_a_provider_note_appended_to_a_live_line_is_silent(self):
        """The consequence the reviewer measured, on a real retained model line."""
        base = ("GLM-5.3-Flash Free is free on OpenCode for a limited time. "
                "Its provider retains your data for 30 days for safety reasons.")
        for note in self.PROVIDER_NOTES:
            with self.subTest(note=note):
                events = watch.diff(
                    state(policy={"opencode": policy_map(**{"beam-free": base})}),
                    state(policy={"opencode": policy_map(**{"beam-free": base + " " + note})}),
                    catalog({}), config(),
                )
                self.assertEqual(events, [], note)

    def test_real_disclosure_verbs_still_fire(self):
        """Word-bounding the acts must not silence the acts themselves."""
        for sentence in ("We share your prompts with partners.",
                         "We provide your data to third parties.",
                         "It transfers your data to affiliates.",
                         "We sold your data last year.",
                         "It discloses your conversations to law enforcement."):
            with self.subTest(sentence=sentence):
                self.assertTrue(watch.is_retention_sentence(sentence), sentence)

    def test_territory_list_edit_is_silent(self):
        """The country list is the page's most churn-prone copy and states nothing."""
        before = BEAM_PROSE + (" It is not available in Angola, Belarus, Cambodia "
                               "and Cuba.")
        after = (BEAM_PROSE + " It is not available in Angola, Belarus, Cambodia, "
                 "Cuba and Zambia.")
        self.assertEqual(watch.retention_sentences(before),
                         watch.retention_sentences(after))
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"beam-free": before})}),
            state(policy={"opencode": policy_map(**{"beam-free": after})}),
            catalog({}), config(),
        )
        self.assertEqual(events, [])

    def test_training_sentence_reword_still_fires(self):
        """A reworded training promise is a privacy change, not page churn."""
        before = TRAINS_PROSE
        after = TRAINS_PROSE.replace("collect feedback and improve the model",
                                     "collect feedback to improve its models")
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"flint-40b-free": before})}),
            state(policy={"opencode": policy_map(**{"flint-40b-free": after})}),
            catalog({}), config(),
        )
        self.assertEqual([e["kind"] for e in events], ["policy_changed"])
        carrier = ("The team is using this time to collect feedback "
                   "to improve its models.")
        self.assertIn('The sentence carrying the change: "%s"' % carrier,
                      watch.render(events, config()).replace("\n", " "))

    def test_the_junk_strings_are_excluded_by_the_rule_not_a_blacklist(self):
        """AC-13 in spirit: the exclusion must be structural, not enumerative."""
        import inspect
        source = inspect.getsource(watch.is_retention_sentence)
        for junk in ("see", "free-models", "to-hide-free-models-in-opencode-set",
                     "opencode-disable-free-models-1"):
            self.assertNotIn(junk, source)
        for junk in JunkKeyTests.FURNITURE:
            self.assertFalse(watch.is_retention_sentence(junk), junk)

    def test_a_marketing_only_edit_never_names_itself_as_the_changed_sentence(self):
        """AC-25: no claim about his data when the provider made none."""
        edited = GLM_PROSE + " Try it free for 7 days."
        self.assertEqual(watch.changed_sentence(GLM_PROSE, edited), "")
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": edited})}),
            catalog({}), config(),
        )
        self.assertEqual(events, [])
        # The class is unchanged either way, so a render of the old event shape
        # must not carry a "sentence carrying the change" line for marketing.
        self.assertNotIn("Try it free for 7 days", watch.render([{
            "kind": "policy_changed", "provider": "opencode",
            "model": "glm-5-3-flash-free", "watched": True,
            "class_before": "zero-retention", "class_after": "zero-retention",
            "class_changed": False, "wording_changed": False,
            "text_before": GLM_PROSE, "text_after": edited}], config()))

    def test_a_genuine_retention_edit_still_fires_and_quotes_the_new_wording(self):
        """The fix must not silence the signal it was meant to protect."""
        edited = GLM_PROSE + " It keeps your prompts for 30 days for safety."
        events = watch.diff(
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
            state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": edited})}),
            catalog({}), config(),
        )
        self.assertEqual(len(events), 1)
        text = watch.render(events, config())
        self.assertIn('The sentence carrying the change: "It keeps your prompts for '
                      '30 days for safety."', text)

    def test_common_qualified_window_phrasings_are_never_unstated(self):
        """MW-CLASSIFIER-STILL-UNSTATED-ON-COMMON-WINDOWS, each phrase verbatim."""
        cases = {
            "It retains prompts for up to 30 days.": "retained-30d",
            "It keeps prompts for at most 30 days.": "retained-30d",
            "Its retention period is 30 days.": "retained-30d",
            "Prompts are purged within 30 days.": "retained-30d",
            "It keeps prompts for no more than 30 days.": "retained-30d",
            "It retains prompts for less than 30 days.": "retained-30d",
            "The provider's retention period is 40 days.": "retained-40d",
            "Retention window: 2 weeks.": "retained-2w",
        }
        for prose, expected in cases.items():
            self.assertEqual(watch.classify(prose), expected, prose)
            events = watch.diff(
                state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})}),
                state(policy={"opencode": policy_map(**{"beam-free": prose})}),
                catalog({}), config(),
            )
            self.assertNotIn("does not say what it does with your data",
                             watch.render(events, config()), prose)

    def test_logged_class_carrier_is_the_privacy_sentence(self):
        """MW-LOGGED-CARRIER-WRONG: keys on the prose's own 'logs', not 'logged'."""
        cases = (
            "GLM-5.3-Flash Free is free on OpenCode for a limited time. "
            "Its provider logs your prompts.",
            "Beam Free is free for a limited time. "
            "Its provider logs requests for abuse prevention.",
            "Flint 40B Free is free for a limited time. Its provider archives prompts.",
        )
        for prose in cases:
            with self.subTest(prose=prose):
                self.assertEqual(watch.classify(prose), "logged")
                carrier = watch.carrier_sentence(prose, "logged")
                self.assertNotIn("is free on OpenCode", carrier)
                self.assertTrue(watch.is_retention_sentence(carrier), carrier)


# ------------------------------------------------------------------ junk keys

class JunkKeyTests(unittest.TestCase):
    JUNK = ["", "see", "free-models", "to-hide-free-models-in-opencode-set",
            "opencode-disable-free-models-1"]
    FURNITURE = [
        "To hide free models in OpenCode, set",
        "OPENCODE_DISABLE_FREE_MODELS=1",
        ". See",
        "free models",
        ".",
        "Contact us",
        "if you have any questions.",
        "Auto-recharge",
        "Pricing",
        "All models are subject to the terms of service",
    ]

    def test_page_furniture_never_becomes_a_key(self):
        lines = self.FURNITURE + [
            GLM_PROSE, BEAM_PROSE, TRAINS_PROSE,
            "Jev 1.13 Free is available on OpenCode for a limited time.",
        ]
        # The run's identity data, as collect() supplies it: the catalogue names
        # every one of these. Without corroboration nothing is a model at all -
        # that is the rule now - so the fixture must carry the real evidence.
        keys = set(watch.extract_policy(
            lines, known={"glm-5-3-flash-free", "beam-free", "flint-40b-free",
                          "jev-1-13-free"}))
        self.assertEqual(keys, {"glm-5-3-flash-free", "beam-free", "flint-40b-free",
                                "jev-1-13-free"})
        for junk in self.JUNK:
            self.assertNotIn(junk, keys)

    def test_rule_is_structural_not_a_blacklist(self):
        """Future furniture must be excluded by shape, not by a known-string list."""
        future = [
            "Read more about how we handle your data",
            "Set OPENCODE_FREE_TIER=off to disable",
            "This page is not available in your region",
            "Deprecated models",
        ]
        self.assertEqual(watch.extract_policy(future), {})
        self.assertEqual(watch.extract_policy(self.FURNITURE), {})

    def test_real_looking_entry_is_kept(self):
        """A plain-worded name is kept, corroborated by the run's own identity.

        It is NOT admitted on shape any more: the shape-only route admitted
        "All models", "Section 3" and "OpenCode Console" in the same position.
        The catalogue names this model, which is the evidence a real entry has.
        """
        line = "North Mini Code Free is free on OpenCode for a limited time."
        self.assertEqual(set(watch.extract_policy([line])), set())
        self.assertEqual(
            set(watch.extract_policy([line], known={"north-mini-code-free"})),
            {"north-mini-code-free"})

    def test_real_captured_page_extracts_only_models(self):
        with offline() as seen:
            _snap, _cat, meta = watch.collect(config(), {})
        self.assertTrue(meta["policy_ok"])
        keys = set(_snap["policy"]["opencode"])
        self.assertEqual(keys, {"glm-5-3-flash-free", "beam-free", "flint-40b-free"})
        self.assertNotIn("openrouter", " ".join(seen))

    def test_free_clause_rewording_never_drops_a_real_entry(self):
        """MW-EXTRACTOR-PHRASE-WHITELIST-TO-FALSE-REMOVAL.

        A literal phrase allowlist admitted only the exact free clauses on the
        page today. Rewording the opener alone left the retention sentence
        untouched but dropped the entry, and the next run then reported the
        model as "no longer described" while the page still said it verbatim.
        """
        tail = " Its provider retains your data for 30 days for safety reasons."
        openers = (
            "GLM-5.3-Flash Free is free on OpenCode for a limited time.",
            "GLM-5.3-Flash Free. Free to use on OpenCode for a limited time.",
            "GLM-5.3-Flash Free is free to use on OpenCode for a limited time.",
            "GLM-5.3-Flash Free is available on OpenCode at no cost for a limited time.",
            "GLM-5.3-Flash Free is offered free of charge on OpenCode for a limited time.",
            "GLM-5.3-Flash Free is free on OpenCode for a short while.",
            "GLM-5.3-Flash Free is currently free on OpenCode for a limited time.",
            # Predicates the copula split did not know about. Heading on the
            # copula verb made the whole first sentence the name, so these
            # dropped the entry and fired a false "no longer described".
            "GLM-5.3-Flash Free may be used at no cost on OpenCode for a limited time.",
            "GLM-5.3-Flash Free costs nothing on OpenCode for a limited time.",
            "GLM-5.3-Flash Free can be used for free on OpenCode for a limited time.",
            "Foo Free. Its provider retains your data for 30 days.",
        )
        # "Foo Free" is not a model the run knows, and no longer a name on
        # shape alone; every real opener here names an identity the catalogue
        # holds, which is the evidence the rule requires.
        known = GLM_KNOWN | {"foo-free"}
        for opener in openers:
            with self.subTest(opener=opener):
                got = watch.extract_policy([opener + tail], known=known)
                self.assertEqual(len(got), 1, opener)
                self.assertIn("retains your data for 30 days", list(got.values())[0])

    def test_the_reader_data_is_never_a_model_name(self):
        """A line whose subject is the data states a posture, not a model."""
        for line in ("Requests are retained for 30 days.",
                     "Your prompts are stored for 30 days.",
                     "All data is purged after 30 days.",
                     "Chat logs are archived for 30 days."):
            with self.subTest(line=line):
                self.assertTrue(watch.is_retention_sentence(line), line)
                self.assertEqual(watch.extract_policy([line]), {}, line)

    def test_any_free_predicate_keeps_the_name_it_follows(self):
        """The head is the name, whatever the clause says after it.

        A copula allowlist made the whole first sentence the head whenever the
        free clause used a verb it did not list ("costs nothing", "may be used
        at no cost", "can be used for free"), so the entry vanished and the next
        run reported the model as no longer described.
        """
        openers = (
            "GLM-5.3-Flash Free may be used at no cost on OpenCode for a limited time.",
            "GLM-5.3-Flash Free costs nothing on OpenCode for a limited time.",
            "GLM-5.3-Flash Free can be used for free on OpenCode for a limited time.",
            "GLM-5.3-Flash Free comes at no charge on OpenCode for a limited time.",
            "GLM-5.3-Flash Free remains free on OpenCode for a limited time.",
            "GLM-5.3-Flash Free - free to use on OpenCode for a limited time.",
        )
        for opener in openers:
            with self.subTest(opener=opener):
                got = watch.extract_policy([opener + " Its provider retains your "
                                            "data for 30 days for safety reasons."],
                                           known={"glm-5-3-flash-free"})
                self.assertEqual(list(got), ["glm-5-3-flash-free"], opener)

    def test_reworded_free_clause_does_not_read_as_removed(self):
        """The consequence, not just the extraction: no false 'removed' alert."""
        old = "GLM-5.3-Flash Free is free on OpenCode for a limited time." \
              " Its provider retains your data for 30 days for safety reasons."
        new = "GLM-5.3-Flash Free. Free to use on OpenCode for a limited time." \
              " Its provider retains your data for 30 days for safety reasons."
        known = {"glm-5-3-flash-free"}
        before = watch.extract_policy([old], known=known)
        after = watch.extract_policy([new], known=known)
        self.assertEqual(set(before), set(after))
        self.assertEqual("retained-30d", watch.classify(list(after.values())[0]))
        events = watch.diff(
            state(free={"opencode": ["glm-5-3-flash-free"]},
                  policy={"opencode": policy_map(**before)}),
            state(free={"opencode": ["glm-5-3-flash-free"]},
                  policy={"opencode": policy_map(**after)}),
            catalog({"glm-5.3-flash-free": model()}), config(),
        )
        self.assertEqual(events, [])

    def test_entry_split_across_lines_is_joined(self):
        """A name on one line and its prose on the next is one entry."""
        got = watch.extract_policy([
            "North Mini Code Free", "is free on OpenCode for a limited time.",
        ], known={"north-mini-code-free"})
        self.assertEqual(set(got), {"north-mini-code-free"})

    # MW-EXTRACTOR-ADMITS-SECTION-PROSE. "A model name carries a capital" was
    # satisfied by any single capitalised common word, so a section label keyed
    # as a model and fired a retention alert naming a model the source never
    # states (AC-13, AC-25).
    SECTION_PROSE = (
        "Update: data is retained for 30 days.",
        "Warning: requests are logged for abuse monitoring.",
        "Limits: requests are retained for 30 days.",
        "Storage: prompts are kept for 30 days.",
        "Notes: data is retained for 30 days.",
        "During the beta, requests are retained for 30 days.",
        "Monthly Limits: requests are retained for 30 days.",
        "Retention Policy: prompts are kept for 30 days.",
        "Deprecated Models: requests are retained for 30 days.",
        "Retention",
    )
    # The heading pair the reviewer measured as keys "retention-free"/"during".
    HEADING_PAIRS = (
        ("Retention", "Free models retain requests for 30 days."),
        ("Retention", "Free models from stealth providers"),
        ("Limits", "Requests are retained for 30 days."),
    )

    def test_section_prose_never_becomes_a_policy_key(self):
        """AC-13: an entry must structurally name a model, not a heading."""
        known = {"beam-free", "glm-5-3-flash-free"}
        for line in self.SECTION_PROSE:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)

    def test_section_heading_pair_never_becomes_a_key(self):
        known = {"beam-free", "glm-5-3-flash-free"}
        for pair in self.HEADING_PAIRS:
            with self.subTest(pair=pair):
                self.assertEqual(watch.extract_policy(list(pair), known=known), {}, pair)

    def test_section_prose_alerts_nothing_against_a_seeded_baseline(self):
        """The consequence, and the trap: an EMPTY baseline skips the check.

        `diff()` only compares policy maps when the previous snapshot has one,
        so a test seeded with nothing passes whatever the extractor does. The
        baseline here holds the real GLM statement.
        """
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in self.SECTION_PROSE:
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                events = watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config(),
                )
                self.assertEqual(events, [], line)

    def test_section_prose_inserted_into_the_live_page_yields_only_real_models(self):
        """The reviewer's reproduction, through the real opencode_policy_text().

        Compared against the SAME extractor's reading of the clean page, not
        against a list of expected junk keys: the pre-change code keyed the
        inserted line as "update-data", which a blacklist of "update" would
        have missed, and a version-independent comparison cannot be outgrown.
        """
        with offline():
            _url, clean = live_prose()
            self.assertTrue(clean)
            for line in self.SECTION_PROSE:
                with self.subTest(line=line):
                    page = DOC_PAGE.replace(
                        "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                    with offline({watch.OPENCODE_DOCS[0]: page}):
                        _url2, prose = live_prose()
                    self.assertEqual(sorted(prose), sorted(clean), line)

    def test_a_plain_worded_model_name_is_kept_when_the_run_knows_it(self):
        """The stricter rule is corroborated, not a vocabulary.

        A model whose name is ordinary words must still be extracted when the
        catalog or the previous snapshot carries it as an identity, so the rule
        cannot become a word list in either direction.
        """
        line = "Mystery Model: During its free period, collected data is retained for 30 days."
        self.assertEqual(watch.extract_policy([line]), {})
        known = watch.policy_identities({"opencode": {"mystery-model": {}}}, {}, ["opencode"])
        observed = watch.observed_identities({"opencode": {"mystery-model": {}}}, ["opencode"])
        self.assertEqual(
            set(watch.extract_policy([line], known=known, observed=observed)),
            {"mystery-model"})

    # The residual the round-9 fix left: "any single capitalised common word"
    # was fixed, but a head of SEVERAL plain capitalised words was not, and a
    # heading set in bold in the same paragraph as its prose renders exactly
    # that way ("Retention Free tier users' data is retained ...").
    INLINE_HEADINGS = (
        "Retention Free tier users' data is retained for 30 days.",
        "Pricing Free tier requests are retained for 30 days.",
        "Retention Free-tier requests are retained for 30 days.",
        "Limits Free models are retained for 30 days.",
        "Storage Free users' prompts are kept for 30 days.",
        "Notes Users' data is retained for 30 days.",
        "Privacy Policy Your data is retained for 30 days.",
        "Usage Policy All requests are retained for 30 days.",
        "Terms of Service Data is retained for 30 days.",
        "Retention Policy Prompts are retained for 30 days.",
        "Data Retention Prompts are kept for 30 days.",
    )

    def test_inline_heading_never_becomes_a_policy_key(self):
        """A heading inlined with its prose is not a model name either."""
        known = {"beam-free", "glm-5-3-flash-free"}
        for line in self.INLINE_HEADINGS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)

    def test_a_heading_is_not_joined_onto_the_sentence_it_introduces(self):
        """The join route must not rebuild the same wrong key.

        A heading on one line and its prose on the next is the same furniture
        in a different shape, so the continuation has to open with the claim
        itself. A real model that wraps is still joined (asserted above).
        """
        known = {"beam-free", "glm-5-3-flash-free"}
        pairs = (
            ("Retention Policy", "Prompts are retained for 30 days."),
            ("Data Retention", "Prompts are kept for 30 days."),
            ("Pricing", "Free tier requests are retained for 30 days."),
        )
        for pair in pairs:
            with self.subTest(pair=pair):
                self.assertEqual(watch.extract_policy(list(pair), known=known), {}, pair)

    def test_a_real_model_wrapping_onto_a_predicate_is_still_joined(self):
        """The other direction: the join rule did not close the real entry."""
        got = watch.extract_policy([
            "MiMo-V2.6-Flash Free", "is available on OpenCode for a limited time.",
        ])
        self.assertEqual(set(got), {"mimo-v2-6-flash-free"})

    def test_inline_heading_alerts_nothing_against_a_seeded_baseline(self):
        """AC-25: no alert may name "Retention Free" as a model."""
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in self.INLINE_HEADINGS:
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                events = watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config(),
                )
                self.assertEqual(events, [], line)

    def test_inline_heading_in_the_live_page_adds_no_key(self):
        """The same insertions through the real opencode_policy_text()."""
        with offline():
            _url, clean = live_prose()
            self.assertTrue(clean)
            for line in self.INLINE_HEADINGS:
                with self.subTest(line=line):
                    page = DOC_PAGE.replace(
                        "<li>" + BEAM_PROSE, "<li>%s</li><li>%s" % (line, BEAM_PROSE))
                    with offline({watch.OPENCODE_DOCS[0]: page}):
                        _url2, prose = live_prose()
                    self.assertEqual(sorted(prose), sorted(clean), line)

    # The remaining accept route after the plain-word rule: a NUMBERED section
    # label carries a digit, and a digit is a distinctive shape, so the shape
    # shortcut admitted "Section 3:", "Changelog 2025:" and "Table 2:" while the
    # data-subject veto sat behind it. Same defect, same class (AC-13, AC-25).
    NUMBERED_LABELS = (
        "Section 3: requests are retained for 30 days.",
        "Section 3 Requests are retained for 30 days.",
        "Q1 2026: data is retained for 30 days.",
        "2025 Requests are retained for 30 days.",
        "Changelog 2025: requests are logged for 30 days.",
        "Table 2: requests are retained for 30 days.",
        "v2 Policy: data is retained for 30 days.",
        "Step 1 Requests are retained for 30 days.",
        "FAQ 2 Prompts are kept for 30 days.",
        "Update 2: data is retained for 30 days.",
        "Limits 2026: requests are retained for 30 days.",
    )

    def test_numbered_section_label_never_becomes_a_policy_key(self):
        """A digit numbers a heading; it does not make a heading a model."""
        known = {"beam-free", "glm-5-3-flash-free"}
        for line in self.NUMBERED_LABELS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)

    def test_numbered_section_label_alerts_nothing_against_a_seeded_baseline(self):
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in self.NUMBERED_LABELS:
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                events = watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config(),
                )
                self.assertEqual(events, [], line)

    def test_a_versioned_model_label_is_still_kept(self):
        """The other direction: a dotted version IS part of a real name."""
        known = {"glm-5-3-flash-free", "jev-1-13-free"}
        for line, key in (
            ("GLM-5.3-Flash Free: Prompts are retained for 30 days.", "glm-5-3-flash-free"),
            ("Jev 1.13: Prompts are retained for 30 days.", "jev-1-13"),
        ):
            with self.subTest(line=line):
                self.assertEqual(set(watch.extract_policy([line], known=known)), {key})

    def test_a_known_model_whose_name_carries_a_data_word_is_still_kept(self):
        """The data-subject veto cannot swallow a real, already-known model."""
        line = ("Beam Free Pricing is free on OpenCode for a limited time. "
                "Its provider retains your data for 30 days.")
        known = {"beam-free-pricing"}
        self.assertEqual(set(watch.extract_policy([line], known=known)), {"beam-free-pricing"})

    # MW-EXTRACTOR-LABEL-VERSION-ROUTE. The fourth shape of the same class. Each
    # earlier fix tightened the shape a LABEL had to carry and left the next one
    # open: any digit closed "Section 3:", a dotted version closed "3.1:", and
    # both were still numbers, which is how a page numbers its headings. The
    # rule is now the same on BOTH routes: a known identity, or an internal
    # capital. A number of any shape accepts nothing.
    NUMBERED_VERSION_LABELS = (
        "3.1 Retention terms: requests are retained for 30 days.",
        "Section 3.1: requests are retained for 30 days.",
        "Table 4.2: requests are retained for 30 days.",
        "Figure 2.1: requests are retained for 30 days.",
        "Step 4.2: requests are retained for 30 days.",
        "Changelog v1.2: requests are logged for 30 days.",
        "v2.1 Changes: data is retained for 30 days.",
        "2026.11 Update: data is retained for 30 days.",
        "Release 3.0: data is retained for 30 days.",
        "Beta 1.2: data is retained for 30 days.",
        "API v3.1: data is retained for 30 days.",
        "5.2 Privacy: requests are retained for 30 days.",
        "1.1 Free models: requests are retained for 30 days.",
        "2.4 Data retention: prompts are kept for 30 days.",
        "3.1: requests are retained for 30 days.",
    )

    # Round 16: with the shape-only route gone, a numbered head could still
    # reach an accept route through the internal capital a digit gives it. A
    # bare counter at a head BOUNDARY is numbering; a name keeps its number in
    # the middle or as a trailing version. These are the boundary forms, and
    # they are the ones the rule closes. A dotted number at the BACK
    # ("Figure 2.1") and a number in the MIDDLE ("Chapter 7 Privacy") are
    # indistinguishable in form from "Jev 1.13" and "Nemotron 3 Ultra Free", so
    # they are NOT claimed here - closing them would need a vocabulary list,
    # which is the defect this whole class was about. They are reported as a
    # measured residual in verify_review16.py section 6 instead.
    NUMBERED_HEADS = (
        "Section 3 retains your data for 30 days.",
        "Section 3 is free to use on OpenCode for a limited time.",
        "Table 2: requests are retained for 30 days.",
        "Changelog 2025: requests are logged for 30 days.",
        "3.1 Retention terms: requests are retained for 30 days.",
    )

    def test_a_number_at_the_head_boundary_is_numbering_not_a_name(self):
        """MW-EXTRACTOR-SUBJECT-ROUTE-ADMITS-COLLECTIVE-PROSE, last member.

        With the shape-only route gone, a numbered head still reached an accept
        route through the internal capital a digit gives it. A name carries its
        number in the MIDDLE ("Nemotron 3 Ultra Free", "Flint 40B Free") or as a
        version at the BACK ("Jev 1.13"), never as a counter at a boundary, so
        the form decides and no list of the words a page numbers with is needed.
        """
        for line in self.NUMBERED_HEADS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line]), {}, line)
                # Harshest case: a previous snapshot holding this exact key.
                key = watch.slug(watch.policy_head(line))
                self.assertEqual(watch.extract_policy([line], known={key}), {}, line)

    def test_a_number_in_the_middle_of_a_name_is_still_a_name(self):
        """The other direction, so the rule cannot degenerate into "no digits"."""
        for line, key in (
            ("Nemotron 3 Ultra Free is free on OpenCode for a limited time.",
             "nemotron-3-ultra-free"),
            ("Flint 40B Free is free on OpenCode for a limited time.",
             "flint-40b-free"),
            ("Jev 1.13: Prompts are retained for 30 days.", "jev-1-13"),
            ("GLM-5.3-Flash Free: Prompts are retained for 30 days.",
             "glm-5-3-flash-free"),
        ):
            with self.subTest(line=line):
                known = {key, "jev-1-13-free"}
                self.assertEqual(
                    set(watch.extract_policy([line], known=known)), {key})

    def test_no_numbered_heading_in_the_live_block_adds_a_key(self):
        """The same shapes through the real opencode_policy_text()."""
        with offline():
            _url, clean = live_prose()
        self.assertTrue(clean)
        for line in self.NUMBERED_HEADS:
            with self.subTest(line=line):
                page = DOC_PAGE.replace(
                    "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                with offline({watch.OPENCODE_DOCS[0]: page}):
                    _url2, prose = live_prose()
                self.assertEqual(sorted(prose), sorted(clean), line)

    def test_a_numbered_or_versioned_heading_never_becomes_a_policy_key(self):
        """A number numbers a heading; it does not make a heading a model."""
        known = {"beam-free", "glm-5-3-flash-free"}
        for line in self.NUMBERED_VERSION_LABELS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)
                self.assertEqual(watch.extract_policy([line]), {}, line)

    def test_a_numbered_or_versioned_heading_alerts_nothing_against_a_seeded_baseline(self):
        """The empty-baseline trap: with nothing seeded, diff() skips policy.

        The baseline here holds the real GLM statement, so a spurious key would
        reach diff() and render as an alert naming a model the page never says.
        """
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in self.NUMBERED_VERSION_LABELS:
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                events = watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config(),
                )
                self.assertEqual(events, [], line)

    def test_a_numbered_heading_in_the_live_block_adds_no_key(self):
        """The reviewer's reproduction, through the real opencode_policy_text()."""
        with offline():
            _url, clean = live_prose()
            self.assertTrue(clean)
            self.assertEqual(len(clean), 3)
            for line in self.NUMBERED_VERSION_LABELS:
                with self.subTest(line=line):
                    page = DOC_PAGE.replace(
                        "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                    with offline({watch.OPENCODE_DOCS[0]: page}):
                        _url2, prose = live_prose()
                    self.assertEqual(sorted(prose), sorted(clean), line)

    def test_a_provider_name_is_never_read_as_one_of_its_own_models(self):
        """A provider heading carries an internal capital, and must still fail.

        The internal-capital route is what accepts a model the run has never
        seen ("MiniMax M2"). "OpenCode:" carries the same shape, so a page
        heading naming the provider keyed as a model called OpenCode and would
        alert about a model the page never states (MW-EXTRACTOR-PROVIDER-LABEL).
        """
        known = {"glm-5-3-flash-free", "beam-free", "jev-1-13"}
        for line in ("OpenCode: Prompts are retained for 30 days.",
                     "opencode: data is retained for 30 days.",
                     "OpenRouter: requests are retained for 30 days.",
                     "Opencode: prompts are kept for 30 days."):
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)
                self.assertEqual(watch.extract_policy([line]), {}, line)

    def test_a_provider_heading_alerts_nothing_against_a_seeded_baseline(self):
        """The seeded case: with nothing seeded diff() skips the policy compare."""
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in ("OpenCode: Prompts are retained for 30 days.",
                     "OpenRouter: requests are retained for 30 days."):
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                self.assertEqual(watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config()), [], line)

    def test_a_provider_heading_in_the_live_block_adds_no_key(self):
        """The same route end-to-end, through the real opencode_policy_text()."""
        with offline():
            _url, clean = live_prose()
            self.assertEqual(len(clean), 3)
            for line in ("OpenCode: Prompts are retained for 30 days.",
                         "OpenRouter: requests are retained for 30 days."):
                with self.subTest(line=line):
                    page = DOC_PAGE.replace(
                        "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                    with offline({watch.OPENCODE_DOCS[0]: page}):
                        _url2, prose = live_prose()
                    self.assertEqual(sorted(prose), sorted(clean), line)

    def test_the_provider_refusal_does_not_close_a_real_name(self):
        """The other direction: a model whose name holds a provider word is kept.

        A head that OPENS with a provider's name is refused by shape, because
        that is also how the page opens a section about the provider itself
        ("OpenCode Console provides a list of models ..."). Identity is judged
        first, so a real model whose name begins with the provider word is kept
        as soon as the catalogue or the previous snapshot knows it - which is
        the only evidence that such a head is a model rather than a heading.
        """
        for line, key in (
            ("OpenCode Zen Free: Prompts are retained for 30 days.", "opencode-zen-free"),
            ("OpenRouter Auto Free is free on OpenCode for a limited time.",
             "openrouter-auto-free"),
        ):
            with self.subTest(line=line):
                # The corroboration must be a catalogue identity this run READ.
                # A bare policy key is the extractor's own past output and may
                # not overturn the provider veto (see the collective-prose tests).
                self.assertIn(
                    key, watch.extract_policy([line], known={key}, observed={key}))
                self.assertEqual(watch.extract_policy([line]), {}, line)


    def test_a_real_model_name_survives_the_same_rule_on_both_routes(self):
        """The other direction: the fix must not close a genuine entry.

        A versioned or plain-worded real name is accepted because the run knows
        it, and a name the run has not seen yet is accepted only when a token
        carries an internal capital - never because it contains a number.
        """
        known = {"glm-5-3-flash-free", "jev-1-13-free", "jev-1-13", "beam-free"}
        for line, key in (
            ("GLM-5.3-Flash Free: Prompts are retained for 30 days.", "glm-5-3-flash-free"),
            ("Jev 1.13: Prompts are retained for 30 days.", "jev-1-13"),
            ("Beam Free: Prompts and other inputs are retained for 30 days.", "beam-free"),
            ("Jev 1.13 Free is available on OpenCode for a limited time.", "jev-1-13-free"),
            ("MiniMax M2 Free is free on OpenCode for a limited time.", "minimax-m2-free"),
        ):
            with self.subTest(line=line):
                self.assertIn(key, watch.extract_policy([line], known=known))
        self.assertEqual(
            set(watch.extract_policy(
                ["MiniMax M2 Free is free on OpenCode for a limited time."])),
            {"minimax-m2-free"})

    def test_the_real_page_entries_all_survive_the_stricter_rule(self):
        """Nothing is lost: every real entry on the fixture page still keys.

        The live-page counterpart (all twelve entries) is measured against the
        fetched page in the project's verify script, because a test may not
        reach the network.
        """
        with offline():
            _url, prose = live_prose()
        self.assertEqual(sorted(prose), [
            "beam-free", "flint-40b-free", "glm-5-3-flash-free",
        ])

    # MW-EXTRACTOR-SUBJECT-ROUTE-ADMITS-COLLECTIVE-PROSE. The round-12 rule made
    # a known identity or an internal capital the ONLY corroboration on a colon
    # label, but left the name-followed-by-its-own-clause route returning
    # `bool(subject)`. Subject position is not identity: a page writes "All
    # models are free ...", "Each model is free ..." and "OpenCode Console
    # provides a list of models ..." with the group, not a model, as subject.
    COLLECTIVE_HEADS = (
        "All models are free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Some models are free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Each model is free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Every model is free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Any model is free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Most models are free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "Many models are free to use on OpenCode for a limited time. "
        "Data is retained for 30 days.",
        "All free models on this page are free to use on OpenCode for a "
        "limited time.",
        "OpenCode Console: Prompts are retained for 30 days.",
        "OpenCode Go: Prompts are retained for 30 days.",
        "OpenCode Console provides a list of models tested and verified by the "
        "OpenCode team.",
    )

    def test_collective_prose_never_becomes_a_policy_key(self):
        """A determiner or a provider name opens a group, never a model."""
        known = {"beam-free", "glm-5-3-flash-free"}
        for line in self.COLLECTIVE_HEADS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line], known=known), {}, line)
                self.assertEqual(watch.extract_policy([line]), {}, line)

    def test_collective_prose_alerts_nothing_against_a_seeded_baseline(self):
        """AC-25: no alert may name "All models" as a model the page states."""
        before = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in self.COLLECTIVE_HEADS:
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                self.assertEqual(watch.diff(
                    before,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config()), [], line)

    def test_collective_prose_in_the_live_page_adds_no_key(self):
        """The same shapes through the real opencode_policy_text()."""
        with offline():
            _url, clean = live_prose()
            self.assertTrue(clean)
            for line in self.COLLECTIVE_HEADS:
                with self.subTest(line=line):
                    page = DOC_PAGE.replace(
                        "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                    with offline({watch.OPENCODE_DOCS[0]: page}):
                        _url2, prose = live_prose()
                    self.assertEqual(sorted(prose), sorted(clean), line)

    # The residual of MW-EXTRACTOR-SUBJECT-ROUTE-ADMITS-COLLECTIVE-PROSE. Round
    # 13 vetoed the determiners it had measured, which is a list of the words
    # that can precede a group noun - so "Selected models", "Listed models" and
    # "Available models" walked through the same route untouched. The rule is now
    # on the NOUN: a head whose own noun is the page's word for a model names a
    # group, whatever the adjective in front of it.
    CATEGORY_NOUN_HEADS = (
        "Selected models are free to use on OpenCode for a limited time.",
        "Listed models are free to use on OpenCode for a limited time.",
        "Available models are free to use on OpenCode for a limited time.",
        "Free models are free to use on OpenCode for a limited time.",
        "Supported models are free to use on OpenCode for a limited time.",
        "All Models are free to use on OpenCode for a limited time.",
        "Any model is free to use on OpenCode for a limited time.",
        "All free models on this page are free to use on OpenCode.",
    )

    def test_the_category_noun_is_a_group_on_every_route(self):
        """A head nouned "model(s)" names a group, not a model (AC-13/AC-25)."""
        for line in self.CATEGORY_NOUN_HEADS:
            with self.subTest(line=line):
                self.assertEqual(watch.extract_policy([line]), {}, line)
                # ...and the head reader must still SEE the noun. Dropping it
                # left the bare adjective "Selected" in the head, which is a
                # name-shaped word, so the veto could not tell what it was about.
                self.assertIn("model", watch.policy_head(line).lower(), line)

    def test_a_stale_baseline_cannot_corroborate_its_own_heading(self):
        """The veto may not be overturned by the extractor's own past output.

        `known` also carries the previous snapshot's policy keys. If those could
        overturn the veto, a baseline that once recorded "all-models" would keep
        that heading alive on every later run, for ever.
        """
        for line in self.CATEGORY_NOUN_HEADS + (
                "All models are free to use on OpenCode for a limited time. "
                "Data is retained for 30 days.",):
            with self.subTest(line=line):
                key = watch.slug(watch.policy_head(line))
                self.assertEqual(watch.extract_policy([line], known={key}), {}, line)

    def test_a_catalogue_identity_still_overturns_the_category_veto(self):
        """The other direction: independent upstream evidence still wins.

        A model the catalogue really names with the category word in it is a
        model, and only an identity this run READ may say so - never the
        previous snapshot's own policy key.
        """
        line = "Mystery Model: During its free period, collected data is retained for 30 days."
        key = "mystery-model"
        observed = watch.observed_identities({"opencode": {key: {}}}, ["opencode"])
        self.assertIn(key, observed)
        self.assertEqual(
            set(watch.extract_policy([line], known={key}, observed=observed)), {key})
        self.assertEqual(watch.extract_policy([line], known={key}), {}, line)

    def test_the_category_veto_passes_the_live_block_through(self):
        """The live page's real entries all survive the stricter rule."""
        with offline():
            _url, clean = live_prose()
        self.assertEqual(len(clean), 3)
        for line in self.CATEGORY_NOUN_HEADS:
            with self.subTest(line=line):
                page = DOC_PAGE.replace(
                    "<li>" + BEAM_PROSE, "<p>%s</p><li>%s" % (line, BEAM_PROSE))
                with offline({watch.OPENCODE_DOCS[0]: page}):
                    _url2, prose = live_prose()
                self.assertEqual(sorted(prose), sorted(clean), line)

    def test_no_accept_route_survives_the_two_stated_tests(self):
        """MW-EXTRACTOR-SUBJECT-ROUTE-ADMITS-COLLECTIVE-PROSE: the class, pinned.

        The reviewer measured collective prose and real page headings keying and
        firing *while* every shape-specific test passed, because one accept
        route - "multi-token, every word name-shaped, head is the clause
        subject" - skipped the stated rule entirely. Each earlier round closed
        the one shape that route had just admitted, so the next shape was
        admitted by the same route. This test asserts the route is GONE: every
        member below is admitted by neither route, whatever its shape.
        """
        collective = (
            "All models are free to use on OpenCode for a limited time. "
            "Data is retained for 30 days.",
            "Some models are free to use on OpenCode for a limited time.",
            "Each model is free to use on OpenCode for a limited time.",
            "Every model is free to use on OpenCode for a limited time.",
            "Any model is free to use on OpenCode for a limited time.",
            "Most models are free to use on OpenCode for a limited time.",
            "Many models are free to use on OpenCode for a limited time.",
            "All free models on this page are free to use on OpenCode.",
            "Selected models are free to use on OpenCode for a limited time.",
            "OpenCode Console: Prompts are retained for 30 days.",
            "OpenCode Console provides a list of models tested by the team.",
            "OpenCode Go: Prompts are retained for 30 days.",
            "OpenCode Go provides a subscription with included usage.",
            "Section 3 is free to use on OpenCode for a limited time.",
            "Section 3 retains your data for 30 days.",
        )
        for line in collective:
            with self.subTest(line=line):
                head = watch.policy_head(line)
                self.assertFalse(
                    watch.looks_like_model_name(head, subject=True,
                                                known=GLM_KNOWN),
                    "%r keyed as %r - an accept route skipped the stated rule"
                    % (line, watch.slug(head)))
                self.assertEqual(watch.extract_policy([line], known=GLM_KNOWN), {})

    def test_the_live_block_survives_without_the_removed_route(self):
        """No collateral: all twelve live entries are corroborated identities."""
        with offline():
            _url, prose = live_prose()
        self.assertEqual(len(prose), 3)
        self.assertEqual(sorted(prose),
                         ["beam-free", "flint-40b-free", "glm-5-3-flash-free"])

    def test_a_plain_worded_new_name_is_still_admitted_on_the_subject_route(self):
        """The other direction: a real new name is still admitted, with evidence.

        Two different kinds of evidence, because there are exactly two: an
        identity the run READ upstream, and an internal capital. A name with
        neither - "North Mini Code Free" on a page whose catalogue has not seen
        it - is not distinguishable from "All models", so it waits for the next
        catalogue read. That is the deliberate trade: a missed model shows up
        as a catalogue entry the policy page will describe next run, while a
        fabricated one alerts about a model the source never states.
        """
        for line, key, known in (
            ("North Mini Code Free is free on OpenCode for a limited time.",
             "north-mini-code-free", {"north-mini-code-free"}),
            ("MiniMax M2 Free is free on OpenCode for a limited time.",
             "minimax-m2-free", None),
        ):
            with self.subTest(line=line):
                self.assertEqual(
                    set(watch.extract_policy([line], known=known)), {key})



# ------------------------------------------------------------------ identity

class IdentityTests(unittest.TestCase):
    def test_normalise_treats_dot_dash_underscore_alike(self):
        self.assertEqual(watch.normalise("glm-5.3-flash-free"),
                         watch.normalise("glm-5-3-flash-free"))
        self.assertEqual(watch.normalise("GLM-5_3-Flash-Free"),
                         watch.normalise("glm-5.3-flash-free"))
        self.assertEqual(watch.normalise("opencode/glm-5.3-flash-free"),
                         watch.normalise("glm-5-3-flash-free"))

    def test_real_id_forms_land_in_group_a_with_the_owner_spelling(self):
        """The live baseline really carries these three spellings for one model."""
        cfg = config()
        old = state(policy={"opencode": {"glm-5-3-flash-free": {
            "text": GLM_PROSE, "class": "zero-retention"}}})
        new = state(
            free={"opencode": ["glm-5.3-flash-free"]},
            policy={"opencode": {"glm-5-3-flash-free": {
                "text": TRAINS_PROSE, "class": "trains-or-logs"}}},
        )
        events = watch.diff(old, new, catalog({"glm-5.3-flash-free": model()}), cfg)
        self.assertEqual(len(events), 1, events)
        self.assertTrue(events[0]["watched"])
        self.assertEqual(events[0]["display"], "glm-5.3-flash-free")
        text = watch.render(events, cfg)
        self.assertIn("Models you watch (1)", text)
        self.assertIn("glm-5.3-flash-free", text)
        self.assertNotIn("glm-5-3-flash-free", text)

    def test_literal_match_would_miss_the_watched_model(self):
        """Adversarial proof: a literal comparison files it under group (b)."""
        cfg = config()
        policy_key = "glm-5-3-flash-free"
        watch_entry = "opencode/glm-5.3-flash-free"
        self.assertFalse(policy_key in cfg["watch"] or watch_entry == policy_key,
                         "a literal match must not recognise the model")
        self.assertEqual(watch.watched_entry(cfg, "opencode", policy_key), watch_entry)

    def test_real_baseline_recognises_all_three_watched_models(self):
        with open(os.path.join(ROOT, "state.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        cfg = watch.load_config(os.path.join(ROOT, "config.json"))
        free = baseline["free"]["opencode"]
        documented = baseline["policy"]["opencode"]
        # Recognition must not depend on the free set: a watched model that has
        # legitimately gone paid is still watched, and still belongs in group (a).
        for entry in cfg["watch"]:
            _pid, mid = entry.split("/", 1)
            self.assertEqual(watch.watched_entry(cfg, "opencode", mid), entry)
        # The baseline's own spelling mismatch must resolve: policy says
        # glm-5-3-flash-free while watch says glm-5.3-flash-free.
        self.assertIn("glm-5-3-flash-free", documented)
        self.assertIn("glm-5.3-flash-free", free)
        self.assertEqual(watch.watched_entry(cfg, "opencode", "glm-5-3-flash-free"),
                         "opencode/glm-5.3-flash-free")
        # Every documented model is either watched or not, deterministically.
        for key in documented:
            self.assertEqual(bool(watch.watched_entry(cfg, "opencode", key)),
                             watch.normalise(key) in {watch.normalise(m) for m in WATCH_MODELS})


# ------------------------------------------------------------------ provider authority

class ProviderAuthorityTests(unittest.TestCase):
    def test_opencode_only_config_writes_no_openrouter_doc(self):
        with offline():
            snap, _cat, _meta = watch.collect(config(), {})
        self.assertEqual(list(snap["docs"]), [watch.OPENCODE_DOCS[0]])
        self.assertNotIn("openrouter", snap["free"])
        self.assertNotIn("openrouter", snap["policy"])

    def test_no_openrouter_url_is_fetched(self):
        with offline() as seen:
            watch.collect(config(), {})
        self.assertTrue(seen)
        self.assertEqual([u for u in seen if "openrouter" in u], [])

    def test_openrouter_config_still_collects_both_providers(self):
        with offline():
            snap, _cat, meta = watch.collect(config(providers=["opencode", "openrouter"]), {})
        self.assertIn("opencode", snap["free"])
        self.assertIn("openrouter", snap["free"])
        self.assertTrue(meta["providers_ok"]["openrouter"])
        self.assertTrue(any("openrouter.ai" in u for u in snap["docs"]))
        self.assertEqual(snap["free"]["openrouter"], ["cohere/north-mini-code:free"])

    def test_openrouter_excludes_router_alias_nontext_and_stealth(self):
        rows = {
            "openrouter/free": model(),
            "google/lyria-3-pro-preview": model(modalities=["text", "audio"]),
            "stealth/crouton-alpha": model(),
            "cohere/north-mini-code:free": model(),
        }
        self.assertEqual(watch.free_ids("openrouter", rows), ["cohere/north-mini-code:free"])

    def test_zero_price_alone_is_never_enough(self):
        self.assertFalse(watch.is_free("openrouter", "openrouter/free", model()))
        self.assertTrue(watch.is_free("opencode", "beam-free", model()))

    def test_exclusions_are_stated(self):
        self.assertIn("router alias", watch.OPENROUTER_EXCLUSIONS)
        self.assertIn("output modalities", watch.OPENROUTER_EXCLUSIONS)
        self.assertIn("stealth/*", watch.OPENROUTER_EXCLUSIONS)

    def test_shipped_config_is_opencode_only_and_keeps_watch_list(self):
        cfg = watch.load_config(os.path.join(ROOT, "config.json"))
        self.assertEqual(cfg["providers"], ["opencode"])
        self.assertEqual(cfg["watch"], WATCH)

    def test_old_state_shape_still_loads(self):
        legacy = {
            "docs": {"https://openrouter.ai/docs/guides/privacy/provider-logging": "deadbeefdeadbeef"},
            "free": {"opencode": ["beam-free"], "openrouter": ["openrouter/free"]},
            "policy": {"opencode": {"": {"class": "unstated", "text": "."}}, "openrouter": {}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(legacy, fh)
            self.assertEqual(watch.load_state(path), legacy)


# ------------------------------------------------------------------ determinism

class DeterminismTests(unittest.TestCase):
    def test_identical_state_yields_zero_events(self):
        with offline():
            snap, cat, meta = watch.collect(config(), {})
        self.assertEqual(watch.diff(snap, snap, cat, config(), meta), [])

    def test_two_collections_serialise_byte_identically(self):
        with offline():
            first, _c, _m = watch.collect(config(), {})
            second, _c, _m = watch.collect(config(), {})
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
            watch.write_state(first, a)
            watch.write_state(second, b)
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read())

    def test_missing_state_is_an_empty_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(watch.load_state(os.path.join(tmp, "nope.json")), {})

    def test_corrupt_state_is_an_empty_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(watch.load_state(path), {})

    def test_empty_state_file_is_an_empty_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            open(path, "w").close()
            self.assertEqual(watch.load_state(path), {})


# ------------------------------------------------------------------ phone readability

class ReadabilityTests(unittest.TestCase):
    def _full_text(self):
        old = state(
            free={"opencode": ["kimi-k2.5-free", "gone-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE,
                "flint-40b-free": TRAINS_PROSE,
            })},
        )
        new = state(
            free={"opencode": ["new-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": BEAM_PROSE,
                "flint-40b-free": GLM_PROSE,
            })},
        )
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8), "new-free": model(),
                       "gone-free": model(1.0, 2.0)})
        return watch.render(watch.diff(old, new, cat, config()), config())

    def test_no_table_rows(self):
        text = self._full_text()
        self.assertNotIn("|---", text)
        self.assertFalse(any(re.match(r"^\|.*\|$", l) for l in text.splitlines()))

    def test_no_line_over_200_characters(self):
        for line in self._full_text().splitlines():
            self.assertLessEqual(len(line), 200, line)

    def test_no_hash_identifiers(self):
        self.assertIsNone(re.search(r"\b[0-9a-f]{16}\b", self._full_text()))

    def test_mention_is_a_single_trailer_line(self):
        lines = self._full_text().splitlines()
        self.assertEqual(lines[-1], "@MatthewMBowyer")
        self.assertEqual(sum(1 for l in lines if l.strip() == "@MatthewMBowyer"), 1)
        self.assertFalse(any(l.startswith("cc @") for l in lines))

    def test_quiet_run_has_no_mention_line(self):
        self.assertNotIn("@MatthewMBowyer", watch.render([], config()))


class DelistingTests(unittest.TestCase):
    """A watched model leaving the catalog is ONE item, and a re-spelt free id
    is NO change (reviewer defects MW-DUP-DELISTED-HEADLINE, MW-FREE-SET-RESPELT).
    """

    def test_one_delisted_watched_model_is_one_item(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"glm-5.3-flash-free": model()})
        events = watch.diff(old, new, cat, config())
        self.assertEqual([(e["kind"], e["model"]) for e in events],
                         [("watched_delisted", "kimi-k2.5-free")])
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "One model you watch is no longer listed by its provider: kimi-k2.5-free.",
        )
        self.assertIn("Models you watch (1)", text)
        self.assertEqual(text.count("kimi-k2.5-free"), 2, text)  # headline + item
        self.assertNotIn("no longer free", text)
        self.assertNotIn("unstated", text)

    def test_three_delisted_watched_models_are_three_items(self):
        old = state(free={"opencode": list(WATCH_MODELS)})
        new = state(free={"opencode": []})
        events = watch.diff(old, new, catalog({"other-free": model()}), config())
        self.assertEqual(len(events), 3)
        self.assertEqual(sorted(e["model"] for e in events), sorted(WATCH_MODELS))
        text = watch.render(events, config())
        self.assertTrue(text.startswith("3 models you watch are no longer listed by "
                                        "their providers: "), text.splitlines()[0])
        self.assertIn("Models you watch (3)", text)
        for name in WATCH_MODELS:
            self.assertEqual(text.count(name), 2, name)  # headline + one item
        self.assertNotIn("no longer free", text)

    def test_mixed_delisting_and_paid_names_each_model_once(self):
        old = state(free={"opencode": ["kimi-k2.5-free", "deepseek-v4.1-flash-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"deepseek-v4.1-flash-free": model(0.5, 1.5)})
        events = watch.diff(old, new, cat, config())
        self.assertEqual(sorted(e["kind"] for e in events),
                         ["free_removed", "watched_delisted"])
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "2 models you watch are no longer free or no longer listed: "
            "deepseek-v4.1-flash-free, kimi-k2.5-free.",
        )
        self.assertIn("Models you watch (2)", text)
        for name in ("kimi-k2.5-free", "deepseek-v4.1-flash-free"):
            self.assertEqual(text.count(name), 2, name)  # headline + one item

    def test_free_set_respelling_is_not_a_change(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": ["kimi-k2-5-free"]})  # same id, re-spelt
        cat = catalog({"kimi-k2.5-free": model()})
        events = watch.diff(old, new, cat, config())
        self.assertEqual(events, [])
        self.assertEqual(watch.render(events, config()).splitlines()[0],
                         "Nothing changed since the last check.")

    def test_free_set_respelling_does_not_claim_new_and_removed(self):
        old = state(free={"opencode": ["beam-free", "kimi-k2.5-free"]})
        new = state(free={"opencode": ["beam-free", "kimi_k2_5_free"]})
        cat = catalog({"beam-free": model(), "kimi-k2.5-free": model()})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        self.assertNotIn("is a new free model", text)
        self.assertNotIn("is no longer free", text)
        self.assertNotIn("$0 in, $0 out", text)


class WatchedFreeAgainTests(unittest.TestCase):
    """A watched model that RETURNS to the free list must announce itself.

    Reviewer defect MW-HEADLINE-WATCHED-FREE-ADDED: `free_added` with
    watched=True fell through `_headline` to "Nothing changed since the last
    check." on an alert that notifies, so the one sentence the owner reads
    contradicted the body under it.
    """

    def _returning(self, models):
        """The owner's watched models come back at 0/0 after being absent."""
        old = state(free={"opencode": []})
        new = state(free={"opencode": list(models)})
        cat = catalog({m: model(0.0, 0.0) for m in models})
        return watch.diff(old, new, cat, config())

    def test_one_watched_model_free_again_headline(self):
        events = self._returning(["kimi-k2.5-free"])
        self.assertEqual([(e["kind"], e["watched"]) for e in events],
                         [("free_added", True)])
        text = watch.render(events, config())
        self.assertEqual(text.splitlines()[0],
                         "One model you watch is free again: kimi-k2.5-free.")
        self.assertNotEqual(text.splitlines()[0], "Nothing changed since the last check.")

    def test_two_watched_models_free_again_plural(self):
        events = self._returning(["kimi-k2.5-free", "glm-5.3-flash-free"])
        self.assertEqual(sorted(e["kind"] for e in events), ["free_added", "free_added"])
        self.assertTrue(all(e["watched"] for e in events))
        text = watch.render(events, config())
        self.assertEqual(
            text.splitlines()[0],
            "2 models you watch are free again: glm-5.3-flash-free, kimi-k2.5-free.",
        )

    def test_watched_free_added_lands_in_group_a_with_a_count_of_one(self):
        events = self._returning(["kimi-k2.5-free"])
        text = watch.render(events, config())
        self.assertIn("Models you watch (1)", text)
        self.assertIn("Other free models (0)", text)
        self.assertIn("kimi-k2.5-free is a model you watch, and it is free again on OpenCode.",
                      text)

    def test_watched_free_added_is_not_a_background_event(self):
        """It is group (a): a watched model, so it never reads as background news."""
        events = self._returning(["kimi-k2.5-free"])
        self.assertTrue(all(e["watched"] for e in events))
        self.assertIn("Models you watch (1)", watch.render(events, config()))

    def test_free_again_headline_is_one_sentence(self):
        text = watch.render(self._returning(["kimi-k2.5-free"]), config())
        headline = text.splitlines()[0]
        self.assertEqual(len(re.findall(r"[.!?](\s|$)", headline)), 1, headline)


class BackgroundFreeModelTests(unittest.TestCase):
    """Reviewer defect MW-OWNER-SUMMARY-GROUP-C-SILENT.

    The reviewer allowed either resolution: suppress the ping for a run whose
    only news is a free model the owner does not watch, or correct the doc.
    AC-40 / INV-19 require a run WITH events to hand the rendered summary to
    both notifiers, so the doc is corrected and these tests pin the agreement:
    a background-only run DOES notify, and OWNER_SUMMARY.md no longer says it
    does not.
    """

    def _main(self, old, new, cat, cfg=None):
        cfg = cfg or config()
        calls = []
        originals = (watch.notify_issue, watch.notify_webhook, watch.collect,
                     watch.load_config, watch.load_state, watch.write_state)
        try:
            watch.collect = lambda c, prev=None: (
                new, cat, {"providers_ok": {"opencode": True}, "catalog_ok": True,
                           "policy_ok": True})
            watch.load_config = lambda path=None: cfg
            watch.load_state = lambda path=None: old
            watch.write_state = lambda s, path=None: calls.append("state")
            watch.notify_issue = lambda *a, **k: calls.append("issue")
            watch.notify_webhook = lambda *a, **k: calls.append("webhook")
            with tempfile.TemporaryDirectory() as tmp:
                summary_path = os.path.join(tmp, "summary.md")
                os.environ["GITHUB_STEP_SUMMARY"] = summary_path
                os.environ["GITHUB_TOKEN"] = "unused"
                os.environ["GITHUB_REPOSITORY"] = "MatthewMBowyer/model-watch"
                try:
                    watch.main([])
                finally:
                    for key in ("GITHUB_STEP_SUMMARY", "GITHUB_TOKEN",
                                "GITHUB_REPOSITORY"):
                        os.environ.pop(key, None)
                with open(summary_path, encoding="utf-8") as fh:
                    step = fh.read()
        finally:
            (watch.notify_issue, watch.notify_webhook, watch.collect,
             watch.load_config, watch.load_state, watch.write_state) = originals
        return calls, step

    def _owner_summary(self):
        return open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 os.pardir, "OWNER_SUMMARY.md"), encoding="utf-8").read()

    def test_owner_summary_describes_the_wording_diff(self):
        """MW-OWNER-SUMMARY-RETENTION-CLAIM: the doc must describe what ships."""
        doc = self._owner_summary()
        self.assertIn("This fires on a change to\nthe meaning AND on a change to the "
                      "wording alone", doc)
        self.assertIn('becoming "keeps your prompts for 30 days" counts', doc)

    def test_owner_summary_documents_the_free_again_headline(self):
        self.assertIn('"One model you watch is free again: X."', self._owner_summary())

    def test_owner_summary_rewording_claim_is_true_of_the_shipped_rule(self):
        """The doc promises a reworded free clause is not a disappearance."""
        doc = self._owner_summary()
        self.assertIn("does not mistake a rewording for a disappearance", doc)
        tail = (" Its provider follows a zero-retention policy and does not use "
                "your data for model training.")
        was = "GLM-5.3-Flash Free is free on OpenCode for a limited time."
        now = "GLM-5.3-Flash Free costs nothing on OpenCode for a limited time."
        known = {"glm-5-3-flash-free"}
        before = watch.extract_policy([was + tail], known=known)
        after = watch.extract_policy([now + tail], known=known)
        self.assertEqual(set(before), set(after))
        self.assertEqual(set(before), {"glm-5-3-flash-free"})
        self.assertEqual(watch.diff(
            state(free={"opencode": ["glm-5-3-flash-free"]},
                  policy={"opencode": policy_map(**before)}),
            state(free={"opencode": ["glm-5-3-flash-free"]},
                  policy={"opencode": policy_map(**after)}),
            catalog({"glm-5.3-flash-free": model()}), config()), [])

    def test_owner_summary_churn_paragraph_matches_the_privacy_sentence_rule(self):
        doc = self._owner_summary()
        self.assertIn('a sentence only counts when\n  it names what happens to your '
                      'data', doc)
        self.assertNotIn("compares only\n  the sentences that make a privacy promise",
                         doc)

    def test_owner_summary_churn_claim_is_true_of_the_shipped_rule(self):
        """MW-OWNER-DOC-CHURN-CLAIM: the doc must not promise more than ships.

        The doc tells the owner that editing marketing copy produces no alert.
        That is only true if a marketing sentence is not a retention sentence,
        and if a marketing-only edit produces no event at all.
        """
        doc = self._owner_summary()
        self.assertIn("It does not go\n  looking for words like", doc)
        for sentence in StructuralPrivacyRuleTests.CHURN:
            self.assertFalse(watch.is_retention_sentence(sentence), sentence)
            events = watch.diff(
                state(policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})}),
                state(policy={"opencode": policy_map(**{
                    "glm-5-3-flash-free": GLM_PROSE + " " + sentence})}),
                catalog({}), config(),
            )
            self.assertEqual(events, [], sentence)

    def test_owner_summary_says_naming_the_provider_is_not_a_disclosure(self):
        """The round-7 fix is a distinction the owner has to be told about."""
        doc = self._owner_summary()
        self.assertIn("Naming the provider is not a privacy statement", doc)
        note = "The provider supports prompt caching."
        self.assertIn(note.strip(".").lower(), " ".join(doc.lower().split()))
        self.assertFalse(watch.is_retention_sentence(note))
        self.assertTrue(watch.is_retention_sentence("We provide your data to third parties."))

    def test_owner_summary_no_longer_promises_group_c_is_silent(self):
        doc = self._owner_summary()
        self.assertNotIn("never wakes your phone on its own", doc)
        self.assertIn("rides along in", doc)

    def test_owner_summary_states_what_a_group_c_only_run_actually_does(self):
        doc = self._owner_summary()
        self.assertIn("Background news only. Nothing to do.", doc)
        self.assertIn("the alert that arrives is just this section", doc)

    def test_owner_summary_heading_claim_is_true_of_the_shipped_rule(self):
        """The doc promises a numbered heading is never read as a model.

        Round 12: a page numbers its headings ("3.1 Retention terms:"), and the
        claim is only honest if such a line both fails to key and cannot be
        named by an alert against a seeded baseline.
        """
        doc = " ".join(self._owner_summary().split())
        self.assertIn("does not read a section heading as a model", doc)
        self.assertIn("a number is not a model name", doc)
        self.assertIn("Nor is the provider's own name a model", doc)
        self.assertIn("Nor is a heading that merely starts with the provider's name", doc)
        self.assertIn("Nor is a sentence that talks about the whole catalogue", doc)
        seeded = state(free={"opencode": ["glm-5-3-flash-free"]},
                       policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})})
        for line in ("3.1 Retention terms: requests are retained for 30 days.",
                     "Section 4.2: data is retained for 30 days.",
                     "Changelog v1.2: requests are logged for 30 days.",
                     "OpenCode: Prompts are retained for 30 days.",
                     "OpenCode Console: Prompts are retained for 30 days.",
                     "OpenCode Go provides a subscription with included usage.",
                     "All models are free to use on OpenCode for a limited time. "
                     "Data is retained for 30 days.",
                     "Each model is free to use on OpenCode for a limited time. "
                     "Data is retained for 30 days.",
                     "All free models on this page are free to use on OpenCode "
                     "for a limited time."):
            with self.subTest(line=line):
                keys = watch.extract_policy([GLM_PROSE, line], known=GLM_KNOWN)
                self.assertEqual(list(keys), ["glm-5-3-flash-free"], line)
                self.assertEqual(watch.diff(
                    seeded,
                    state(free={"opencode": ["glm-5-3-flash-free"]},
                          policy={"opencode": policy_map(**keys)}),
                    catalog({"glm-5.3-flash-free": model()}), config()), [], line)

    def test_group_c_only_run_still_notifies_as_the_doc_now_says(self):
        old = state(free={"opencode": []})
        new = state(free={"opencode": ["beam-free"]})   # not on the watch list
        calls, step = self._main(old, new, catalog({"beam-free": model()}))
        self.assertIn("issue", calls)
        self.assertIn("webhook", calls)
        self.assertIn("One free model changed, and it is not on your watch list.", step)
        self.assertIn("Other free models (1)", step)

    def test_watched_event_still_notifies(self):
        old = state(free={"opencode": []})
        new = state(free={"opencode": ["kimi-k2.5-free"]})
        calls, _ = self._main(old, new, catalog({"kimi-k2.5-free": model()}))
        self.assertIn("issue", calls)
        self.assertIn("webhook", calls)

    def test_unwatched_retention_change_still_notifies(self):
        old = state(policy={"opencode": policy_map(**{"beam-free": GLM_PROSE})})
        new = state(policy={"opencode": policy_map(**{"beam-free": TRAINS_PROSE})})
        calls, _ = self._main(old, new, catalog({}))
        self.assertIn("issue", calls)
        self.assertIn("webhook", calls)


class OpenRouterStatementTests(unittest.TestCase):
    """AC-11: when OpenRouter is configured the alert SAYS what it excludes."""

    def test_alert_states_the_exclusions_when_openrouter_is_configured(self):
        cfg = config(providers=["opencode", "openrouter"])
        old = state(free={"openrouter": ["openrouter/free"]})
        new = state(free={"openrouter": []})
        cat = {"openrouter": {"cohere/north-mini-code:free": model()}}
        text = watch.render(watch.diff(old, new, cat, cfg), cfg)
        self.assertIn("About OpenRouter:", text)
        self.assertIn("router alias", text)
        self.assertIn("output modalities", text)
        self.assertIn("stealth/*", text)

    def test_shipped_config_alert_carries_no_openrouter_sentence(self):
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        text = watch.render(watch.diff(old, new, cat, config()), config())
        self.assertNotIn("About OpenRouter:", text)

    def test_quiet_run_is_never_padded_with_the_exclusion_sentence(self):
        cfg = config(providers=["opencode", "openrouter"])
        text = watch.render([], cfg)
        self.assertNotIn("About OpenRouter:", text)
        self.assertEqual(text.splitlines()[0], "Nothing changed since the last check.")


# ------------------------------------------------------------------ delivered alert

class ShippedConfigAlertTests(unittest.TestCase):
    """AC-34: the quoted alert bodies must be reproducible from the SHIPPED
    config, not from a watch list extended for the example."""

    def test_alert_one_subject_is_on_the_shipped_watch_list(self):
        cfg = watch.load_config(os.path.join(ROOT, "config.json"))
        subject = "kimi-k2.5-free"
        self.assertIn("opencode/" + subject, cfg["watch"])
        events = watch.diff(
            state(free={"opencode": [subject]}),
            state(free={"opencode": []}),
            catalog({subject: model(0.4, 2.5)}),
            cfg,
        )
        text = watch.render(events, cfg)
        self.assertEqual(text.splitlines()[0],
                         "One model you watch is no longer free: %s." % subject)
        self.assertIn("You have it on your watch list, so this is likely to affect "
                      "your setup.", text)
        self.assertNotIn("not on your watch list", text)

    def test_alert_two_delisting_is_reproducible_from_the_shipped_config(self):
        cfg = watch.load_config(os.path.join(ROOT, "config.json"))
        subject = "kimi-k2.5-free"
        events = watch.diff(
            state(free={"opencode": [subject]}),
            state(free={"opencode": []}),
            catalog({"glm-5.3-flash-free": model()}),
            cfg,
        )
        text = watch.render(events, cfg)
        self.assertEqual(text.splitlines()[0],
                         "One model you watch is no longer listed by its provider: %s."
                         % subject)
        self.assertIn("Models you watch (1)", text)


class DeliveredAlertTests(unittest.TestCase):
    def test_delivered_alert_is_the_rendered_summary(self):
        """AC-40: the payloads handed to the notifiers ARE the rendered summary."""
        old = state(
            free={"opencode": ["kimi-k2.5-free"]},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": GLM_PROSE})},
        )
        new = state(
            free={"opencode": []},
            policy={"opencode": policy_map(**{"glm-5-3-flash-free": TRAINS_PROSE})},
        )
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        cfg = config()
        expected = watch.render(watch.diff(old, new, cat, cfg), cfg)

        captured = {}
        originals = (watch.notify_issue, watch.notify_webhook, watch.collect,
                     watch.load_config, watch.load_state, watch.write_state)
        try:
            watch.collect = lambda c, prev=None: (
                new, cat, {"providers_ok": {"opencode": True}, "catalog_ok": True,
                           "policy_ok": True})
            watch.load_config = lambda path=None: cfg
            watch.load_state = lambda path=None: old
            watch.write_state = lambda snap, path=None: captured.setdefault("state", snap)
            watch.notify_issue = lambda body, token, repo: captured.setdefault("issue", body) or "ok"
            watch.notify_webhook = lambda body, url: captured.setdefault("webhook", body) or "ok"

            with tempfile.TemporaryDirectory() as tmp:
                summary_path = os.path.join(tmp, "summary.md")
                os.environ["GITHUB_STEP_SUMMARY"] = summary_path
                os.environ["GITHUB_TOKEN"] = "unused"
                os.environ["GITHUB_REPOSITORY"] = "MatthewMBowyer/model-watch"
                try:
                    watch.main([])
                finally:
                    for key in ("GITHUB_STEP_SUMMARY", "GITHUB_TOKEN", "GITHUB_REPOSITORY"):
                        os.environ.pop(key, None)
                with open(summary_path, encoding="utf-8") as fh:
                    step = fh.read()
        finally:
            (watch.notify_issue, watch.notify_webhook, watch.collect,
             watch.load_config, watch.load_state, watch.write_state) = originals

        self.assertIn("issue", captured, "expected exactly one issue notification")
        self.assertEqual(captured.get("issue"), expected)
        self.assertEqual(captured.get("webhook"), expected)
        self.assertEqual(step.rstrip("\n"), expected)
        for code in ("NEW FREE", "no longer free", "POLICY CHANGED", "DOC CHANGED"):
            self.assertFalse(any(l.startswith(code) for l in expected.splitlines()))
        self.assertTrue(expected.endswith("\n\n@MatthewMBowyer"))

    def test_quiet_run_notifies_nobody_but_writes_the_step_summary(self):
        """AC-05 / INV-13."""
        cfg = config()
        snap = state(free={"opencode": ["beam-free"]},
                     policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})})
        calls = []
        originals = (watch.notify_issue, watch.notify_webhook, watch.collect,
                     watch.load_config, watch.load_state, watch.write_state)
        try:
            watch.collect = lambda c, prev=None: (
                snap, catalog({"beam-free": model()}),
                {"providers_ok": {"opencode": True}, "catalog_ok": True, "policy_ok": True})
            watch.load_config = lambda path=None: cfg
            watch.load_state = lambda path=None: snap
            watch.write_state = lambda s, path=None: None
            watch.notify_issue = lambda *a, **k: calls.append("issue")
            watch.notify_webhook = lambda *a, **k: calls.append("webhook")
            with tempfile.TemporaryDirectory() as tmp:
                summary_path = os.path.join(tmp, "summary.md")
                os.environ["GITHUB_STEP_SUMMARY"] = summary_path
                try:
                    watch.main([])
                finally:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                with open(summary_path, encoding="utf-8") as fh:
                    step = fh.read()
        finally:
            (watch.notify_issue, watch.notify_webhook, watch.collect,
             watch.load_config, watch.load_state, watch.write_state) = originals

        self.assertEqual(calls, [])
        self.assertIn("Nothing changed since the last check.", step)
        self.assertIn("Models you watch (0)", step)

    def test_dry_run_writes_no_state_and_notifies_nobody(self):
        """INV-12, with events present."""
        cfg = config()
        old = state(free={"opencode": ["kimi-k2.5-free"]})
        new = state(free={"opencode": []})
        cat = catalog({"kimi-k2.5-free": model(0.2, 0.8)})
        calls = []
        originals = (watch.notify_issue, watch.notify_webhook, watch.collect,
                     watch.load_config, watch.load_state, watch.write_state)
        try:
            watch.collect = lambda c, prev=None: (
                new, cat, {"providers_ok": {"opencode": True}, "catalog_ok": True,
                           "policy_ok": True})
            watch.load_config = lambda path=None: cfg
            watch.load_state = lambda path=None: old
            watch.write_state = lambda s, path=None: calls.append("state")
            watch.notify_issue = lambda *a, **k: calls.append("issue")
            watch.notify_webhook = lambda *a, **k: calls.append("webhook")
            watch.main(["--dry-run"])
        finally:
            (watch.notify_issue, watch.notify_webhook, watch.collect,
             watch.load_config, watch.load_state, watch.write_state) = originals
        self.assertEqual(calls, [])


# ------------------------------------------------------------------ failure handling

class FailureTests(unittest.TestCase):
    def test_dead_docs_page_does_not_read_as_every_model_removed(self):
        """INV-10: an unreachable policy source suppresses removal alerts."""
        old = state(
            free={"opencode": ["flint-40b-free"]},
            policy={"opencode": policy_map(**{
                "glm-5-3-flash-free": GLM_PROSE, "flint-40b-free": TRAINS_PROSE})},
            docs={"https://opencode.ai/v2/docs/console/models/": "oldoldoldoldold0"},
        )
        new = state(free={"opencode": ["flint-40b-free"]}, policy={"opencode": {}})
        meta = {"catalog_ok": True, "policy_ok": False, "providers_ok": {"opencode": True}}
        self.assertEqual(watch.diff(old, new, catalog({"flint-40b-free": model()}), config(), meta), [])

    def test_dead_catalog_does_not_read_as_every_model_removed(self):
        old = state(free={"opencode": ["beam-free"]})
        new = state(free={"opencode": []})
        meta = {"catalog_ok": False, "policy_ok": True, "providers_ok": {"opencode": False}}
        self.assertEqual(watch.diff(old, new, {"opencode": {}}, config(), meta), [])

    def test_dead_upstream_collect_keeps_previous_baseline(self):
        previous = state(free={"opencode": ["beam-free"]},
                         policy={"opencode": policy_map(**{"beam-free": BEAM_PROSE})},
                         docs={"https://opencode.ai/v2/docs/console/models/": "abc123abc123abc1"})
        broken = {watch.MODELS_DEV: "not json", watch.OPENCODE_DOCS[0]: "<html>nothing</html>"}
        with offline(pages=broken):
            snap, _cat, meta = watch.collect(config(), previous)
        self.assertFalse(meta["catalog_ok"])
        self.assertFalse(meta["policy_ok"])
        self.assertEqual(snap["free"], previous["free"])
        self.assertEqual(snap["policy"], previous["policy"])

    def test_doc_edit_that_touches_no_retention_statement_alerts_nothing(self):
        """INV-18 / AC-19: navigation or marketing churn is silent."""
        policies = policy_map(**{"glm-5-3-flash-free": GLM_PROSE, "beam-free": BEAM_PROSE})
        old = state(free={"opencode": ["beam-free"]}, policy={"opencode": policies},
                    docs={"https://opencode.ai/v2/docs/console/models/": "1111111111111111"})
        new = state(free={"opencode": ["beam-free"]}, policy={"opencode": dict(policies)},
                    docs={"https://opencode.ai/v2/docs/console/models/": "2222222222222222"})
        self.assertEqual(watch.diff(old, new, catalog({"beam-free": model()}), config()), [])

    def test_navigation_edit_to_the_real_page_shape_alerts_nothing(self):
        """The same page with extra nav/marketing text extracts the same policies."""
        edited = DOC_PAGE.replace("<nav>Models Pricing Docs</nav>",
                                  "<nav>Models Pricing Docs Blog Careers Status</nav>"
                                  "<p>New: faster routing for everyone.</p>")
        with offline(pages={watch.OPENCODE_DOCS[0]: edited}):
            snap, _cat, _meta = watch.collect(config(), {})
        with offline():
            baseline, _c, _m = watch.collect(config(), {})
        self.assertEqual(snap["policy"], baseline["policy"])
        self.assertEqual(watch.diff(baseline, snap, catalog({}), config()), [])

    def test_whole_page_digest_is_not_an_event_source(self):
        self.assertFalse(hasattr(watch, "doc_hash"))


# ------------------------------------------------------------------ stdlib only

class StdlibTests(unittest.TestCase):
    def test_no_third_party_import(self):
        with open(os.path.join(ROOT, "watch.py"), encoding="utf-8") as fh:
            source = fh.read()
        imported = set(re.findall(r"^(?:import|from)\s+([A-Za-z_][\w.]*)", source, re.M))
        allowed = set(sys.stdlib_module_names)
        for name in imported:
            self.assertIn(name.split(".")[0], allowed, "%s is not stdlib" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
