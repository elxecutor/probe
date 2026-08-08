#!/usr/bin/env python3
"""Engines for the elxecutor autopilot: reply, quote, and original content.

Reply and quote cycles gather fresh tweets from followed accounts, rank them
(Phoenix model, falling back to a raw engagement heuristic), generate a genuine
post, and post ONE per run. The content cycle turns trending niche topics into
original tweets and is a standalone tool (NOT wired into the autopilot loop —
original personal tweets are handled manually by the owner).

All cycles read/write the same state.json (dedup logs + daily caps) and share the
candidate-gathering machinery: a rotating sample of followed accounts, a
per-account heartbeat that only admits tweets newer than the last sighting, and
priority for accounts preflight flagged as holding fresh candidates.
"""

import argparse
import logging
import random
import time

from x_client import XClient
import llm
import phoenix_scorer
from state import (load_state, save_state, mark_replied, mark_quoted,
                   mark_seen, is_new_tweet, already_engaged)

log = logging.getLogger(__name__)

_ARTICLE_LINK = "/i/article/"

MIN_TEXT_LEN = 30
ACCOUNTS_PER_RUN = 10   # rotate through followed accounts so no single one dominates
TWEETS_PER_ACCOUNT = 5
MAX_CANDIDATES = 30
FRESH_ACCOUNTS_FILE = "fresh_accounts.txt"   # written by engager.py --preflight


def _resolve_article_text(client: XClient, t: dict) -> str:
    """Return the tweet's article body, fetching it on demand when needed.

    UserTweets omits the article blocks, so if the tweet links an X Article and
    _normalize_tweet didn't capture a body, fetch it via TweetResultByRestId."""
    if t.get("article_text"):
        return t["article_text"]
    if any(_ARTICLE_LINK in u.get("expanded_url", "")
           for u in t.get("urls", [])):
        try:
            return client.get_article_body(t["id_str"])
        except Exception as e:
            log.warning("  get_article_body(%s) failed: %s", t["id_str"], e)
    return ""


