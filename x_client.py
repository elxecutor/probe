import os
import re
import time
import json
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

from x_txid import XTransactionIdGenerator

load_dotenv()

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
            print(f"  [warn] txid generation failed ({e}); continuing without")
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
            print("  Rate limited. Waiting 60s...")
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
            print("  Rate limited. Waiting 60s...")
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
            except KeyError as e:
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

    def get_followers(self, user_id=None, count=100, cursor=None):
        variables = {
            "userId": user_id or self.user_id,
            "count": min(count, 100),
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_get(QUERY_IDS["Followers"], "Followers", variables, features=FEATURES)
        users, next_cursor = self._extract_users_from_timeline(data)
        return users, next_cursor

    def get_all_followers(self, user_id=None, on_progress=None):
        all_users = []
        cursor = None
        page = 0

        while True:
            page += 1
            users, cursor = self.get_followers(user_id=user_id, cursor=cursor)
            all_users.extend(users)

            if on_progress:
                on_progress(page, len(users), len(all_users))

            if not users or not cursor:
                break
            time.sleep(1.1)

        return all_users

    def follow(self, target_user_id):
        params = {
            "include_profile_interstitial_type": "1",
            "include_blocking": "1",
            "include_blocked_by": "1",
            "include_followed_by": "1",
            "include_want_retweets": "1",
            "include_mute_edge": "1",
            "include_can_dm": "1",
            "include_can_media_tag": "1",
            "include_ext_is_blue_verified": "1",
            "include_ext_verified_type": "1",
            "include_ext_profile_image_shape": "1",
            "skip_status": "1",
            "user_id": str(target_user_id),
        }
        resp = self.session.post(f"{REST_BASE}/friendships/create.json", data=params)
        if resp.status_code == 429:
            print("  Rate limited. Waiting 60s...")
            time.sleep(60)
            return self.follow(target_user_id)
        resp.raise_for_status()
        return resp.json()

    def unfollow(self, target_user_id):
        params = {
            "include_profile_interstitial_type": "1",
            "include_blocking": "1",
            "include_blocked_by": "1",
            "include_followed_by": "1",
            "include_want_retweets": "1",
            "include_mute_edge": "1",
            "include_can_dm": "1",
            "include_can_media_tag": "1",
            "include_ext_is_blue_verified": "1",
            "include_ext_verified_type": "1",
            "include_ext_profile_image_shape": "1",
            "skip_status": "1",
            "user_id": str(target_user_id),
        }
        resp = self.session.post(f"{REST_BASE}/friendships/destroy.json", data=params)
        if resp.status_code == 429:
            print("  Rate limited. Waiting 60s...")
            time.sleep(60)
            return self.unfollow(target_user_id)
        resp.raise_for_status()
        return resp.json()

    def get_user_by_screen_name(self, screen_name):
        variables = {"screen_name": screen_name}
        data = self._graphql_get(QUERY_IDS["UserByScreenName"], "UserByScreenName", variables, features=FEATURES)
        try:
            result = data["data"]["user"]["result"]
            return self._normalize_user(result) if result else None
        except KeyError:
            return None

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
                    if item.get("__typename") != "TimelineNotification":
                        continue
                    notif = {
                        "id": item.get("id", ""),
                        "icon": item.get("notification_icon", ""),
                        "url": item.get("notification_url", {}).get("url", ""),
                        "message": item.get("rich_message", {}).get("text", ""),
                        "template_type": item.get("template", {}).get("__typename", ""),
                        "timestamp": entry.get("sortIndex", ""),
                    }
                    # Extract tweet ID from URL
                    if "/status/" in notif["url"]:
                        notif["tweet_id"] = notif["url"].split("/status/")[-1].split("?")[0]
                    else:
                        notif["tweet_id"] = None
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

    def get_all_likes(self, user_id=None, on_progress=None):
        all_tweets = []
        cursor = None
        page = 0

        while True:
            page += 1
            tweets, cursor = self.get_likes(user_id=user_id, cursor=cursor)
            all_tweets.extend(tweets)

            if on_progress:
                on_progress(page, len(tweets), len(all_tweets))

            if not tweets or not cursor:
                break
            time.sleep(1.1)

        return all_tweets

    def get_bookmarks(self, count=40, cursor=None):
        variables = {"count": min(count, 40), "includePromotedContent": True}
        if cursor:
            variables["cursor"] = cursor
        data = self._graphql_get(QUERY_IDS["Bookmarks"], "Bookmarks", variables, features=FEATURES)
        path = ["data", "bookmark_timeline_v2", "timeline"]
        return self._extract_tweets_from_timeline(data, path)

    def create_bookmark(self, tweet_id):
        variables = {"tweet_id": str(tweet_id)}
        data = self._graphql_post(QUERY_IDS["CreateBookmark"], "CreateBookmark", variables, features=FEATURES)
        return data

    def delete_bookmark(self, tweet_id):
        variables = {"tweet_id": str(tweet_id)}
        qid = QUERY_IDS["DeleteBookmark"]
        payload = {"variables": variables, "queryId": qid, "features": FEATURES}
        resp = self.session.post(f"{GRAPHQL_BASE}/{qid}/DeleteBookmark", json=payload)
        if resp.status_code == 429:
            print("  Rate limited. Waiting 60s...")
            time.sleep(60)
            return self.delete_bookmark(tweet_id)
        resp.raise_for_status()
        return resp.json()

    def get_user_tweets(self, user_id=None, count=20, cursor=None):
        uid = user_id or self.user_id
        variables = {
            "userId": uid,
            "count": min(count, 100),
            "includePromotedContent": False,
            "withVoice": False,
        }
        if cursor:
            variables["cursor"] = cursor

        data = self._graphql_get(QUERY_IDS["UserTweets"], "UserTweets", variables, features=FEATURES)
        return self._extract_tweets_from_timeline(data, ["data", "user", "result", "timeline", "timeline"])

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
        next_cursor = None
        try:
            root = data["data"].get("threaded_conversation_with_injections_v2") or data["data"]["threaded_conversation_with_injections"]
            instructions = root["instructions"]
        except KeyError:
            return tweets, None

        for instr in instructions:
            if instr.get("type") == "TimelineAddEntries":
                for entry in instr.get("entries", []):
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

    def unlike(self, tweet_id):
        variables = {"tweet_id": str(tweet_id)}
        data = self._graphql_post(QUERY_IDS["UnfavoriteTweet"], "UnfavoriteTweet", variables, features=FEATURES)
        return data

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
            print("  Rate limited. Waiting 60s...")
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
            print("  Rate limited. Waiting 60s...")
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

    def get_trending_context(self, topic_count=50, tweet_count=20):
        return {
            "category": self.TRENDING_CATEGORY,
            "topics": self.get_trending(count=topic_count),
            "tweets": self.get_trending_tweets(count=tweet_count),
        }


