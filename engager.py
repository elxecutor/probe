#!/usr/bin/env python3
"""elxecutor autopilot main loop.

Splits activity between replies to followed accounts and quote-posts of interesting
tweets. No original personal tweet generation (handled manually by the owner).
  - Reply engine: every REPLY_INTERVAL seconds (default 2h)
  - Quote engine: every QUOTE_INTERVAL seconds (default 6h)
Daily caps enforced via state.json. Use --dry-run to preview without posting.

Usage:
  python engager.py [--dry-run] [--once]
  python engager.py --dry-run --once          # one preview cycle, post nothing
  python engager.py --reply-interval 3600     # run replies hourly
"""

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from x_client import XClient  # noqa: E402
import reply_engine  # noqa: E402
import quote_engine  # noqa: E402
from state import load_state, save_state, roll_daily  # noqa: E402

log = logging.getLogger("engager")


def main():
    ap = argparse.ArgumentParser(description="elxecutor X autopilot (replies + quotes)")
    ap.add_argument("--dry-run", action="store_true", help="preview actions without posting")
    ap.add_argument("--once", action="store_true", help="run one cycle then exit")
    ap.add_argument("--reply-interval", type=int,
                    default=int(os.getenv("REPLY_INTERVAL", "7200")), help="seconds between reply cycles")
    ap.add_argument("--quote-interval", type=int,
                    default=int(os.getenv("QUOTE_INTERVAL", "21600")), help="seconds between quote cycles")
    ap.add_argument("--max-replies", type=int,
                    default=int(os.getenv("MAX_REPLIES_PER_RUN", "5")))
    ap.add_argument("--max-quotes", type=int,
                    default=int(os.getenv("MAX_QUOTES_PER_RUN", "3")))
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

    client = XClient()
    state = load_state()

    def cycle():
        roll_daily(state)
        save_state(state)

        if state["replies_today"] < args.daily_reply_cap:
            n = min(args.max_replies, args.daily_reply_cap - state["replies_today"])
            state["replies_today"] += reply_engine.run_reply_cycle(
                client, state, args.dry_run, n)
            save_state(state)
        else:
            log.info("Reply daily cap reached (%d).", args.daily_reply_cap)

        if state["content_today"] < args.daily_quote_cap:
            n = min(args.max_quotes, args.daily_quote_cap - state["content_today"])
            state["content_today"] += quote_engine.run_quote_cycle(
                client, state, args.dry_run, n)
            save_state(state)
        else:
            log.info("Quote daily cap reached (%d).", args.daily_quote_cap)

    log.info("elxecutor autopilot starting (dry_run=%s, reply every %ss, quote every %ss)",
             args.dry_run, args.reply_interval, args.quote_interval)
    cycle()

    if args.once:
        log.info("--once set, exiting after one cycle.")
        return

    next_reply = time.time() + args.reply_interval
    next_quote = time.time() + args.quote_interval
    while True:
        time.sleep(15)
        now = time.time()
        if now >= next_reply:
            cycle()
            next_reply = now + args.reply_interval
            next_quote = max(next_quote, now + 60)
        elif now >= next_quote:
            cycle()
            next_quote = now + args.quote_interval
            next_reply = max(next_reply, now + 60)


if __name__ == "__main__":
    main()
