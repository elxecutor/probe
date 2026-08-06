#!/usr/bin/env python3
"""Daily EEE digest — finds interesting posts for you to engage with manually."""

import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import requests
from x_client import XClient

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
API_KEY = os.getenv("GROQ_API_KEY")
USERNAME = "elxecutor"


def groq_eecs_check(text):
    prompt = (f"Is this tweet about electrical/electronic engineering: power systems, communication engineering, "
              f"control systems, instrumentation, robotics, electronic materials/devices, circuits, signal processing, "
              f"or electrical machines? Answer only yes or no:\n\n\"{text[:200]}\"")
    try:
        resp = requests.post(GROQ_URL,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 10, "temperature": 0},
            timeout=30)
        ans = resp.json()["choices"][0]["message"]["content"].strip().lower()
        return "yes" in ans
    except Exception:
        return False


def main():
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
        if groq_eecs_check(t["full_text"]):
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
    # Pick a few that seem substantive
    candidates = [t for t in tl_tweets if len(t["full_text"]) > 60 and t["author_screen_name"] != USERNAME]
    random.shuffle(candidates)
    for t in candidates[:5]:
        print(f"  @{t['author_screen_name']}: {t['full_text'][:120]}…")
        print(f"    https://x.com/{t['author_screen_name']}/status/{t['id_str']}\n")

    print("---")
    print("Pick one or two posts above and reply with YOUR thoughts — not AI's.")
    print("That's how you build real connections.\n")


if __name__ == "__main__":
    main()