def _prioritized_accounts(following: list) -> list:
    """Order followed accounts so preflight-flagged ones are scanned first.

    Preflight scans the whole follow list and records which accounts hold fresh
    candidates; the engines only sample a subset per run. Putting those accounts
    first means what preflight spotted is actually within reach — the random
    sample then fills the rest of the budget."""
    fresh = set()
    try:
        with open(FRESH_ACCOUNTS_FILE) as f:
            fresh = {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        pass
    if not fresh:
        return following
    priority = [u for u in following if u["id_str"] in fresh]
    rest = [u for u in following if u["id_str"] not in fresh]
    random.shuffle(rest)
    return priority + rest


def _gather_followed_candidates(client: XClient, following: dict, state: dict) -> list:
    """Collect recent tweets from a rotating sample of followed accounts.

    Fetching each account's own timeline (instead of the flooded home timeline)
    keeps the candidate pool spread across all followed accounts, so a prolific
    account like hackaday can't dominate every cycle.

    A per-account heartbeat (highest tweet id already seen, persisted in
    state.json) means only tweets newer than the last sighting qualify — old
    tweets never resurface even if the account goes quiet.
    """
    accounts = list(following.values())
    random.shuffle(accounts)
    accounts = _prioritized_accounts(accounts)
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
            if not is_new_tweet(state, u["id_str"], t["id_str"]):
                continue
            if len(t["full_text"]) < MIN_TEXT_LEN and not t.get("article_text"):
                continue
            if already_engaged(state, t["id_str"]):
                continue
            candidates.append(t)
            if len(candidates) >= MAX_CANDIDATES:
                break
        for t in tweets:
            mark_seen(state, u["id_str"], t["id_str"])
        time.sleep(0.8)
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def _engagement(t):
    return t["favorite_count"] + 3 * t["retweet_count"] + 2 * t["reply_count"]


def _rank_candidates(client, state, candidates):
    """Rank candidates by Phoenix-predicted engagement, falling back to the raw
    engagement heuristic when the ranker is unavailable. (Groq quota stays
    reserved for content generation + safety checks.)"""
    ranked = phoenix_scorer.rank_candidates(client, state, candidates)
    if ranked and "phoenix" in ranked[0]:
        log.info("  ranked by Phoenix predicted engagement")
    else:
        log.info("  ranked by raw engagement heuristic")
        ranked = sorted(candidates, key=_engagement, reverse=True)
    return ranked


def _mark_engaged(state, mode, tweet_id, score, text, dry_run):
    if mode == "reply":
        mark_replied(state, tweet_id, score, text, dry_run)
    else:
        mark_quoted(state, tweet_id, score, text, dry_run)


def _gate_text(mode, text, target):
    """Run the mode-specific generation gates. Returns (ok, log_line or None)."""
    if mode == "quote":
        honest, why = llm.honesty_check(text)
        if not honest:
            return False, f"  honesty blocked quote of @{target}: {why}"
    ok, reason = llm.safety_check(text)
    if not ok:
        return False, f"  safety blocked {mode} to @{target}: {reason}"
    if mode == "reply":
        rank_ok, rank = llm.reply_rank_ok(text)
        if not rank_ok:
            return False, (f"  algo-blocked reply to @{target} (rank {rank.get('score')}): "
                           f"{rank.get('reason')}")
    else:
        algo_ok, algo = llm.content_passes_algo(text)
        if not algo_ok:
            return False, (f"  algo-blocked quote of @{target} (quality {algo.get('quality_score')}, "
                           f"slop {algo.get('slop_score')}): {algo.get('reason')}")
    return True, None


def run_followed_cycle(client: XClient, state: dict, dry_run: bool,
                       max_posts: int, mode: str) -> int:
    """Pick the single best reply/quote candidate from followed accounts and post it.

    mode: "reply" or "quote". Shared by both — the only differences are the
    generation prompt, the safety/algorithm gates, and how the post is made."""
    following = {u["id_str"]: u for u in client.get_all_following()}
    log.info("Following pool: %d accounts", len(following))

    candidates = _gather_followed_candidates(client, following, state)
    log.info("Fresh candidate tweets to %s: %d", mode, len(candidates))

    if not candidates:
        log.info("No new candidate tweets to %s.", mode)
        return 0

    ranked = _rank_candidates(client, state, candidates)

    posted = 0
    for t in ranked:
        if posted >= max_posts:
            break
        score = t.get("phoenix", {}).get("weighted", 0.0)
        author = following.get(t["author_id"], {})
        bio = author.get("description", "")
        name = author.get("name", t["author_screen_name"])
        media_url = ""
        if t.get("media"):
            media_url = t["media"][0].get("url", "")
        desc = llm.describe_tweet(state, media_url, t["id_str"])
        article = llm.article_context(state, t.get("urls") or [], t["full_text"], t["id_str"],
                                      inline_article=_resolve_article_text(client, t))
        if mode == "reply":
            text = llm.generate_reply(t["full_text"], name, bio,
                                      image_desc=desc, article=article)
        else:
            text = llm.generate_quote(t["full_text"], t["author_screen_name"],
                                      image_desc=desc, article=article)

        ok, block_line = _gate_text(mode, text, t["author_screen_name"])
        if not ok:
            log.info(block_line)
            _mark_engaged(state, mode, t["id_str"], score, text, dry_run)  # don't re-pick
            save_state(state)
            continue

        log.info("  %s to @%s (phoenix %.3f): %s",
                 mode.upper(), t["author_screen_name"], score, text)
        if dry_run:
            log.info("    [DRY RUN] would post")
            _mark_engaged(state, mode, t["id_str"], score, text, dry_run)
            posted += 1
        else:
            try:
                client.like(t["id_str"])
                log.info("    liked tweet %s", t["id_str"])
            except Exception as e:
                log.warning("    FAILED to like tweet: %s", e)
            try:
                if mode == "reply":
                    new_id = client.create_tweet(text, reply_to_tweet_id=t["id_str"])
                    log.info("    posted (tweet %s)", new_id)
                else:
                    new_id = client.quote_tweet(text, t["id_str"])
                    log.info("    quoted (tweet %s)", new_id)
                _mark_engaged(state, mode, t["id_str"], score, text, dry_run)
                posted += 1
            except Exception as e:
                log.error("    FAILED to post %s: %s", mode, e)
        if posted >= max_posts:
            break  # one post per run

    save_state(state)
    log.info("%s cycle done: %d posted", mode.title(), posted)
    return posted


def run_reply_cycle(client, state, dry_run, max_replies=1) -> int:
    return run_followed_cycle(client, state, dry_run, max_replies, "reply")


def run_quote_cycle(client, state, dry_run, max_quotes=1) -> int:
    return run_followed_cycle(client, state, dry_run, max_quotes, "quote")


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
    p = argparse.ArgumentParser(description="Run a single engine cycle")
    p.add_argument("engine", choices=["reply", "quote", "content"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-posts", type=int, default=1)
    args = p.parse_args()

    c = XClient()
    st = load_state()
    if args.engine == "reply":
        run_reply_cycle(c, st, args.dry_run, args.max_posts)
    elif args.engine == "quote":
        run_quote_cycle(c, st, args.dry_run, args.max_posts)
    else:
        run_content_cycle(c, st, args.dry_run, args.max_posts)
