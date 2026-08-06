#!/usr/bin/env python3
"""Lightweight preflight check for the autopilot workflow.

Scans followed accounts for any tweet fresh enough to engage (newer than each
account's heartbeat and within the freshness window) WITHOUT loading the Phoenix
model, the Groq client, or any generation code — imports only x_client + state.

The workflow runs this first and only installs/loads Phoenix and runs the real
engager when there is actually something to respond to, so quiet runs stay fast
and cheap. Exits 0 and prints "has_candidates=1/0" so the workflow can gate the
heavy steps on the output.

NOTE: this is read-only — it never advances the heartbeat. The real engager
advances it when it actually runs, so a tweet spotted here is still eligible
for the engine it dispatches to.
"""

import logging
import os
import random
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from x_client import XClient  # noqa: E402
from state import load_state, is_new_tweet, already_replied, already_quoted  # noqa: E402

log = logging.getLogger("preflight")

MIN_TEXT_LEN = 30
TWEETS_PER_ACCOUNT = 3   # newest few is enough to detect "anything fresh"
ACCOUNTS_PER_RUN = 24    # scan the whole follow list — preflight is cheap


def has_fresh_candidates(client: XClient, state: dict) -> bool:
    following = {u["id_str"]: u for u in client.get_all_following()}
    accounts = list(following.values())
    random.shuffle(accounts)
    accounts = accounts[:ACCOUNTS_PER_RUN]

    found = 0
    for u in accounts:
        try:
            tweets, _ = client.get_user_tweets(u["id_str"], count=TWEETS_PER_ACCOUNT)
        except Exception as e:
            log.warning("  get_user_tweets(%s) failed: %s", u.get("screen_name"), e)
            continue
        for t in tweets:
            if t["author_screen_name"] == "elxecutor":
                continue
            if not is_new_tweet(state, u["id_str"], t["id_str"]):
                continue
            if len(t["full_text"]) < MIN_TEXT_LEN and not t.get("article_text"):
                continue
            if already_replied(state, t["id_str"]) or already_quoted(state, t["id_str"]):
                continue
            found += 1
            log.info("  fresh candidate: @%s %s %.60s",
                     t["author_screen_name"], t["id_str"], t["full_text"])
        time.sleep(0.3)
        if found >= 5:
            break
    return found > 0


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    client = XClient()
    state = load_state()
    ok = has_fresh_candidates(client, state)
    print(f"has_candidates={'1' if ok else '0'}")
    with open("preflight.txt", "w") as f:
        f.write("1" if ok else "0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
