#!/usr/bin/env python3
"""elxecutor autopilot CLI.

One run posts AT MOST ONE tweet — alternating between a reply and a quote.
No bursts. Reply/quote cycles each pick a single best candidate and post it,
so a scheduled run (or --once) results in a single natural post. Daily caps
enforced via state.json. Use --dry-run to preview without posting.

Subcommands:
  (default)         autopilot loop — one reply/quote post per run
  --preflight       cheap fresh-candidate scan (CI gate; read-only, no model load)
  --digest          daily digest of posts worth engaging with manually
  --content         standalone original-content cycle (trending niche tweets)

Usage:
  python engager.py [--dry-run] [--once]
  python engager.py --preflight
  python engager.py --digest
  python engager.py --content [--dry-run] [--max-tweets N]
"""

import argparse
import logging
import os
import random
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from x_client import XClient  # noqa: E402
import engines  # noqa: E402
import llm  # noqa: E402
from state import load_state, save_state, roll_daily, is_new_tweet, already_engaged  # noqa: E402

log = logging.getLogger("engager")


# --- Autopilot loop ----------------------------------------------------------

def _autopilot(args):
    client = XClient()
    state = load_state()

    def cycle():
        roll_daily(state)
        save_state(state)

        engine = state.get("next_engine", "reply")
        replies_cap_left = state["replies_today"] < args.daily_reply_cap
        quotes_cap_left = state["content_today"] < args.daily_quote_cap

        # Notifications first: answering people who replied to/mentioned our own
        # posts is the highest-signal engagement, so it takes the one-post slot
        # for this run when there's anything fresh and unanswered.
        if replies_cap_left:
            posted = engines.run_notification_cycle(
                client, state, args.dry_run, 1)
            if posted:
                state["replies_today"] += posted
                # Alternate next run's engine so replies/quotes stay mixed.
                state["next_engine"] = "quote" if engine == "reply" else "reply"
                save_state(state)
                return

        if engine == "reply" and replies_cap_left:
            state["replies_today"] += engines.run_reply_cycle(
                client, state, args.dry_run, 1)
        elif engine == "quote" and quotes_cap_left:
            state["content_today"] += engines.run_quote_cycle(
                client, state, args.dry_run, 1)
        elif quotes_cap_left:
            state["content_today"] += engines.run_quote_cycle(
                client, state, args.dry_run, 1)
        elif replies_cap_left:
            state["replies_today"] += engines.run_reply_cycle(
                client, state, args.dry_run, 1)
        else:
            log.info("Both daily caps reached (%d replies / %d quotes).",
                     args.daily_reply_cap, args.daily_quote_cap)

        # Alternate next run's engine so replies and quotes stay mixed.
        state["next_engine"] = "quote" if engine == "reply" else "reply"
        save_state(state)

    log.info("elxecutor autopilot starting (dry_run=%s, one post per run)", args.dry_run)
    cycle()

    if args.once:
        log.info("--once set, exiting after one cycle.")
        return

    while True:
        time.sleep(3600)
        cycle()


# --- Preflight ---------------------------------------------------------------

MIN_TEXT_LEN = 30
TWEETS_PER_ACCOUNT = 3   # newest few is enough to detect "anything fresh"
ACCOUNTS_PER_RUN = 24    # scan the whole follow list — preflight is cheap
FRESH_ACCOUNTS_FILE = "fresh_accounts.txt"


def _write_fresh_accounts(account_ids) -> None:
    """Persist which accounts had fresh candidates so the engines can prioritize
    them. The engines only sample a subset of followed accounts per run, so this
    makes sure what preflight spotted is actually within reach of the engager."""
    with open(FRESH_ACCOUNTS_FILE, "w") as f:
        for acc in sorted(account_ids):
            f.write(f"{acc}\n")


