#!/usr/bin/env python3
"""Shared runtime state helpers for the elxecutor autopilot.

All engines (reply, quote, content) and the main loop read and write the same
state.json so daily caps and dedup logs stay consistent across cycles.
"""

import json
import logging
import os
import time
from datetime import date

log = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

_DEFAULT_STATE = {
    "replied": {},
    "quoted": {},
    "posted_topics": [],
    "last_reply_day": "",
    "replies_today": 0,
    "last_content_day": "",
    "content_today": 0,
    "next_engine": "reply",
    # Per-account heartbeat: account_id -> highest tweet id already seen.
    # Engines only gather tweets newer than this so old tweets never qualify.
    "heartbeat": {},
    # Tweet ids the bot has already laid eyes on (notifications, replies/mentions
    # on our own posts). Once seen, never responded to again — "only respond to
    # stuff it hasn't seen" applies to the notification path too.
    "viewed": {},
    # Epoch-ms baseline for the notification timeline. Only replies/mentions with
    # sortIndex strictly newer than this qualify, so once the floor is stamped the
    # bot leaves all pre-existing notifications alone and only answers what comes
    # after. 0 = no floor (legacy behavior).
    "notification_floor": 0,
}


def load_state():
    """Load state.json, merging in any missing default keys. Returns a fresh dict on failure."""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
            for key, value in _DEFAULT_STATE.items():
                state.setdefault(key, value)
            return state
        except Exception as e:
            log.warning("Could not load state: %s", e)
    return dict(_DEFAULT_STATE)


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def roll_daily(state):
    """Reset daily counters when the calendar day rolls over."""
    today = date.today().isoformat()
    if state.get("last_reply_day") != today:
        state["last_reply_day"] = today
        state["replies_today"] = 0
    if state.get("last_content_day") != today:
        state["last_content_day"] = today
        state["content_today"] = 0


def already_replied(state, tweet_id):
    return str(tweet_id) in state["replied"]


def mark_replied(state, tweet_id, score, text, dry_run):
    state["replied"][str(tweet_id)] = {
        "ts": time.time(),
        "score": score,
        "reply": text,
        "dry_run": dry_run,
    }


def already_quoted(state, tweet_id):
    return str(tweet_id) in state.get("quoted", {})


def already_engaged(state, tweet_id):
    """True when the tweet has been replied to OR quoted — a tweet should only
    ever be engaged once, whichever engine gets to it first."""
    return already_replied(state, tweet_id) or already_quoted(state, tweet_id)


def mark_quoted(state, tweet_id, score, text, dry_run):
    state.setdefault("quoted", {})[str(tweet_id)] = {
        "ts": time.time(),
        "score": score,
        "quote": text,
        "dry_run": dry_run,
    }


# --- Heartbeat: track the newest tweet seen per followed account -------------
# Tweet ids are X snowflakes: id >> 22 = ms since Twitter's epoch, so ids
# encode a monotonic timestamp. We store the highest id seen per account and
# only gather tweets newer than it, plus a hard age window so genuinely old
# tweets never qualify even on the first run.

TWEET_EPOCH_MS = 1288834974657
FRESH_WINDOW_HOURS = 48


def _tweet_ts_ms(tweet_id) -> int:
    try:
        return (int(tweet_id) >> 22) + TWEET_EPOCH_MS
    except (ValueError, TypeError):
        return 0


def seen_tweet_id(state, account_id) -> int:
    return int(state.get("heartbeat", {}).get(str(account_id), 0) or 0)


def mark_seen(state, account_id, tweet_id):
    """Advance the account's heartbeat to a newer tweet id (max of the two)."""
    state.setdefault("heartbeat", {})[str(account_id)] = max(
        seen_tweet_id(state, account_id), int(tweet_id))


def is_new_tweet(state, account_id, tweet_id) -> bool:
    """True when the tweet is newer than the account's heartbeat AND within the
    freshness window (so stale tweets don't resurface)."""
    ts = _tweet_ts_ms(tweet_id)
    if not ts:
        return False
    if int(tweet_id) <= seen_tweet_id(state, account_id):
        return False
    age_ms = time.time() * 1000 - ts
    return age_ms <= FRESH_WINDOW_HOURS * 3600 * 1000


def is_within_window(tweet_id, hours: int = FRESH_WINDOW_HOURS) -> bool:
    """True when a tweet id is recent enough to act on (no per-account heartbeat).

    Notifications (replies/mentions on our own posts) aren't tied to a followed
    account's heartbeat, so this age check is the only freshness gate. Only the
    exact reply tweet ids are ever stored, so a re-run simply skips them."""
    ts = _tweet_ts_ms(tweet_id)
    if not ts:
        return False
    return time.time() * 1000 - ts <= hours * 3600 * 1000


def is_viewed(state, tweet_id) -> bool:
    """True when the bot has already laid eyes on this tweet (notification path).
    Once seen, it is never responded to again."""
    return str(tweet_id) in state.get("viewed", {})


def mark_viewed(state, tweet_id):
    """Record that a notification tweet has been seen, so later runs skip it."""
    state.setdefault("viewed", {})[str(tweet_id)] = time.time()


def is_above_notification_floor(state, sort_index) -> bool:
    """True when a notification is strictly newer than the stamped floor.

    The floor is an epoch-ms baseline: everything at or below it counts as
    'present' and is left alone; only future notifications pass."""
    floor = state.get("notification_floor", 0) or 0
    try:
        si = int(sort_index)
    except (ValueError, TypeError):
        return True
    return si > floor
