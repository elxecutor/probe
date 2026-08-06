#!/usr/bin/env python3
"""Quote engine: pick high-engagement tweets, add original niche commentary, and
quote-post them. Runs alongside the reply engine; does NOT generate original
personal tweets."""

import logging
import time

from x_client import XClient
import llm
import phoenix_scorer
from state import (load_state, save_state, already_quoted, mark_quoted)

log = logging.getLogger(__name__)

MIN_TEXT_LEN = 30


def run_quote_cycle(client: XClient, state: dict, dry_run: bool, max_quotes: int) -> int:
    """Fetch tweets, rank by Phoenix/engagement, add commentary, and quote-post. Returns count."""
    def _engagement(t):
        return t["favorite_count"] + 3 * t["retweet_count"] + 2 * t["reply_count"]

    tweets, _ = client.get_timeline_paged(count=120, max_pages=4, page_size=40)
    candidates = []
    for t in tweets:
        if t["author_screen_name"] == "elxecutor":
            continue
        if len(t["full_text"]) < MIN_TEXT_LEN:
            continue
        if already_quoted(state, t["id_str"]):
            continue
        candidates.append(t)
    log.info("Fresh candidate tweets to quote: %d", len(candidates))

    if not candidates:
        log.info("No new candidate tweets to quote.")
        return 0

    SCORE_BUDGET = 12
    ranked = phoenix_scorer.rank_candidates(client, state, candidates)
    if ranked and "phoenix" in ranked[0]:
        scorable = [t for t in ranked
                    if t["phoenix"]["weighted"] > 0 or llm.is_niche(t["full_text"])]
    else:
        ranked = sorted(candidates, key=_engagement, reverse=True)
        scorable = [t for t in ranked if _engagement(t) > 0 or llm.is_niche(t["full_text"])]

    selected = llm.score_candidates(scorable, SCORE_BUDGET)[:max_quotes]

    posted = 0
    for score, t in selected:
        media_url = ""
        if t.get("media"):
            media_url = t["media"][0].get("url", "")
        desc = llm.describe_tweet(state, media_url, t["id_str"])
        article = llm.article_context(state, t.get("urls") or [], t["full_text"], t["id_str"])
        quote = llm.generate_quote(t["full_text"], t["author_screen_name"],
                                   image_desc=desc, article=article)
        honest, why = llm.honesty_check(quote)
        if not honest:
            log.info("  honesty blocked quote of @%s: %s", t["author_screen_name"], why)
            mark_quoted(state, t["id_str"], score, quote, dry_run)
            save_state(state)
            continue
        ok, reason = llm.safety_check(quote)
        if not ok:
            log.info("  safety blocked quote of @%s: %s", t["author_screen_name"], reason)
            mark_quoted(state, t["id_str"], score, quote, dry_run)
            save_state(state)
            continue
        algo_ok, algo = llm.content_passes_algo(quote)
        if not algo_ok:
            log.info("  algo-blocked quote of @%s (quality %s, slop %s): %s",
                     t["author_screen_name"], algo.get("quality_score"),
                     algo.get("slop_score"), algo.get("reason"))
            mark_quoted(state, t["id_str"], score, quote, dry_run)
            save_state(state)
            continue
        log.info("  QUOTE of @%s (score %d, quality %s): %s",
                 t["author_screen_name"], score, algo.get("quality_score"), quote)
        if dry_run:
            log.info("    [DRY RUN] would quote-post")
            mark_quoted(state, t["id_str"], score, quote, dry_run)
            posted += 1
        else:
            try:
                new_id = client.quote_tweet(quote, t["id_str"])
                log.info("    quoted (tweet %s)", new_id)
                mark_quoted(state, t["id_str"], score, quote, dry_run)
                posted += 1
            except Exception as e:
                log.error("    FAILED to quote-post: %s", e)
        time.sleep(2)

    save_state(state)
    log.info("Quote cycle done: %d posted", posted)
    return posted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-quotes", type=int, default=3)
    args = p.parse_args()

    c = XClient()
    st = load_state()
    run_quote_cycle(c, st, args.dry_run, args.max_quotes)