def preflight():
    """Lightweight fresh-candidate scan for the workflow, run BEFORE the heavy
    steps: no Phoenix model, no Groq client, no generation code — only x_client +
    state. Prints has_candidates=1/0 so the workflow can gate the expensive steps.
    Read-only: never advances the heartbeat, so a tweet spotted here is still
    eligible for the engine it dispatches to."""
    client = XClient()
    state = load_state()

    following = {u["id_str"]: u for u in client.get_all_following()}
    accounts = list(following.values())
    random.shuffle(accounts)
    accounts = accounts[:ACCOUNTS_PER_RUN]

    found = 0
    fresh_ids = set()
    for u in accounts:
        try:
            tweets, _ = client.get_user_tweets(u["id_str"], count=TWEETS_PER_ACCOUNT)
        except Exception as e:
            log.warning("  get_user_tweets(%s) failed: %s", u.get("screen_name"), e)
            continue
        account_fresh = False
        for t in tweets:
            if t["author_screen_name"] == "elxecutor":
                continue
            if not is_new_tweet(state, u["id_str"], t["id_str"]):
                continue
            if len(t["full_text"]) < MIN_TEXT_LEN and not t.get("article_text"):
                continue
            if already_engaged(state, t["id_str"]):
                continue
            account_fresh = True
            found += 1
            log.info("  fresh candidate: @%s %s %.60s",
                     t["author_screen_name"], t["id_str"], t["full_text"])
        if account_fresh:
            fresh_ids.add(u["id_str"])
        time.sleep(0.3)
        if found >= 5:
            break
    _write_fresh_accounts(fresh_ids)

    # Also flag fresh replies/mentions on our own posts, so quiet runs that have
    # nothing new from followed accounts still trigger the notification cycle.
    notif_replyable = engines._notification_candidates(client, state)
    for t in notif_replyable[:3]:
        log.info("  fresh reply to answer: @%s %s %.60s",
                 t["author_screen_name"], t["id_str"], t["full_text"])

    ok = found > 0 or bool(notif_replyable)
    print(f"has_candidates={'1' if ok else '0'}")
    with open("preflight.txt", "w") as f:
        f.write("1" if ok else "0")
    return 0


# --- Daily digest ------------------------------------------------------------

USERNAME = "elxecutor"


def digest():
    """Daily EEE digest — finds interesting posts for you to engage with manually."""
    print("=== Daily EEE Digest (@elxecutor) ===\n")
    client = XClient()
    me = client.get_me()
    print(f"Logged in as @{me['screen_name']}\n")

    # 1. Scan for-you timeline for EEE accounts worth following
    print("--- For You: EEE accounts to follow ---")
    fy_tweets, _ = client.get_for_you_timeline(count=80)
    random.shuffle(fy_tweets)
    found = 0
    for t in fy_tweets:
        if t["author_screen_name"] == USERNAME:
            continue
        if llm.niche_check_llm(t["full_text"]):
            print(f"  @{t['author_screen_name']}: {t['full_text'][:80]}…")
            found += 1
            if found >= 5:
                break
    if not found:
        print("  (none found today)")

    # 2. Scan following timeline for posts worth replying to
    print("\n--- Following: posts worth engaging with ---")
    tl_tweets, _ = client.get_timeline(count=60)
    following_ids = {u["id_str"] for u in client.get_all_following()}
    tl_tweets = [t for t in tl_tweets if t["author_id"] in following_ids]
    candidates = [t for t in tl_tweets if len(t["full_text"]) > 60 and t["author_screen_name"] != USERNAME]
    random.shuffle(candidates)
    for t in candidates[:5]:
        print(f"  @{t['author_screen_name']}: {t['full_text'][:120]}…")
        print(f"    https://x.com/{t['author_screen_name']}/status/{t['id_str']}\n")

    print("---")
    print("Pick one or two posts above and reply with YOUR thoughts — not AI's.")
    print("That's how you build real connections.\n")
    return 0


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="elxecutor X autopilot (one post per run)")
    ap.add_argument("--preflight", action="store_true",
                    help="cheap fresh-candidate scan, exit (CI gate)")
    ap.add_argument("--digest", action="store_true",
                    help="print today's digest of posts worth engaging with, exit")
    ap.add_argument("--content", action="store_true",
                    help="run the standalone original-content cycle, exit")
    ap.add_argument("--dry-run", action="store_true", help="preview actions without posting")
    ap.add_argument("--once", action="store_true", help="run one cycle then exit")
    ap.add_argument("--max-tweets", type=int, default=2, help="content cycle limit")
    ap.add_argument("--daily-reply-cap", type=int,
                    default=int(os.getenv("DAILY_REPLY_CAP", "20")))
    ap.add_argument("--daily-quote-cap", type=int,
                    default=int(os.getenv("DAILY_QUOTE_CAP", "6")))
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "engager.log")),
        ],
    )

    if args.preflight:
        return preflight()
    if args.digest:
        return digest()
    if args.content:
        client = XClient()
        state = load_state()
        engines.run_content_cycle(client, state, args.dry_run, args.max_tweets)
        return 0
    _autopilot(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
