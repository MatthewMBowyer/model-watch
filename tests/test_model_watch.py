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
            "The provider deletes your data after 7 days.": "retained-7d",
        }
        for prose, expected in cases.items():
            self.assertEqual(watch.classify(prose), expected, prose)


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
        keys = set(watch.extract_policy(lines))
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
        line = "North Mini Code Free is free on OpenCode for a limited time."
        self.assertEqual(set(watch.extract_policy([line])), {"north-mini-code-free"})

    def test_real_captured_page_extracts_only_models(self):
        with offline() as seen:
            _snap, _cat, meta = watch.collect(config(), {})
        self.assertTrue(meta["policy_ok"])
        keys = set(_snap["policy"]["opencode"])
        self.assertEqual(keys, {"glm-5-3-flash-free", "beam-free", "flint-40b-free"})
        self.assertNotIn("openrouter", " ".join(seen))


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

    def test_owner_summary_churn_paragraph_matches_the_privacy_sentence_rule(self):
        self.assertIn("compares only\n  the sentences that make a privacy promise",
                      self._owner_summary())

    def test_owner_summary_no_longer_promises_group_c_is_silent(self):
        doc = self._owner_summary()
        self.assertNotIn("never wakes your phone on its own", doc)
        self.assertIn("rides along in", doc)

    def test_owner_summary_states_what_a_group_c_only_run_actually_does(self):
        doc = self._owner_summary()
        self.assertIn("Background news only. Nothing to do.", doc)
        self.assertIn("the alert that arrives is just this section", doc)

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
