#!/usr/bin/env python3
"""Groq LLM helpers for the elxecutor autopilot: scoring, reply/content generation,
niche checking, and safety filtering. All calls go through the Groq OpenAI-compatible
chat completions endpoint (llama-3.3-70b-versatile)."""

import json
import logging
import os
import re
import time

import requests

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen/qwen3.6-27b")
VISION_INTERVAL = float(os.getenv("VISION_INTERVAL", "15"))
API_KEY = os.getenv("GROQ_API_KEY")

_vision_lock = None
_last_vision_at = 0.0

if not API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set in .env")

MAX_RETRIES = 3
RETRY_BACKOFF = 5

# Repetitive sentence shapes the persona should never collapse into. If a
# generated reply/quote matches one of these, it's regenerated (see
# _generate_varied) so posts don't read like a template.
_BANNED_TEMPLATES = (
    re.compile(r"what's with the\b", re.I),
    re.compile(r"is this .{0,30}\b(?:or something|then)\b\??", re.I),
    re.compile(r"looks kinda\b", re.I),
    re.compile(r"\bthat's (?:kinda|pretty|so|just)\s+\w+", re.I),
    re.compile(r"\bsounds like\b", re.I),
    re.compile(r"\bwhat's the (?:big deal|context)\b", re.I),
    re.compile(r"what kind of .{0,25}\b(?:are|is|does|did|would|do) (?:they|it|a|this)", re.I),
    re.compile(r"\bhow did they\b|\bhow are they\b|\bhow'd they\b", re.I),
    re.compile(r"what's powering\b", re.I),
    re.compile(r"\bunpopular opinion\b|\bhot take\b|\bam I the only one\b|\bhere's a thought\b", re.I),
    re.compile(r"\bsomethin\b|\btalkin\b|\bspeakin\b|\bgettin\b|\bdoin\b|\bcomin\b", re.I),
)

NICHE_KEYWORDS = [
    "electrical", "electronic", "electronics", "circuit", "circuits", "embedded",
    "microcontroller", "microcontroller", "arduino", "esp32", "fpga", "signal",
    "communication", "communications", "rf ", "antenna", "antennas", "semiconductor",
    "transistor", "materials", "power", "voltage", "current", "pcb", "schematic",
    "firmware", "kernel", "assembly", "asm ", "c code", "c programming", "low-level",
    "low level", "hardware", "sensor", "robotics", "dsp", "dsp", "os ", "operating system",
    "linux", "kernel", "oscilloscope", "multimeter", "capacitor", "resistor", "diode",
    "soldering", "breadboard", "iot ", "vintage computer", "retrocomputer", "processor",
    "cpu", "gpu", "chip", "silicon", "wafer", "lithography", "vls", "vlsi",
]


