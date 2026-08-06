#!/usr/bin/env python3
"""Content engine: pull trending topics, cross-reference with the EEE/communications/
electronic-materials niche, generate original tweets, and post them.

NOTE: standalone tool — NOT wired into engager.py, which only runs the reply and
quote engines (original personal tweets are handled manually by the owner). Run it
directly via its __main__ block when wanted."""

import logging
import time

from x_client import XClient
import llm
import phoenix_scorer
from state import load_state, save_state

log = logging.getLogger(__name__)


def _phoenix_top_niche_tweets(client: XClient, state: dict, count: int = 10) -> list:
    """Score trending tweets with Phoenix and return the top ones, pre-sorted by
    predicted weighted engagement. Used to seed original content on what the account's
    audience would most likely engage with. Falls back to empty on failure."""
    try:
        tweets = client.get_trending_tweets(count=count)
        if not tweets:
            return []
        me = client.get_me()
        history = []
        likes, _ = client.get_likes(count=40)
        for t in likes:
            history.append({
                "post_id": t["id_str"],
                "author_id": t["author_id"],
                "actions": {"1": 1.0},
            })
        cands = [{"post_id": t["id_str"], "author_id": t["author_id"]} for t in tweets]
        scores = phoenix_scorer.get_scorer().score_tweets(me["id_str"], history, cands)
        by_id = {s["post_id"]: s for s in scores}
        for t in tweets:
            t["phoenix"] = by_id.get(t["id_str"], {"weighted": 0.0, "fav": 0.0})
        return sorted(tweets, key=lambda t: -t["phoenix"]["weighted"])
    except Exception as e:
        log.warning("Phoenix trending scoring unavailable (%s).", e)
        return []


def run_content_cycle(client: XClient, state: dict, dry_run: bool, max_tweets: int) -> int:
    """Pull trending topics, filter to niche, generate and post originals. Returns count posted."""
    trending = client.get_trending(count=50)
    log.info("Trending topics fetched: %d", len(trending))

    niche_topics = [t for t in trending if llm.is_niche(t)]
    log.info("Keyword-matched niche topics: %s", niche_topics)

    # Seed content on the top Phoenix-scored trending tweets when they exist; those
    # reflect what the account's audience most likely engages with. Seeds must pass
    # both the keyword AND the LLM niche check.
    phoenix_tweets = _phoenix_top_niche_tweets(client, state)
    phoenix_topics = []
    for t in phoenix_tweets:
        if llm.is_niche(t["full_text"]) and llm.niche_check_llm(t["full_text"]):
            seed = t["full_text"][:80].split(".")[0]
            if seed not in phoenix_topics:
                phoenix_topics.append(seed)
    log.info("Phoenix-seeded niche topics: %s", phoenix_topics)

    # Prefer Phoenix-seeded topics, then trending niche topics.
    ordered = phoenix_topics + [t for t in niche_topics if t not in phoenix_topics]
    seen = set()
    ordered = [t for t in ordered if not (t in seen or seen.add(t))]
    topics = ordered if ordered else niche_topics

    posted = 0
    for topic in topics:
        if posted >= max_tweets:
            break
        if topic in state["posted_topics"]:
            continue
        try:
            tweets = client.get_trending_tweets(count=10)
            context = "\n".join(f"@{t['author_screen_name']}: {t['full_text'][:140]}"
                                for t in tweets if llm.is_niche(t["full_text"]))[:400]
        except Exception as e:
            log.warning("Could not fetch trending tweet context: %s", e)
            context = ""
        if not context and not llm.niche_check_llm(topic):
            log.info("  %r is not niche after LLM check", topic)
            state["posted_topics"].append(topic)  # don't retry
            continue

        tweet = llm.generate_content(topic, context)
        if not llm.niche_check_llm(tweet):
            log.info("  niche-blocked content on %r (LLM: not EEE/hardware)", topic)
            state["posted_topics"].append(topic)
            continue
        honest, why = llm.honesty_check(tweet)
        if not honest:
            log.info("  honesty-blocked content on %r: %s", topic, why)
            state["posted_topics"].append(topic)
            continue
        ok, reason = llm.safety_check(tweet)
        if not ok:
            log.info("  safety blocked content on %r: %s", topic, reason)
            state["posted_topics"].append(topic)
            continue
        algo_ok, algo = llm.content_passes_algo(tweet)
        if not algo_ok:
            log.info("  algo-blocked content on %r (quality %s, slop %s): %s",
                     topic, algo.get("quality_score"), algo.get("slop_score"),
                     algo.get("reason"))
            state["posted_topics"].append(topic)
            continue
        log.info("  CONTENT (topic %r, quality %s): %s",
                 topic, algo.get("quality_score"), tweet)
        if dry_run:
            log.info("    [DRY RUN] would post")
        else:
            try:
                client.create_tweet(tweet)
                log.info("    posted")
            except Exception as e:
                log.error("    FAILED to post content: %s", e)
        state["posted_topics"].append(topic)
        posted += 1
        time.sleep(3)

    save_state(state)
    log.info("Content cycle done: %d posted", posted)
    return posted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-tweets", type=int, default=2)
    args = p.parse_args()

    c = XClient()
    st = load_state()
    run_content_cycle(c, st, args.dry_run, args.max_tweets)
