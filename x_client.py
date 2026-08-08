import base64
import hashlib
import logging
import os
import random
import re
import time
import json
from functools import reduce

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

API_BASE = "https://api.x.com/1.1"
GRAPHQL_BASE = "https://x.com/i/api/graphql"
REST_BASE = "https://x.com/i/api/1.1"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

QUERY_IDS = {
    "Following": "b8XpwALENnJdFSHchkK6rw",
    "RemoveFollower": "QpNfg0kpPRfjROQ_9eOLXA",
    "UserByScreenName": "Gb-d6r0vxPOADdG62OEBpQ",
    "UserByRestId": "xvmVfRLmnr1alc5f2dib0Q",
    "Viewer": "5XShkXk2oO2J7SYmTu6pvw",
    "Followers": "vJijlO_CM7dyGFNjDd7iqQ",
    "HomeTimeline": "3b9_7tltt0hJRef-xm_3sw",
    "HomeLatestTimeline": "g9NSjyYXOBsmMiP9TmYGaA",
    "CreateTweet": "wUgPBh9hEKhMMGlg8uDuFw",
    "Likes": "BEthBswU1Bt209H5xptp4Q",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "Follow": "nYylIhG4f_mNxc5DGpmUkg",
    "UserTweets": "eoJ5zbv51Z_KVl81v9PmLQ",
    "UserTweetsAndReplies": "wc5DRl4VaW5lSqJ8YbftZQ",
    "TweetDetail": "559hs_YZNV4IgA3Z6zIIuw",
    "TweetResultByRestId": "LkId5Akr61BS6BmOIcffRg",
    "NotificationsTimeline": "2FvqvnMOYuY5EEh--vxdFQ",
    "Bookmarks": "aqjes8lRHRFG0HUglVTfNg",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xzyS1GN3a6cj-mQ",
    "GenericTimelineById": "BrGScxnisMdTXyeLScaEhQ",
}

FEATURES = {
    "articles_preview_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": False,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_profile_redirect_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_awards_web_tipping_enabled": False,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "tweet_with_visibility_results_prefer_gql_media_interstitial_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "verified_phone_label_enabled": False,
    "view_counts_everywhere_api_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "premium_content_api_read_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": False,
    "responsive_web_grok_share_attachment_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_grok_image_annotation_enabled": False,
    "responsive_web_grok_analysis_button_from_backend": False,
    "responsive_web_jetfuel_frame": False,
    "rweb_video_screen_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
}


