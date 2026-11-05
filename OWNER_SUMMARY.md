# model-watch, explained

This page is for you, not for a programmer. It tells you what the tool watches,
what its alerts mean, and what you are supposed to do when one arrives.
You never need to open `watch.py`.

## What it watches

Three things, every six hours, for free:

1. **The free models you actually use.** Right now that is
   `glm-5.3-flash-free`, `deepseek-v4.1-flash-free` and `kimi-k2.5-free`
   on OpenCode. If one of them stops being free, or stops being listed at all,
   you get told.
2. **What a provider says it does with your data.** OpenCode's free-model page
   states, in words, whether a model keeps your prompts. The three answers that
   matter are:
   - *zero retention* - the provider keeps no copy of what you send it. Best.
   - *retained for N days* (or N hours, weeks or months, as the provider
     writes it) - it keeps a copy for a while, for safety reasons.
   - *trains on or improves the model* - your prompts may be used to improve it.
   A model moving from the first to either of the others is the single most
   important thing this tool exists to catch.
3. **Other free models appearing or disappearing.** This is background news.
   It is listed last and it is not urgent: a free model you do not use appearing
   or disappearing never changes anything about your setup. It rides along in
   the same alert as the first two groups. If it is the only thing that changed,
   the alert that arrives is just this section, and there is nothing to do.

It only watches **OpenCode** now. OpenRouter is not watched, because you do not
use it and its alerts were unreadable.

## What an alert looks like

Every alert opens with one sentence telling you what happened for you:

> One model you watch is no longer free: kimi-k2.5-free.

Then three sections, always in this order, always all three present:

- **Models you watch** - anything that changed for the models in your list.
- **Data retention and privacy wording** - anything a provider changed about
  your data. If the change is on a model you watch, it is listed in the section
  above instead, and this section says so.
- **Other free models** - everything else.

Each section starts with a count. If a section has nothing in it, it says
"Nothing here - ..." rather than disappearing, so you can tell "nothing
happened" apart from "the tool forgot to look".

Under each item it says, in plain words, what the model is, what changed, and
what to do. For a retention change it quotes the sentence as it was and the
sentence as it is now, and names which sentence actually changed - so an added
"we may disclose it to law enforcement" clause is shown to you, not buried
inside a long quote. The quoted before and after text is there so you can judge
the change yourself.

One model is one item. If a model you watch disappears from the catalogue you
get a single entry saying it is no longer listed, and the headline counts one
model - it is never counted twice as both "gone" and "no longer free". A
provider re-spelling the same model id (dots versus dashes) is not a change and
alerts nothing.

## What each alert means, and what to do

**"One model you watch is no longer free: X."**
The model still exists but it now costs money. The alert tells you the old price
and the new price. You do not have to do anything immediately, but your setup
may start charging you. Switch to another free model, or accept the cost.

**"One model you watch is no longer listed by its provider: X."**
The model is gone from the catalogue. Whatever uses it will fail. Point it at
something else.

**"One model you watch is free again: X."**
A model on your watch list that had stopped being free is free again. Nothing to
do unless you had stopped using it because it cost money.

**"A model you watch changed its data-retention wording: X."**
The provider changed what it says about your prompts. This fires on a change to
the meaning AND on a change to the wording alone - "retains it for 30 days"
becoming "keeps your prompts for 30 days" counts. Read the quoted before and
after. If it moved from zero-retention to anything else, decide whether you are
still comfortable sending it work.

**"A provider changed its data-retention wording - this is the kind of change
this tool exists to catch."**
A retention statement changed on a model that is not on your watch list. Worth a
glance; usually nothing to do.

**"One free model changed, and it is not on your watch list."**
Background news only. Nothing to do. This is the whole alert when the only thing
that changed was a free model you do not use.

**"Nothing changed since the last check."**
You will not normally see this on your phone. A quiet run writes its summary
into the Actions run page and sends no notification at all, so a silent day
means a quiet day, not a broken tool.

## What it deliberately does not do

- It does not guess. If a source does not state a price, a date or a retention
  rule, the alert says `unstated` instead of making something up.
- It does not alert on page churn. Editing the navigation bar, the marketing
  copy or the layout of the provider's page produces no alert. It does not go
  looking for words like "day", "log" or "share": a sentence only counts when
  it names what happens to your data - keeps your prompts for 30 days, logs your
  requests, deletes your inputs after 7 days - so a reworded promise alerts even
  when its meaning is unchanged, and a price or model-list change alerts as it
  always did.
- It does not treat a broken fetch as news. If the provider's page is down, the
  tool records "could not check" and stays quiet. It will never tell you that
  every model disappeared just because a page failed to load.
- It does not send "nothing changed" messages. Silence is the healthy state.
- It does not use a paid service, a server, or an API key. It is a GitHub
  Actions job on a six-hourly schedule.
- It does not watch anything you did not ask for.

## Who can read your alerts

The alerts are posted to two places:

- a comment on the issue `[model-watch] changes` in your repository, and
- an **ntfy** topic, which is what actually reaches your phone.

Both fire when something changed. A run with nothing to report sends neither -
it writes its summary into the Actions run page.

**Anyone who knows the ntfy topic name can read those alerts.** The topic is a
public name in a public URL, not a password. The alerts contain model names,
prices and provider wording - no prompts, no keys, no personal data - so the
practical risk is low, but it is not zero and you should know it.

If that ever bothers you, change the topic name in `config.json` (or move the
URL into the `ALERT_WEBHOOK_URL` secret, which wins over `config.json`) and
re-subscribe in the ntfy app. Do not put a token or a password in this
repository.

## If you want to change what is watched

Edit the `watch` list in `config.json`. Each entry is `provider/model-id`, for
example:

```json
"watch": [
  "opencode/glm-5.3-flash-free",
  "opencode/kimi-k2.5-free"
]
```

The spelling does not have to match the provider's own spelling exactly -
`glm-5.3-flash-free` and `glm-5-3-flash-free` are treated as the same model -
but the provider name and the model name both have to be right. The `providers`
list decides what is looked at at all; with `["opencode"]` the tool never
contacts OpenRouter.

## Running it by hand

You do not need to. If you ever want to see what it would say without changing
anything or sending anything:

```
python watch.py --dry-run
```

That prints the summary and the list of pages it read, writes nothing, and
notifies nobody.
