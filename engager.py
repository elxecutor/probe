#!/usr/bin/env python3
"""elxecutor autopilot main loop.

One run posts AT MOST ONE tweet — alternating between a reply and a quote.
No bursts. Reply/quote cycles each pick a single best candidate and post it,
so a scheduled run (or --once) results in a single natural post. Daily caps
enforced via state.json. Use --dry-run to preview without posting.

Usage:
  python engager.py [--dry-run] [--once]
  python engager.py --dry-run --once          # one preview cycle, post nothing
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from x_client import XClient  # noqa: E402
import reply_engine  # noqa: E402
import quote_engine  # noqa: E402
from state import load_state, save_state, roll_daily  # noqa: E402

log = logging.getLogger("engager")


def main():
    ap = argparse.ArgumentParser(description="elxecutor X autopilot (one post per run)")
    ap.add_argument("--dry-run", action="store_true", help="preview actions without posting")
    ap.add_argument("--once", action="store_true", help="run one cycle then exit")
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

        engine = state.get("next_engine", "reply")
        replies_cap_left = state["replies_today"] < args.daily_reply_cap
        quotes_cap_left = state["content_today"] < args.daily_quote_cap

        if engine == "reply" and replies_cap_left:
            state["replies_today"] += reply_engine.run_reply_cycle(
                client, state, args.dry_run, 1)
        elif engine == "quote" and quotes_cap_left:
            state["content_today"] += quote_engine.run_quote_cycle(
                client, state, args.dry_run, 1)
        elif quotes_cap_left:
            state["content_today"] += quote_engine.run_quote_cycle(
                client, state, args.dry_run, 1)
        elif replies_cap_left:
            state["replies_today"] += reply_engine.run_reply_cycle(
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
        import time
        time.sleep(3600)
        cycle()


if __name__ == "__main__":
    main()
