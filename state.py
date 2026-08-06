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


def mark_quoted(state, tweet_id, score, text, dry_run):
    state.setdefault("quoted", {})[str(tweet_id)] = {
        "ts": time.time(),
        "score": score,
        "quote": text,
        "dry_run": dry_run,
    }
