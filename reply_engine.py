#!/usr/bin/env python3
"""Reply engine (70% of autopilot): fetch tweets from followed accounts, score them,
generate genuine replies, and post them — respecting rate limits and a reply log."""

import logging
import time

from x_client import XClient
import llm
import phoenix_scorer
from state import (load_state, save_state, already_replied, mark_replied)

log = logging.getLogger(__name__)

MIN_TEXT_LEN = 30


def run_reply_cycle(client: XClient, state: dict, dry_run: bool, max_replies: int) -> int:
    """Fetch followed-account tweets, score, generate and post replies. Returns count posted."""
    following = {u["id_str"]: u for u in client.get_all_following()}
    log.info("Following pool: %d accounts", len(following))

    tweets, _ = client.get_timeline_paged(count=120, max_pages=4, page_size=40)
    candidates = []
    for t in tweets:
        if t["author_id"] not in following:
            continue
        if t["author_screen_name"] == "elxecutor":
            continue
        if len(t["full_text"]) < MIN_TEXT_LEN:
            continue
        if already_replied(state, t["id_str"]):
            continue
        candidates.append(t)
    log.info("Fresh candidate tweets from followed accounts: %d", len(candidates))

    if not candidates:
        log.info("No new candidate tweets to reply to.")
        return 0

    # Pre-filter: rank by Phoenix-predicted engagement when available, else engagement
    # heuristic, then score only the top SCORE_BUDGET with the LLM (Groq rate limits).
    SCORE_BUDGET = 12
    def _engagement(t):
        return t["favorite_count"] + 3 * t["retweet_count"] + 2 * t["reply_count"]

    ranked = phoenix_scorer.rank_candidates(client, state, candidates)
    if ranked and "phoenix" in ranked[0]:
        log.info("  ranked by Phoenix predicted engagement")
        scorable = [t for t in ranked if t["phoenix"]["weighted"] > 0 or llm.is_niche(t["full_text"])]
    else:
        log.info("  ranked by raw engagement heuristic")
        ranked = sorted(candidates, key=_engagement, reverse=True)
        scorable = [t for t in ranked if _engagement(t) > 0 or llm.is_niche(t["full_text"])]

    selected = llm.score_candidates(scorable, SCORE_BUDGET)[:max_replies]

    posted = 0
    for score, t in selected:
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
        log.info("  REPLY to @%s (score %d, algo rank %s): %s",
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
        time.sleep(2)

    save_state(state)
    log.info("Reply cycle done: %d posted", posted)
    return posted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-replies", type=int, default=5)
    args = p.parse_args()

    c = XClient()
    st = load_state()
    run_reply_cycle(c, st, args.dry_run, args.max_replies)
