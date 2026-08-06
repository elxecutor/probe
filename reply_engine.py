#!/usr/bin/env python3
"""Reply engine: fetch tweets from followed accounts, score them, generate a
genuine reply, and post ONE — respecting rate limits and a reply log."""

import logging
import random
import time

from x_client import XClient
import llm
import phoenix_scorer
from state import (load_state, save_state, already_replied, mark_replied)

log = logging.getLogger(__name__)

MIN_TEXT_LEN = 30
ACCOUNTS_PER_RUN = 10   # rotate through followed accounts so no single one dominates
TWEETS_PER_ACCOUNT = 5
MAX_CANDIDATES = 30


def run_reply_cycle(client: XClient, state: dict, dry_run: bool, max_replies: int) -> int:
    """Pick the single best reply candidate from followed accounts and post it."""
    following = {u["id_str"]: u for u in client.get_all_following()}
    log.info("Following pool: %d accounts", len(following))

    candidates = _gather_followed_candidates(client, following, state)
    log.info("Fresh candidate tweets from followed accounts: %d", len(candidates))

    if not candidates:
        log.info("No new candidate tweets to reply to.")
        return 0

    # Rank strictly by Phoenix-predicted engagement (no LLM scoring — Groq quota
    # stays reserved for content generation + safety checks). Fall back to the raw
    # engagement heuristic only when the ranker is unavailable.
    def _engagement(t):
        return t["favorite_count"] + 3 * t["retweet_count"] + 2 * t["reply_count"]

    ranked = phoenix_scorer.rank_candidates(client, state, candidates)
    if ranked and "phoenix" in ranked[0]:
        log.info("  ranked by Phoenix predicted engagement")
        selected = ranked[:max_replies]
    else:
        log.info("  ranked by raw engagement heuristic")
        ranked = sorted(candidates, key=_engagement, reverse=True)
        selected = ranked[:max_replies]

    posted = 0
    for t in selected:
        score = t.get("phoenix", {}).get("weighted", 0.0)
        author = following.get(t["author_id"], {})
        bio = author.get("description", "")
        name = author.get("name", t["author_screen_name"])
        media_url = ""
        if t.get("media"):
            media_url = t["media"][0].get("url", "")
        desc = llm.describe_tweet(state, media_url, t["id_str"])
        article = llm.article_context(state, t.get("urls") or [], t["full_text"], t["id_str"])
        reply = llm.generate_reply(t["full_text"], name, bio, image_desc=desc, article=article)
        ok, reason = llm.safety_check(reply)
        if not ok:
            log.info("  safety blocked reply to @%s: %s", t["author_screen_name"], reason)
            mark_replied(state, t["id_str"], score, reply, dry_run)  # don't re-pick
            save_state(state)
            continue
        rank_ok, rank = llm.reply_rank_ok(reply)
        if not rank_ok:
            log.info("  algo-blocked reply to @%s (rank %s): %s",
                     t["author_screen_name"], rank.get("score"), rank.get("reason"))
            mark_replied(state, t["id_str"], score, reply, dry_run)
            save_state(state)
            continue
        log.info("  REPLY to @%s (phoenix %.3f, algo rank %s): %s",
                 t["author_screen_name"], score, rank.get("score"), reply)
        if dry_run:
            log.info("    [DRY RUN] would post")
            mark_replied(state, t["id_str"], score, reply, dry_run)
            posted += 1
        else:
            try:
                new_id = client.create_tweet(reply, reply_to_tweet_id=t["id_str"])
                log.info("    posted (tweet %s)", new_id)
                mark_replied(state, t["id_str"], score, reply, dry_run)
                posted += 1
            except Exception as e:
                log.error("    FAILED to post reply: %s", e)
        break  # one post per run

    save_state(state)
    log.info("Reply cycle done: %d posted", posted)
    return posted


def _gather_followed_candidates(client: XClient, following: dict, state: dict) -> list:
    """Collect recent tweets directly from a rotating sample of followed accounts.

    Fetching each account's own timeline (instead of the flooded home timeline)
    keeps the candidate pool spread across all followed accounts, so a prolific
    account like hackaday can't dominate every cycle.
    """
    accounts = list(following.values())
    random.shuffle(accounts)
    accounts = accounts[:ACCOUNTS_PER_RUN]

    candidates = []
    for u in accounts:
        try:
            tweets, _ = client.get_user_tweets(u["id_str"], count=TWEETS_PER_ACCOUNT)
        except Exception as e:
            log.warning("  get_user_tweets(%s) failed: %s", u.get("screen_name"), e)
            continue
        for t in tweets:
            if t["author_screen_name"] == "elxecutor":
                continue
            if len(t["full_text"]) < MIN_TEXT_LEN:
                continue
            if already_replied(state, t["id_str"]):
                continue
            candidates.append(t)
            if len(candidates) >= MAX_CANDIDATES:
                break
        time.sleep(0.8)
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-replies", type=int, default=1)
    args = p.parse_args()

    c = XClient()
    st = load_state()
    run_reply_cycle(c, st, args.dry_run, args.max_replies)