def _chat(system: str, user: str, max_tokens: int = 300, temperature: float = 0.7) -> str:
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
            if resp.status_code == 429:
                log.warning("Groq rate limited, retrying in %ss", RETRY_BACKOFF)
                time.sleep(RETRY_BACKOFF)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.warning("Groq call failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF)
    raise RuntimeError("Groq call failed after retries")


def _chat_vision(user_text: str, image_url: str, max_tokens: int = 200, temperature: float = 0.3) -> str:
    """Ask the vision model about a single image. Paced to respect per-model rate limits."""
    global _last_vision_at
    try:
        import threading
        global _vision_lock
        if _vision_lock is None:
            _vision_lock = threading.Lock()
        with _vision_lock:
            elapsed = time.time() - _last_vision_at
            if elapsed < VISION_INTERVAL:
                time.sleep(VISION_INTERVAL - elapsed)
            _last_vision_at = time.time()
            body = {
                "model": VISION_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                "max_completion_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": "none",
            }
            for attempt in range(MAX_RETRIES):
                try:
                    resp = requests.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                        json=body,
                        timeout=90,
                    )
                    if resp.status_code == 429:
                        log.warning("Vision model rate limited, retrying in %ss", RETRY_BACKOFF)
                        time.sleep(RETRY_BACKOFF)
                        continue
                    resp.raise_for_status()
                    return resp.json()["choices"][0]["message"]["content"].strip()
                except Exception as e:
                    log.warning("Vision call failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF)
    except Exception as e:
        log.warning("Vision call error: %s", e)
    return ""


def describe_image(url: str) -> str:
    """Return a 1-2 sentence description of an image URL, or "" if vision is unavailable.

    Returns empty string (never None) so callers can rely on truthiness and always
    fall back to text-only commentary without extra branching."""
    try:
        return _chat_vision(
            "Describe what's in this image in 1-2 sentences, being concrete about any "
            "electronics, hardware, screens, batteries, charts, or engineering content. "
            "If it is not technical, just describe it plainly. Do not invent anything "
            "you cannot see.",
            url,
        )
    except Exception as e:
        log.warning("describe_image failed: %s", e)
        return ""


def describe_tweet(cache: dict, media_url: str, tweet_id: str) -> str:
    """Return a cached or freshly-computed description for a tweet's media.

    cache: the engine's state dict (image_descriptions key persists across runs).
    media_url: the tweet's first media URL (photo or video thumbnail), or "".
    Returns "" when there is no media, when already-failed, or when vision is down."""
    if not media_url:
        return ""
    cache.setdefault("image_descriptions", {})
    entry = cache["image_descriptions"].get(str(tweet_id))
    if entry is not None:
        return entry.get("desc", "") if isinstance(entry, dict) else ""
    desc = describe_image(media_url)
    cache["image_descriptions"][str(tweet_id)] = {"desc": desc, "ts": time.time()}
    if desc:
        log.info("  described image for tweet %s: %.120s", tweet_id, desc)
    return desc


ARTICLE_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
ARTICLE_FETCH_TIMEOUT = 20
ARTICLE_TEXT_CAP = 2000


def fetch_article_text(url: str) -> str:
    """Fetch a linked article and strip it down to plain text. Returns "" on any failure."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": ARTICLE_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=ARTICLE_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        html = re.sub(r"<script.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 50:
            return ""
        return text[:ARTICLE_TEXT_CAP]
    except Exception as e:
        log.warning("fetch_article_text failed for %s: %s", url[:60], e)
        return ""


def summarize_article(article_text: str) -> str:
    """Summarize article text into 2-3 concrete sentences. Returns "" on failure."""
    if not article_text.strip():
        return ""
    system = (
        "Summarize the key technical facts of this article in 2-3 plain sentences. "
        "Focus on what was built, how, and any notable tradeoffs. Be concrete and "
        "specific (name components, techniques, measurements). Do NOT invent details "
        "not in the text. Return ONLY the summary."
    )
    try:
        return _chat(system, article_text[:1800], max_tokens=200, temperature=0.3)
    except Exception as e:
        log.warning("summarize_article failed: %s", e)
        return ""


def article_context(cache: dict, urls: list, tweet_text: str, tweet_id: str,
                    inline_article: str = "") -> str:
    """Return a cached or freshly-computed summary for a tweet's article.

    When `inline_article` is provided (X Article body already extracted from the
    GraphQL response), it is summarized directly — no network fetch, and it
    bypasses the thin-caption gate. Otherwise only reads articles for thin-caption
    tweets (the URL dominates), so rich captions don't trigger expensive fetches.
    Returns "" when nothing qualifies or fetching/summarizing fails (silent
    fallback to text-only commentary)."""
    if not inline_article and not urls:
        return ""
    cache.setdefault("article_summaries", {})
    if inline_article:
        key = f"inline:{tweet_id}"
    else:
        caption = re.sub(r"https?://\S+", "", tweet_text).strip()
        if len(caption) >= 40:
            return ""
        key = urls[0]["expanded_url"]
    entry = cache["article_summaries"].get(key)
    if entry is not None:
        return entry.get("summary", "") if isinstance(entry, dict) else ""
    text = inline_article or fetch_article_text(key)
    summary = summarize_article(text) if text else ""
    cache["article_summaries"][key] = {"summary": summary, "ts": time.time(),
                                       "url": key, "tweet_id": str(tweet_id)}
    if summary:
         log.info("  summarized article for tweet %s (%s): %.120s",
                  tweet_id, key[:50], summary)
    return summary


def _generate_varied(system: str, user: str, max_tokens: int = 160) -> str:
    """Generate text, retrying up to a few times if it collapses into a banned template."""
    for attempt in range(3):
        text = _chat(system, user, max_tokens=max_tokens, temperature=1.0)
        text = text.strip().strip('"')
        if attempt < 2 and any(p.search(text) for p in _BANNED_TEMPLATES):
            log.info("  regenerating (templated output): %.100s", text)
            system += ("\n\nThat last reply was too repetitive/templated. Rewrite it "
                       "with a genuinely different structure — vary whether it ends in "
                       "a question, and drop the forced slang.")
            continue
        return text
    return text


def generate_reply(tweet_text: str, author_name: str, author_bio: str, image_desc: str = "", article: str = "", thread_context: str = "") -> str:
    """Generate a genuine, in-voice reply from the elxecutor persona.

    When `thread_context` is provided, it is the preceding reply chain (oldest
    first) leading up to this tweet, so the reply can reference earlier turns
    instead of treating the tweet in isolation."""
    system = (
        "You are @elxecutor, a 19-year-old EEE student at OAU, Nigeria who posts as a "
        "real person. You are texting someone you follow on X. Write like a real human "
        "scrolling at 1am, not a bot.\n\n"
        "VARIETY — the single most important rule. Real people do not use one sentence "
        "shape every time, so neither should you. Rotate naturally between different "
        "kinds of replies:\n"
        "  - a specific question about the ACTUAL thing in the tweet (never generic)\n"
        "  - a plain statement of opinion ('this is so overengineered and i love it')\n"
        "  - a tangent or memory the tweet triggers\n"
        "  - a dry one-line joke\n"
        "  - agreement or pushback tied to a concrete detail\n"
        "Sometimes one sentence, sometimes two, sometimes a half-finished thought. Do "
        "NOT end every reply with a question — a good number of your replies should be "
        "statements with no question at all.\n\n"
        "BE SPECIFIC. Reference a real detail from the tweet — a number, a part, a "
        "phrase, what the image actually shows. Vague filler ('that's pretty cool', "
        "'sounds interesting', 'looks cool') is forbidden.\n\n"
        "WRITE NORMALLY, not as a dialect caricature. Use contractions where natural, "
        "but never cram slang onto every word: write 'something', not 'somethin'; "
        "'talking', not 'talkin'. One casual dropped letter now and then is fine, but "
        "never on every post.\n\n"
        "FORBIDDEN TEMPLATES (these repeat and instantly out you as a bot):\n"
        "- opening with 'what's with the ...', 'is this ... or something?', 'looks kinda', "
        "'that's' + adjective, 'sounds like'\n"
        "- opening with 'The use of', 'The prospect of', 'As an EEE student', 'It's "
        "interesting/intriguing how', 'I wonder about'\n"
        "- 'allows for', 'tradeoffs', 'implications for', 'is widely used', 'could have "
        "significant implications'\n"
        "- Never name a chip/part just to name one — only mention a component if it's "
        "the actual thing in the tweet or genuinely relevant to what you'd do.\n"
        "- No lists, no structured 'first... second...', no perfect balanced sentences.\n\n"
        "If the tweet includes an image, you may naturally reference what it shows, but "
        "NEVER invent visual details beyond what is provided. If the tweet links to an "
        "article, you may reference its content, but NEVER invent facts beyond the "
        "summary provided. "
        "Max 280 chars. No hashtags, no emojis. Never mention being an AI. Return ONLY "
        "the reply text."
    )
    user = (
        f"Tweet from @{author_name} (bio: {author_bio[:120]}):\n\n"
        f'"{tweet_text[:500]}"'
    )
    if thread_context:
        user += f"\n\nThis is the conversation so far (oldest first):\n{thread_context[:1500]}"
    if image_desc:
        user += f'\n\nThe tweet includes an image which shows: {image_desc[:300]}'
    if article:
        user += f'\n\nThe tweet links to an article which is about: {article[:400]}'
    user += "\n\nWrite your reply:"
    return _generate_varied(system, user)


def generate_quote(tweet_text: str, author_name: str, image_desc: str = "", article: str = "", thread_context: str = "") -> str:
    """Generate a short original commentary for a quote-post of someone else's tweet.

    When `thread_context` is provided, it is the preceding reply chain (oldest
    first) leading up to this tweet.

    HONESTY RULES: the account is a real person; never claim to have done a build,
    measurement, teardown, or experiment that was not actually performed. No invented
    first-person results, no fake part numbers. Frame instead as opinions, questions,
    observations about known/public facts, or things the author is curious about.
    """
    system = (
        "You are @elxecutor, a 19-year-old EEE student at OAU, Nigeria who posts as a "
        "real person. You are quoting someone's tweet and adding ONE short take on top "
        "of it (max 280 chars, 1-3 sentences).\n\n"
        "VARIETY — the single most important rule. Real people don't use one sentence "
        "shape every time, so neither should you. Vary naturally between:\n"
        "  - a hot take that pushes back on the tweet\n"
        "  - a specific question about the ACTUAL thing in the tweet (never generic)\n"
        "  - a plain statement of opinion or a connection to something you're into\n"
        "  - a dry, blunt one-liner\n"
        "Sometimes end with a question, sometimes don't — a good share of your quote-"
        "takes should be statements with no question at all.\n\n"
        "BE SPECIFIC. Reference a real detail from the tweet — a number, a part, a "
        "claim, what the image shows. Vague filler ('that's pretty cool', 'sounds "
        "interesting', 'looks cool') is forbidden.\n\n"
        "WRITE NORMALLY, not as a dialect caricature. Use contractions where natural, "
        "but never cram slang onto every word: write 'something', not 'somethin'; "
        "'talking', not 'talkin'. One casual dropped letter now and then is fine, but "
        "never on every post.\n\n"
        "FORBIDDEN TEMPLATES (these repeat and instantly out you as a bot):\n"
        "- opening with 'what's with the ...', 'is this ... or something?', 'looks kinda', "
        "'that's' + adjective, 'sounds like'\n"
        "- opening with 'The use of', 'The prospect of', 'It's interesting/intriguing "
        "that', 'This is', 'As an EEE student', 'I wonder'\n"
        "- 'allows for', 'tradeoffs', 'implications for', 'is widely used', 'could have "
        "significant implications', 'in such projects/designs'\n"
        "- Never name a chip/part just to name one — only mention hardware if it's the "
        "actual thing in the tweet.\n"
        "- No structured, perfectly-balanced, evenly-hyphenated sentences. No lists.\n\n"
        "If the tweet includes an image, you may naturally reference what it shows, but "
        "NEVER invent visual details beyond what is provided. If the tweet links to an "
        "article, you may reference its content, but NEVER invent facts beyond the "
        "summary provided. "
        "CRITICAL HONESTY RULES: never claim in first person to have performed a build, "
        "teardown, measurement, or experiment (no 'I reverse engineered...', no 'I "
        "measured...', no invented results or specific numbers from your own tests). "
        "Opinions, questions, widely-known public facts, and plans are fine. "
        "Do NOT repeat or summarize the quoted tweet. No clickbait, no hashtags, no "
        "emoji-stuffing. Do NOT say you are an AI. Return ONLY the commentary text."
    )
    user = (
        f"Tweet by @{author_name}:\n\n"
        f'"{tweet_text[:500]}"'
    )
    if thread_context:
        user += f"\n\nThis is the conversation so far (oldest first):\n{thread_context[:1500]}"
    if image_desc:
        user += f'\n\nThe tweet includes an image which shows: {image_desc[:300]}'
    if article:
        user += f'\n\nThe tweet links to an article which is about: {article[:400]}'
    user += "\n\nWrite your quote commentary:"
    return _generate_varied(system, user)


def generate_content(topic: str, context: str = "") -> str:
    """Generate an original build-in-public tweet tying a trending topic to the EEE niche.

    HONESTY RULES: the account is a real person; never claim to have done a build,
    measurement, teardown, or experiment that was not actually performed. No invented
    first-person results, no fake part numbers, no made-up numbers from personal tests.
    Frame instead as questions, opinions, observations about known/public facts, or
    things the author is curious about or planning to try.
    """
    system = (
        "You are @elxecutor, an EEE student at OAU, Nigeria, 19 years old. You post original "
        "content at the intersection of communication engineering, electronic materials, and "
        "low-level software/hardware. Write ONE short original tweet (max 280 chars) that "
        "ranks high in the feed algorithm, which rewards engagement (favorites, replies, "
        "reposts, shares, dwell time) and originality, and penalizes spam and slop. "
        "So write a tweet that: (1) opens with a SPECIFIC, concrete hook — a real component, "
        "tradeoff, or public fact — that makes people stop scrolling; (2) states a "
        "genuine opinion or counterintuitive observation (drives replies/reposts); "
        "(3) optionally ends with a real question to invite replies; (4) is written from a "
        "real student voice with a specific detail, NOT a generic thought. "
        "CRITICAL HONESTY RULES: never claim to have performed a build, teardown, "
        "measurement, test, or experiment in first person (no 'I reverse engineered...', "
        "no 'I measured...', no fake personal results or invented specific numbers). You "
        "may ask questions, give opinions, cite widely-known public facts, or say you are "
        "curious/planning to try something — but do NOT fabricate personal experience. "
        "No clickbait, no emoji-stuffing, no hashtags at all. Do NOT say you are an AI. "
        "Return ONLY the tweet text."
    )
    user = f"Topic: {topic}\n\nRelevant context:\n{context[:400]}\n\nWrite the tweet:"
    return _generate_varied(system, user, max_tokens=180)


def honesty_check(text: str) -> tuple[bool, str]:
    """Reject tweets that fabricate personal experience/measurements the author didn't do.

    The account is a student who posts opinions, questions, and observations — not lab
    results. So ANY first-person claim of a specific performed build/teardown/measurement/
    test with concrete results is treated as a fabrication risk and blocked.

    Returns (ok, reason). Blocks first-person claims of builds, teardowns, measurements,
    tests, or experiments with specific invented results, plus made-up part numbers.
    """
    system = (
        "You review a short tweet for factual honesty. The author is a student who posts "
        "opinions, questions, and observations about electronics — they do NOT perform "
        "builds, teardowns, measurements, or tests in first person. "
        "Return ONLY JSON with these fields: "
        '"ok": a boolean, "reason": a string (quoted). '
        "Set ok=false if the tweet claims in first person to have performed a specific "
        "physical task with concrete results — e.g. 'I reverse engineered...', 'I measured "
        "...', 'I built ...', 'my oscilloscope showed...', 'my test found...' — even if "
        "the claim sounds plausible. Such claims would be fabricated because the author "
        "does not do hands-on experiments for their posts. "
        "Set ok=true for opinions, genuine questions ('has anyone tried...?'), plans "
        "('I want to try...'), and widely-known public facts. "
        "Do NOT invent a reason; keep it short and factual."
    )
    try:
        raw = _chat(system, text[:500], max_tokens=120, temperature=0.1)
    except RuntimeError:
        return True, "LLM unavailable"
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return True, "parse failed"
    try:
        d = json.loads(m.group(0))
        ok = d.get("ok", True)
        reason = d.get("reason", "")
        return bool(ok), str(reason)
    except Exception:
        return True, "parse failed"


def x_algo_score(text: str) -> dict:
    """Mirror the grox BangerInitialScreen + SpamSystemLowFollower classifiers used by the
    X For You algorithm. Returns a dict with quality_score (0-1), slop_score (0-1),
    spam (bool) and a short reason. High-algo content: quality >= 0.4, low slop, not spam."""
    system = (
        "You are a content-quality classifier for a social feed ranking system. "
        "Given a tweet, return ONLY JSON with these fields: "
        '"quality_score": a float 0-1 rating how original, substantive, and engaging the '
        "tweet is (specific technical detail, genuine insight or opinion, a hook that makes "
        "people want to click/reply/repost). Generic platitudes, low-effort engagement bait, "
        "news rehashes, and vague fluff score low. "
        '"slop_score": a float 0-1 rating how sloppy/low-effort the tweet is (emojis-for-clout, '
        "hollow motivational content, empty engagement farming). "
        '"spam": true or false whether this looks like spam (promotional, link-bait, '
        'begging for follows/retweets, irrelevant). '
        '"reason": one short sentence explaining the quality_score. '
        "Return ONLY the JSON object, no markdown."
    )
    try:
        raw = _chat(system, text[:500], max_tokens=200, temperature=0.1)
    except RuntimeError:
        return {"quality_score": 0.5, "slop_score": 0.5, "spam": False, "reason": "LLM unavailable"}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"quality_score": 0.5, "slop_score": 0.5, "spam": False, "reason": "parse failed"}
    try:
        d = json.loads(m.group(0))
        d["quality_score"] = float(d.get("quality_score", 0.5))
        d["slop_score"] = float(d.get("slop_score", 0.5))
        d["spam"] = bool(d.get("spam", False))
        return d
    except Exception:
        return {"quality_score": 0.5, "slop_score": 0.5, "spam": False, "reason": "parse failed"}


def content_passes_algo(text: str) -> tuple[bool, dict]:
    """Gate original content on the X algorithm's banger threshold (quality >= 0.4, non-spam)."""
    s = x_algo_score(text)
    ok = (s["quality_score"] >= 0.4) and (not s["spam"]) and (s["slop_score"] < 0.8)
    return ok, s


def reply_rank_ok(text: str) -> tuple[bool, dict]:
    """Mirror grox ReplyScorer (0-3 score; 0.0 => auto-labeled spam). Reject low scores."""
    system = (
        "Rate how this reply will rank in a reply-ranking system. A high-scoring reply "
        "(3) is specific, adds real value, asks a genuine question or gives a concrete "
        "technical observation. A score of 0 means spammy or worthless (link-bait, "
        "flattery, 'great post!', irrelevant). Return ONLY JSON: "
        '{"score": 0-3 integer, "reason": one short sentence}.'
    )
    try:
        raw = _chat(system, text[:500], max_tokens=120, temperature=0.1)
    except RuntimeError:
        return True, {"score": 2, "reason": "LLM unavailable"}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return True, {"score": 2, "reason": "parse failed"}
    try:
        d = json.loads(m.group(0))
        d["score"] = int(d.get("score", 2))
        return d["score"] >= 1, d
    except Exception:
        return True, {"score": 2, "reason": "parse failed"}


def is_niche(text: str) -> bool:
    """Keyword pre-filter: is this plausibly in the EEE/systems niche? (cheap gate before LLM)"""
    low = text.lower()
    for kw in NICHE_KEYWORDS:
        if kw in low:
            return True
    return False


def niche_check_llm(text: str) -> bool:
    """LLM-based niche check with strict yes/no.

    Only returns True when the PRIMARY subject is the EEE/hardware niche.
    Rejects lifestyle/anecdotal content that merely mentions an engineering
    keyword (e.g. 'my friend graduated in Mechatronics', 'my phone privacy')."""
    system = (
        "Answer only 'yes' or 'no'. Is the PRIMARY subject of this text about electrical/"
        "electronic engineering, communication engineering, electronic materials, embedded "
        "systems, low-level programming (C/assembly/kernels), or hardware design/builds? "
        "Reply 'no' if the niche is only a passing mention or a detail (e.g. someone's "
        "academic field, a phone brand, a random gadget in a personal story) rather than "
        "the actual topic being discussed."
    )
    try:
        ans = _chat(system, text[:500], max_tokens=5, temperature=0).lower()
    except RuntimeError:
        return False
    return "yes" in ans


def safety_check(text: str) -> tuple[bool, str]:
    """Reject spammy/toxic content before posting. Returns (ok, reason)."""
    low = text.lower()
    if re.search(r"\b(follow me|like and retweet|like and share|dm me|check my bio)\b", low):
        return False, "spam pattern"
    if "#" in low:
        return False, "contains a hashtag"
    if len(text) > 280:
        return False, "over 280 chars"
    if not text.strip():
        return False, "empty"
    return True, "ok"