class XClient:
    def __init__(self, auth_token=None, ct0=None, user_id=None, username=None):
        self.auth_token = auth_token or os.getenv("X_AUTH_TOKEN")
        self.ct0 = ct0 or os.getenv("X_CT0")
        self.user_id = user_id or os.getenv("X_USER_ID")
        self.username = username or os.getenv("X_USERNAME")

        if not self.auth_token or not self.ct0:
            raise ValueError("X_AUTH_TOKEN and X_CT0 are required. Set them in .env or as environment variables.")

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "X-Csrf-Token": self.ct0,
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Client-Language": "en",
            "Content-Type": "application/json",
            "Origin": "https://x.com",
            "Referer": "https://x.com/home",
        })
        cookie_str = (
            f"auth_token={self.auth_token}; "
            f"ct0={self.ct0}; "
            f"twid=u%3D{self.user_id}; "
        )
        self.session.headers.update({"Cookie": cookie_str})

        self._fetch_session = requests.Session()
        self._fetch_session.headers.update({
            "User-Agent": USER_AGENT,
            "Cookie": cookie_str,
        })
        self._txid = XTransactionIdGenerator(session=self.session, fetch_session=self._fetch_session)

        self._me = None

    def _attach_txid(self, headers, method, path):
        if self._txid is None:
            return headers
        try:
            headers["X-Client-Transaction-Id"] = self._txid.generate(method, path)
        except Exception as e:
            log.warning("txid generation failed (%s); continuing without", e)
        return headers

    def _graphql_post(self, query_id, operation_name, variables, features=None):
        url = f"{GRAPHQL_BASE}/{query_id}/{operation_name}"
        path = f"/i/api/graphql/{query_id}/{operation_name}"
        payload = {"variables": variables}
        if features:
            payload["features"] = features
        headers = self._attach_txid({}, "POST", path)
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code == 429:
            log.warning("Rate limited. Waiting 60s...")
            time.sleep(60)
            return self._graphql_post(query_id, operation_name, variables, features)
        resp.raise_for_status()
        return resp.json()

    def _graphql_get(self, query_id, operation_name, variables, features=None, field_toggles=None):
        params = {"variables": json.dumps(variables)}
        if features:
            params["features"] = json.dumps(features)
        if field_toggles:
            params["fieldToggles"] = json.dumps(field_toggles)
        url = f"{GRAPHQL_BASE}/{query_id}/{operation_name}"
        path = f"/i/api/graphql/{query_id}/{operation_name}"
        headers = self._attach_txid({}, "GET", path)
        resp = self.session.get(url, params=params, headers=headers)
        if resp.status_code == 429:
            log.warning("Rate limited. Waiting 60s...")
            time.sleep(60)
            return self._graphql_get(query_id, operation_name, variables, features, field_toggles)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _normalize_user(result):
        legacy = result.get("legacy", {})
        core = result.get("core", {})
        rest_id = result.get("rest_id", "")
        return {
            "id_str": rest_id,
            "name": core.get("name", legacy.get("name", "")),
            "screen_name": core.get("screen_name", legacy.get("screen_name", "")),
            "description": legacy.get("description", ""),
            "location": legacy.get("location", ""),
            "url": legacy.get("url", ""),
            "followers_count": legacy.get("followers_count", 0),
            "friends_count": legacy.get("friends_count", 0),
            "statuses_count": legacy.get("statuses_count", 0),
            "verified": legacy.get("verified", False) or result.get("is_blue_verified", False),
            "ext_is_blue_verified": result.get("is_blue_verified", False),
        }

    def _extract_users_from_timeline(self, data):
        users = []
        try:
            instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
        except (KeyError, TypeError):
            try:
                instructions = data["data"]["user"]["result"]["timeline_response"]["timeline"]["instructions"]
            except (KeyError, TypeError):
                return users, None

        cursor = None
        for instruction in instructions:
            if instruction.get("type") == "TimelineAddEntries":
                for entry in instruction.get("entries", []):
                    content = entry.get("content", {})
                    entry_type = content.get("entryType", "")
                    if entry_type == "TimelineTimelineItem":
                        item_content = content.get("itemContent", {})
                        user_result = item_content.get("user_results", {}).get("result", {})
                        if user_result:
                            users.append(self._normalize_user(user_result))
                    elif entry_type == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            cursor = content.get("value")
        return users, cursor

    def get_me(self):
        if self._me is None:
            data = self._graphql_get(QUERY_IDS["Viewer"], "Viewer", variables={})
            try:
                result = data["data"]["viewer"]["user_results"]["result"]
                core = result.get("core", {})
                legacy = result.get("legacy", {})
                self._me = {
                    "id_str": result.get("rest_id", self.user_id),
                    "screen_name": core.get("screen_name", legacy.get("screen_name", self.username or "")),
                    "name": core.get("name", legacy.get("name", "")),
                    "friends_count": legacy.get("friends_count", 0),
                    "followers_count": legacy.get("followers_count", 0),
                    "description": legacy.get("description", ""),
                }
            except KeyError:
                self._me = {"id_str": self.user_id, "screen_name": self.username or ""}
        return self._me

    def get_following(self, count=100, cursor=None):
        variables = {
            "userId": self.user_id,
            "count": min(count, 100),
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_get(QUERY_IDS["Following"], "Following", variables, features=FEATURES)
        users, next_cursor = self._extract_users_from_timeline(data)
        return users, next_cursor

    def get_all_following(self, on_progress=None):
        all_users = []
        cursor = None
        page = 0

        while True:
            page += 1
            users, cursor = self.get_following(cursor=cursor)
            all_users.extend(users)

            if on_progress:
                on_progress(page, len(users), len(all_users))

            if not users or not cursor:
                break
            time.sleep(1.1)

        return all_users

    @staticmethod
    def _normalize_tweet(result):
        legacy = result.get("legacy", {})
        user_result = (result.get("core", {})
                       .get("user_results", {})
                       .get("result", {}))
        user_core = user_result.get("core", {})
        # Extract media info
        media_list = (legacy.get("extended_entities", {}).get("media", [])
                      or legacy.get("entities", {}).get("media", []))
        media_info = []
        for m in media_list:
            entry = {"type": m.get("type"), "url": m.get("media_url_https", "")}
            alt = m.get("ext_alt_text", "")
            if alt:
                entry["alt"] = alt
            if m.get("type") in ("video", "animated_gif"):
                variants = m.get("video_info", {}).get("variants", [])
                best = max((v for v in variants if v.get("bit_rate")),
                           key=lambda v: v.get("bit_rate", 0), default=None)
                if best:
                    entry["video_url"] = best.get("url", "")
            media_info.append(entry)

        # Extract URL entities (article/outbound links)
        url_info = []
        seen_urls = set()
        _SKIP_PREFIXES = ("https://pbs.twimg.com/", "https://abs.twimg.com/",
                          "https://x.com/i/status", "https://twitter.com/i/status",
                          "https://x.com/home", "https://twitter.com/home")
        for u in legacy.get("entities", {}).get("urls", []):
            expanded = u.get("expanded_url", "").strip()
            if not expanded or expanded in seen_urls:
                continue
            if any(expanded.startswith(p) for p in _SKIP_PREFIXES):
                continue
            seen_urls.add(expanded)
            url_info.append({
                "url": u.get("url", ""),
                "expanded_url": expanded,
                "display_url": u.get("display_url", ""),
            })
            if len(url_info) >= 2:
                break

        # X Articles: the caption (legacy.full_text) is just a teaser; the real
        # body lives in result.article.article_results.result.content_state.blocks.
        article_text = ""
        article_blocks = (result.get("article", {})
                          .get("article_results", {})
                          .get("result", {})
                          .get("content_state", {})
                          .get("blocks", []))
        if article_blocks:
            article_text = "\n".join(
                b.get("text", "") for b in article_blocks if b.get("text")
            ).strip()

        return {
            "id_str": result.get("rest_id", ""),
            "full_text": legacy.get("full_text", ""),
            "article_text": article_text,
            "created_at": legacy.get("created_at", ""),
            "conversation_id_str": legacy.get("conversation_id_str", ""),
            "author_id": user_result.get("rest_id", ""),
            "author_screen_name": user_core.get("screen_name", ""),
            "author_name": user_core.get("name", ""),
            "author_bio": user_result.get("profile_bio", {}).get("description", ""),
            "favorite_count": legacy.get("favorite_count", 0),
            "retweet_count": legacy.get("retweet_count", 0),
            "reply_count": legacy.get("reply_count", 0),
            "quote_count": legacy.get("quote_count", 0),
            "media": media_info,
            "urls": url_info,
        }

    def get_timeline(self, count=20, cursor=None):
        variables = {
            "count": min(count, 100),
            "enableRanking": False,
            "includePromotedContent": False,
            "requestContext": "launch",
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_post(QUERY_IDS["HomeLatestTimeline"], "HomeLatestTimeline", variables, features=FEATURES)
        return self._extract_tweets_from_timeline(data, ["data", "home", "home_timeline_urt"])

    def get_timeline_paged(self, count=80, max_pages=4, page_size=40):
        """Fetch multiple timeline pages so each cycle sees fresh tweets.

        The single-page get_timeline always returns the same first N tweets; walking the
        Bottom cursor advances past already-seen tweets. Returns (tweets, last_cursor).
        """
        tweets = []
        seen = set()
        cursor = None
        pages = 0
        while pages < max_pages:
            pages += 1
            batch, cursor = self.get_timeline(count=page_size, cursor=cursor)
            fresh = [t for t in batch if t["id_str"] not in seen]
            for t in fresh:
                seen.add(t["id_str"])
            tweets.extend(fresh)
            if not batch or not cursor:
                break
            if len(tweets) >= count:
                break
            time.sleep(1.0)
        return tweets, cursor

    def get_notifications(self, count=20, cursor=None):
        """Return (notifs, next_cursor). Each notif is a dict with:
          - id, element (notification kind, e.g. user_replied_to_your_tweet)
          - url, message, timestamp, tweet_id
          - tweet: normalized tweet for reply/mention notifications (else None)

        The reply/mention tweet is embedded directly in the entry
        (itemContent.tweet_results.result), so we can reply to it without a
        follow-up fetch.
        """
        variables = {"timeline_type": "All", "count": min(count, 40)}
        if cursor:
            variables["cursor"] = cursor
        data = self._graphql_get(QUERY_IDS["NotificationsTimeline"], "NotificationsTimeline", variables, features=FEATURES)
        notifs = []
        next_cursor = None
        try:
            path = ["data", "viewer_v2", "user_results", "result", "notification_timeline", "timeline"]
            obj = data
            for key in path:
                obj = obj[key]
            instructions = obj["instructions"]
        except (KeyError, TypeError):
            return notifs, None
        for instr in instructions:
            if instr.get("type") == "TimelineAddEntries":
                for entry in instr.get("entries", []):
                    content = entry.get("content", {})
                    if content.get("__typename") == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            next_cursor = content.get("value")
                        continue
                    if content.get("__typename") != "TimelineTimelineItem":
                        continue
                    item = content.get("itemContent", {})
                    element = (content.get("clientEventInfo") or {}).get("element", "")
                    # Reply/mention notifications embed the full tweet.
                    if item.get("__typename") == "TimelineTweet":
                        tweet_result = item.get("tweet_results", {}).get("result", {})
                        if not tweet_result or tweet_result.get("__typename") != "Tweet":
                            continue
                        t = self._normalize_tweet(tweet_result)
                        if not t.get("id_str"):
                            continue
                        notifs.append({
                            "id": entry.get("entryId", ""),
                            "element": element,
                            "url": (f"https://x.com/{t['author_screen_name']}/status/{t['id_str']}"),
                            "message": element.replace("_", " "),
                            "timestamp": entry.get("sortIndex", ""),
                            "tweet_id": t["id_str"],
                            "tweet": t,
                        })
                        continue
                    if item.get("__typename") != "TimelineNotification":
                        continue
                    notif = {
                        "id": item.get("id", ""),
                        "element": element,
                        "icon": item.get("notification_icon", ""),
                        "url": item.get("notification_url", {}).get("url", ""),
                        "message": item.get("rich_message", {}).get("text", ""),
                        "template_type": item.get("template", {}).get("__typename", ""),
                        "timestamp": entry.get("sortIndex", ""),
                        "tweet_id": None,
                        "tweet": None,
                    }
                    # Aggregate notifications (likes/retweets) may still carry a
                    # status URL; extract the id when present.
                    if "/status/" in notif["url"]:
                        notif["tweet_id"] = notif["url"].split("/status/")[-1].split("?")[0]
                    notifs.append(notif)
        return notifs, next_cursor

    @staticmethod
    def _created_tweet_id(data):
        try:
            result = data["data"]["create_tweet"]["tweet_results"]["result"]
        except (KeyError, TypeError):
            raise ValueError(f"CreateTweet failed: {json.dumps(data.get('errors', data))[:300]}")
        rid = result.get("rest_id")
        if not rid:
            raise ValueError(f"CreateTweet returned no tweet id: {json.dumps(data)[:300]}")
        return rid

    def create_tweet(self, text, reply_to_tweet_id=None, conversation_id=None):
        variables = {
            "tweet_text": text,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "disallowed_reply_options": None,
        }
        if reply_to_tweet_id:
            variables["reply"] = {
                "in_reply_to_tweet_id": reply_to_tweet_id,
                "exclude_reply_user_ids": [],
            }
            variables["conversation_id"] = conversation_id or reply_to_tweet_id

        data = self._graphql_post(QUERY_IDS["CreateTweet"], "CreateTweet", variables, features=FEATURES)
        return self._created_tweet_id(data)

    def quote_tweet(self, text, quoted_tweet_id):
        variables = {
            "tweet_text": text,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "disallowed_reply_options": None,
            "attachment_url": f"https://x.com/i/status/{quoted_tweet_id}",
            "dark_request": False,
        }
        data = self._graphql_post(QUERY_IDS["CreateTweet"], "CreateTweet", variables, features=FEATURES)
        return self._created_tweet_id(data)

    def _extract_tweets_from_timeline(self, data, path):
        tweets = []
        next_cursor = None
        try:
            obj = data
            for key in path:
                obj = obj[key]
            instructions = obj["instructions"]
        except (KeyError, TypeError):
            return tweets, None

        for instr in instructions:
            if instr.get("type") == "TimelineAddEntries":
                for entry in instr.get("entries", []):
                    if entry.get("entryId", "").startswith("promoted-"):
                        continue
                    if entry.get("content", {}).get("promotedMetadata"):
                        continue
                    content = entry.get("content", {})
                    if content.get("entryType") == "TimelineTimelineItem":
                        item = content.get("itemContent", {})
                        if item.get("itemType") == "TimelineTweet":
                            result = item.get("tweet_results", {}).get("result", {})
                            if result and result.get("__typename") == "Tweet":
                                tweets.append(self._normalize_tweet(result))
                    elif content.get("entryType") == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            next_cursor = content.get("value")
        return tweets, next_cursor

    def get_likes(self, user_id=None, count=100, cursor=None):
        uid = user_id or self.user_id
        variables = {
            "userId": uid,
            "count": min(count, 100),
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_get(QUERY_IDS["Likes"], "Likes", variables, features=FEATURES)
        path = ["data", "user", "result", "timeline", "timeline"]
        return self._extract_tweets_from_timeline(data, path)

    def get_article_body(self, tweet_id: str) -> str:
        """Fetch an X Article's body for a tweet via TweetResultByRestId.

        The UserTweets timeline query omits the article blocks, so article tweets
        seen as candidates need a follow-up fetch keyed on the tweet's rest id.
        Returns the joined block text, or "" when the tweet has no article."""
        variables = {
            "tweetId": str(tweet_id),
            "includePromotedContent": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
            "withCommunity": True,
        }
        field_toggles = {
            "withArticleRichContentState": True,
            "withArticlePlainText": False,
            "withArticleSummaryText": True,
            "withArticleVoiceOver": True,
        }
        data = self._graphql_get(QUERY_IDS["TweetResultByRestId"],
                                 "TweetResultByRestId", variables, features=FEATURES,
                                 field_toggles=field_toggles)
        try:
            result = data["data"]["tweetResult"]["result"]
            blocks = (result.get("article", {})
                      .get("article_results", {})
                      .get("result", {})
                      .get("content_state", {})
                      .get("blocks", []))
        except (KeyError, TypeError):
            return ""
        return "\n".join(b.get("text", "") for b in blocks if b.get("text")).strip()

    def get_tweet_conversation(self, tweet_id):
        """Fetch the threaded conversation for a tweet via TweetDetail.

        Returns the list of tweets in the thread (oldest first), each normalized,
        so callers can reconstruct the reply chain leading up to `tweet_id`."""
        variables = {
            "focalTweetId": str(tweet_id),
            "cursor": None,
            "referrer": "tweet",
            "with_rux_injections": False,
            "includePromotedContent": False,
            "withCommunity": True,
            "withQuickPromoteEligibility": False,
            "withBirdwatchNotes": False,
            "withDownvotePerspective": False,
            "withReactionsMetadata": False,
            "withReactionsPerspective": False,
            "withVoice": False,
            "withV2Timeline": True,
        }
        data = self._graphql_get(QUERY_IDS["TweetDetail"], "TweetDetail", variables, features=FEATURES)
        tweets = []
        try:
            root = data["data"].get("threaded_conversation_with_injections_v2") or data["data"]["threaded_conversation_with_injections"]
            instructions = root["instructions"]
        except (KeyError, TypeError):
            return tweets

        for instr in instructions:
            if instr.get("type") != "TimelineAddEntries":
                continue
            for entry in instr.get("entries", []):
                content = entry.get("content", {})
                if content.get("entryType") != "TimelineTimelineItem":
                    continue
                item = content.get("itemContent", {})
                if item.get("itemType") != "TimelineTweet":
                    continue
                result = item.get("tweet_results", {}).get("result", {})
                if result and result.get("__typename") == "Tweet":
                    tweets.append(self._normalize_tweet(result))
        return tweets

    def get_for_you_timeline(self, count=20, cursor=None):
        variables = {
            "count": min(count, 100),
            "includePromotedContent": False,
            "requestContext": "launch",
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_post(QUERY_IDS["HomeTimeline"], "HomeTimeline", variables, features=FEATURES)

        tweets = []
        next_cursor = None
        try:
            instructions = data["data"]["home"]["home_timeline_urt"]["instructions"]
        except KeyError:
            return tweets, None

        for instr in instructions:
            if instr.get("type") == "TimelineAddEntries":
                for entry in instr.get("entries", []):
                    content = entry.get("content", {})
                    if content.get("entryType") != "TimelineTimelineItem":
                        continue
                    item = content.get("itemContent", {})
                    if item.get("itemType") != "TimelineTweet":
                        continue
                    result = item.get("tweet_results", {}).get("result", {})
                    if result and result.get("__typename") == "Tweet":
                        tweets.append(self._normalize_tweet(result))
                    elif content.get("entryType") == "TimelineTimelineCursor":
                        if content.get("cursorType") == "Bottom":
                            next_cursor = content.get("value")

        return tweets, next_cursor

    def like(self, tweet_id):
        variables = {"tweet_id": str(tweet_id)}
        data = self._graphql_post(QUERY_IDS["FavoriteTweet"], "FavoriteTweet", variables, features=FEATURES)
        return data

    TRENDING_CATEGORY = "Technology"
    TRENDING_PLACE_ID = "6463355099376608651"
    TRENDING_TOPIC_ID = "ZzE5MjU5NDk3MjI2ODgxMjY5NzY"
    TRENDING_API = "https://x.com/i/jfapi/global-trending/tagBrowserFeed"

    def _get_trending_raw(self):
        headers = {
            "Referer": "https://x.com/i/jf/global-trending/home",
            "x-jf-client-theme": "dark",
            "x-jf-v": "JP-5",
            "timezone": "Africa/Lagos",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
        }
        params = {
            "place_id": self.TRENDING_PLACE_ID,
            "topic_id": self.TRENDING_TOPIC_ID,
        }
        resp = self.session.get(self.TRENDING_API, params=params, headers=headers)
        if resp.status_code == 429:
            log.warning("Rate limited. Waiting 60s...")
            time.sleep(60)
            return self._get_trending_raw()
        resp.raise_for_status()
        return resp.content

    def get_trending(self, count=50):
        raw = self._get_trending_raw()

        topics = []
        i = 0
        n = len(raw)
        while i < n - 1:
            length = raw[i]
            if 2 <= length <= 40 and i + 1 + length <= n:
                chunk = raw[i + 1:i + 1 + length]
                if all(32 <= b < 127 for b in chunk):
                    s = chunk.decode()
                    if re.fullmatch(r"[A-Za-z0-9 .&#+'/-]{3,}", s) and s not in (
                        "page", "section", "main", "tag",
                    ):
                        topics.append(s)
                    i += 1 + length
                    continue
            i += 1

        seen = set()
        unique = []
        for t in topics:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:count]

    TRENDING_TIMELINE_API = "https://x.com/i/jfapi/global-trending/timelineGeneric"

    def _get_trending_timeline_id(self):
        headers = {
            "Referer": "https://x.com/i/jf/global-trending/home",
            "x-jf-client-theme": "dark",
            "x-jf-v": "JP-5",
            "timezone": "Africa/Lagos",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
        }
        params = {
            "time": "Day",
            "place_id": self.TRENDING_PLACE_ID,
            "topic_id": self.TRENDING_TOPIC_ID,
            "tag": "",
        }
        resp = self.session.get(self.TRENDING_TIMELINE_API, params=params, headers=headers)
        if resp.status_code == 429:
            log.warning("Rate limited. Waiting 60s...")
            time.sleep(60)
            return self._get_trending_timeline_id()
        resp.raise_for_status()
        match = re.search(rb"[A-Za-z0-9+/=]{60,}", resp.content)
        return match.group().decode() if match else None

    def get_trending_tweets(self, count=20):
        timeline_id = self._get_trending_timeline_id()
        if not timeline_id:
            return []
        variables = {
            "timelineId": timeline_id,
            "count": count,
            "withQuickPromoteEligibilityTweetFields": True,
        }
        data = self._graphql_get(QUERY_IDS["GenericTimelineById"], "GenericTimelineById", variables)
        tweets = []
        try:
            instructions = data["data"]["timeline"]["timeline"]["instructions"]
        except (KeyError, TypeError):
            return tweets
        for instr in instructions:
            if instr.get("type") != "TimelineAddEntries":
                continue
            for entry in instr.get("entries", []):
                content = entry.get("content", {})
                if content.get("entryType") != "TimelineTimelineItem":
                    continue
                item = content.get("itemContent", {})
                result = item.get("tweet_results", {}).get("result", {})
                if not result or result.get("__typename") != "Tweet":
                    continue
                tweets.append(self._normalize_tweet(result))
        return tweets


# --- X-Client-Transaction-Id generator ---------------------------------------
# Twitter web attaches a derived header (X-Client-Transaction-Id) to GraphQL
# requests. The algorithm is reverse-engineered from the web client's ondemand.s
# bundle: it mines an XOR-key + animation key from the homepage and hashes the
# request method/path/timestamp with them. Implemented as an internal helper of
# XClient; kept private because it mirrors web-client internals that change.

ONDEMAND_URL = "https://abs.twimg.com/responsive-web/client-web/ondemand.s.{hash}a.js"
KEYWORD = "obfiowerehiring"
EPOCH_MS = 1682924400000

_INDICES_RE = re.compile(r"(\(\w{1}\[(\d{1,2})\],\s*16\))+")


class _Cubic:
    def __init__(self, curves):
        self.curves = curves

    def get_value(self, time):
        start_gradient = 0.0
        end_gradient = 0.0
        start = 0.0
        mid = 0.0
        end = 1.0

        if time <= 0.0:
            if self.curves[0] > 0.0:
                start_gradient = self.curves[1] / self.curves[0]
            elif self.curves[1] == 0.0 and self.curves[2] > 0.0:
                start_gradient = self.curves[3] / self.curves[2]
            return start_gradient * time

        if time >= 1.0:
            if self.curves[2] < 1.0:
                end_gradient = (self.curves[3] - 1.0) / (self.curves[2] - 1.0)
            elif self.curves[2] == 1.0 and self.curves[0] < 1.0:
                end_gradient = (self.curves[1] - 1.0) / (self.curves[0] - 1.0)
            return 1.0 + end_gradient * (time - 1.0)

        while start < end:
            mid = (start + end) / 2
            x_est = self._calculate(self.curves[0], self.curves[2], mid)
            if abs(time - x_est) < 0.00001:
                return self._calculate(self.curves[1], self.curves[3], mid)
            if x_est < time:
                start = mid
            else:
                end = mid
        return self._calculate(self.curves[1], self.curves[3], mid)

    @staticmethod
    def _calculate(a, b, m):
        return 3.0 * a * (1 - m) * (1 - m) * m + 3.0 * b * (1 - m) * m * m + m * m * m


class _MathUtils:
    @staticmethod
    def is_odd(num):
        return -1.0 if num % 2 else 0.0

    @staticmethod
    def interpolate_num(from_val, to_val, f):
        if isinstance(from_val, bool) and isinstance(to_val, bool):
            return from_val if f < 0.5 else to_val
        return from_val * (1 - f) + to_val * f

    @staticmethod
    def interpolate(from_list, to_list, f):
        return [_MathUtils.interpolate_num(from_list[i], to_list[i], f)
                for i in range(min(len(from_list), len(to_list)))]

    @staticmethod
    def convert_rotation_to_matrix(rotation):
        import math
        rad = math.radians(rotation)
        return [math.cos(rad), -math.sin(rad), math.sin(rad), math.cos(rad)]

    @staticmethod
    def float_to_hex(x):
        result = []
        quotient = int(x)
        fraction = x - quotient
        while quotient > 0:
            quotient = int(x / 16)
            remainder = int(x - (float(quotient) * 16))
            if remainder > 9:
                result.insert(0, chr(remainder + 55))
            else:
                result.insert(0, str(remainder))
            x = float(quotient)
        if fraction == 0:
            return "".join(result)
        result.append(".")
        while fraction > 0:
            fraction *= 16
            integer = int(fraction)
            fraction -= float(integer)
            if integer > 9:
                result.append(chr(integer + 55))
            else:
                result.append(str(integer))
        return "".join(result)

    @staticmethod
    def round(num):
        import math
        x = math.floor(num)
        if (num - x) >= 0.5:
            x = math.ceil(num)
        return math.copysign(x, num)


class XTransactionIdGenerator:
    def __init__(self, session=None, ondemand_content=None, home_page=None, fetch_session=None):
        self.session = session or requests.Session()
        self.fetch_session = fetch_session or self.session
        self._cache_home = home_page
        self._cache_ondemand = ondemand_content
        self.row_index = None
        self.key_bytes_indices = None
        self.key_bytes = None
        self.animation_key = None

    def _get_home_page(self):
        if self._cache_home is not None:
            return self._cache_home
        r = self.fetch_session.get("https://x.com/home")
        r.raise_for_status()
        self._cache_home = r.text
        return self._cache_home

    def _get_ondemand(self, home_html):
        if self._cache_ondemand is not None:
            return self._cache_ondemand
        m = re.search(r'(\d+):"ondemand\.s"', home_html)
        if m:
            chunk_id = m.group(1)
            m2 = re.search(re.escape(chunk_id) + r':"([a-f0-9]+)"', home_html)
            if not m2:
                raise RuntimeError("Couldn't find ondemand.s hash in home page")
            hsh = m2.group(1)
        else:
            m = re.search(r"ondemand\.s\.([a-f0-9]+a)\.js", home_html)
            if not m:
                raise RuntimeError("Couldn't find ondemand.s.js reference in home page")
            hsh = m.group(1)[:-1]
        r = self.fetch_session.get(ONDEMAND_URL.format(hash=hsh))
        r.raise_for_status()
        self._cache_ondemand = r.text
        return self._cache_ondemand

    def _init(self):
        if self.key_bytes is not None:
            return
        home_html = self._get_home_page()
        ondemand = self._get_ondemand(home_html)

        indices = [int(m.group(2)) for m in _INDICES_RE.finditer(ondemand)]
        if not indices:
            raise RuntimeError("Couldn't get KEY_BYTE indices")
        self.row_index, self.key_bytes_indices = indices[0], indices[1:]

        soup = BeautifulSoup(home_html, "html.parser")
        element = soup.select_one("meta[name='twitter-site-verification']")
        if not element:
            raise RuntimeError("Couldn't get twitter-site-verification key")
        key = element.get("content")
        self.key_bytes = list(base64.b64decode(key.encode()))
        self.animation_key = self._get_animation_key(soup)

    def generate(self, method, path):
        self._init()
        time_now = int((time.time() * 1000 - EPOCH_MS) / 1000)
        time_now_bytes = [(time_now >> (i * 8)) & 0xFF for i in range(4)]
        hash_val = hashlib.sha256(
            f"{method}!{path}!{time_now}{KEYWORD}{self.animation_key}".encode()
        ).digest()
        random_num = random.randint(0, 255)
        bytes_arr = [*self.key_bytes, *time_now_bytes, *list(hash_val)[:16], 3]
        out = bytearray([random_num, *[b ^ random_num for b in bytes_arr]])
        return base64.b64encode(out).decode().rstrip("=")

    def _get_animation_key(self, soup):
        row_index = self.key_bytes[self.row_index] % 16
        frame_time = reduce(
            lambda x, y: x * y,
            [self.key_bytes[i] % 16 for i in self.key_bytes_indices],
        )
        frame_time = _MathUtils.round(frame_time / 10) * 10

        frames = soup.select("[id^='loading-x-anim']")
        if not frames:
            raise RuntimeError("Couldn't find loading-x-anim frames")
        path_data = list(list(frames[self.key_bytes[5] % 4].children)[0].children)[1].get("d")[9:]
        arr = [
            [int(x) for x in re.sub(r"[^\d]+", " ", item).strip().split()]
            for item in path_data.split("C")
        ]
        frame_row = arr[row_index]
        target_time = float(frame_time) / 4096
        return self._animate(frame_row, target_time)

    def _animate(self, frames, target_time):
        import math

        def solve(value, min_val, max_val, rounding):
            result = value * (max_val - min_val) / 255 + min_val
            return math.floor(result) if rounding else round(result, 2)

        from_color = [float(x) for x in [*frames[:3], 1]]
        to_color = [float(x) for x in [*frames[3:6], 1]]
        to_rotation = [solve(float(frames[6]), 60.0, 360.0, True)]

        curves = [
            solve(float(item), _MathUtils.is_odd(i), 1.0, False)
            for i, item in enumerate(frames[7:])
        ]

        val = _Cubic(curves).get_value(target_time)
        color = [max(0, min(255, v)) for v in _MathUtils.interpolate(from_color, to_color, val)]
        rotation = _MathUtils.interpolate([0.0], to_rotation, val)
        matrix = _MathUtils.convert_rotation_to_matrix(rotation[0])

        str_arr = [format(round(value), "x") for value in color[:-1]]
        for value in matrix:
            rounded = abs(round(value, 2))
            hex_value = _MathUtils.float_to_hex(rounded)
            str_arr.append(f"0{hex_value}".lower() if hex_value.startswith(".") else hex_value or "0")
        str_arr.extend(["0", "0"])

        return re.sub(r"[.-]", "", "".join(str_arr))


