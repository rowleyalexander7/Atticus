#!/usr/bin/env python3
"""
Fetches recent @AtticusScot tweets via Twitter API v2, merges with the
stored dataset, and regenerates index.html.

Required env var: TWITTER_BEARER_TOKEN
"""
import os
import re
import json
import time
import datetime
import requests
from pathlib import Path

BEARER_TOKEN = os.environ.get("TWITTER_BEARER_TOKEN")
USERNAME = "AtticusScot"
ROOT = Path(__file__).parent.parent
TWEETS_JSON = ROOT / "data" / "tweets.json"
INDEX_HTML = ROOT / "index.html"

CATEGORIES_ORDER = ["mindset", "philosophy", "society", "tech", "politics", "love", "college", "humor"]

# Keywords drive category assignment; mindset is the catch-all.
CATEGORY_KEYWORDS = {
    "college": [
        "college", "university", "class", "professor", "campus", "semester",
        "major", "gpa", "finals", "dorm", "roommate", "textbook", "exam",
        "lecture", "tuition", "frat", "sorority",
    ],
    "love": [
        "love", "heart", "relationship", "miss ", "hurt", "alone", "lonely",
        "together", "apart", "loss", "crush", "ex ", "partner", "feelings",
        "girl", "boy", "date", "dating",
    ],
    "humor": [
        "funny", "lol", "joke", "laugh", "hilarious", "irony", "weird",
        "ridiculous", "bizarre", "piñata", "cutscene", "prank",
    ],
    "tech": [
        "ai ", "chatgpt", "artificial intelligence", "tech", "instagram",
        "tiktok", "social media", "algorithm", "app ", "code", "data",
        "internet", "software", "elon", "google", "openai", "reels", "twitter",
        "x.com", "gpt", "llm", "cluely",
    ],
    "politics": [
        "politic", "government", "power", "leader", "vote", "election",
        "president", "democrat", "republican", "freedom", "liberty",
        "founding fathers", "congress", "economy", "greenland", "propaganda",
        "tariff", "senate", "policy",
    ],
    "society": [
        "society", "people", "human", "world", "culture", "norm ", "behavior",
        "civiliz", "masses", "media", "audience", "crowd", "public",
    ],
    "philosophy": [
        "wonder", "reality", "death", "die ", "exist", "meaning", "truth",
        "universe", "consciousness", "dream", "believe", "night", "life is",
        "terrified", "mortality", "soul",
    ],
}


def categorize(text: str) -> str:
    t = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return cat
    return "mindset"


def _headers() -> dict:
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}


def get_user_id() -> str:
    r = requests.get(
        f"https://api.twitter.com/2/users/by/username/{USERNAME}",
        headers=_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["data"]["id"]


def fetch_tweets_since(user_id: str, since_iso: str) -> list[dict]:
    url = f"https://api.twitter.com/2/users/{user_id}/tweets"
    params: dict = {
        "max_results": 100,
        "tweet.fields": "public_metrics,created_at,id",
        "exclude": "retweets,replies",
        "start_time": since_iso,
    }
    tweets: list[dict] = []
    while True:
        r = requests.get(url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        for t in data.get("data", []):
            tweets.append({
                "id": t["id"],
                "text": t["text"],
                "likes": t["public_metrics"]["like_count"],
                "created_at": t["created_at"],
            })
        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break
        params["pagination_token"] = next_token
        time.sleep(1)
    return tweets


def load_store() -> dict:
    if TWEETS_JSON.exists():
        return json.loads(TWEETS_JSON.read_text(encoding="utf-8"))
    return {"tweets": [], "last_updated": None}


def save_store(store: dict) -> None:
    TWEETS_JSON.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def merge(store: dict, api_tweets: list[dict]) -> int:
    """Add new API tweets and refresh like counts on existing ones. Returns added count."""
    existing_by_id = {t["id"]: t for t in store["tweets"] if t.get("id")}

    # Build a text-fingerprint index for legacy entries that have no id
    def fingerprint(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().strip())[:120]

    existing_by_fp = {fingerprint(t["text"]): t for t in store["tweets"] if not t.get("id")}

    added = 0
    for api in api_tweets:
        if api["id"] in existing_by_id:
            existing_by_id[api["id"]]["likes"] = api["likes"]
        elif fingerprint(api["text"]) in existing_by_fp:
            entry = existing_by_fp[fingerprint(api["text"])]
            entry["id"] = api["id"]
            entry["likes"] = api["likes"]
            entry.setdefault("created_at", api["created_at"])
        else:
            store["tweets"].append({
                "id": api["id"],
                "text": api["text"],
                "likes": api["likes"],
                "created_at": api["created_at"],
                "category": categorize(api["text"]),
            })
            added += 1

    return added


def rebuild_html(store: dict) -> None:
    by_cat: dict[str, list] = {c: [] for c in CATEGORIES_ORDER}
    for t in store["tweets"]:
        cat = t.get("category") or categorize(t["text"])
        if cat not in by_cat:
            cat = "mindset"
        by_cat[cat].append(t)

    lines = ["const DATA = {"]
    for cat in CATEGORIES_ORDER:
        tweets = sorted(by_cat[cat], key=lambda t: t["likes"], reverse=True)
        lines.append(f"  {cat}: [")
        for t in tweets:
            escaped = (
                t["text"]
                .replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", " ")
                .replace("\r", "")
            )
            lines.append(f'    {{ text: "{escaped}", likes: {t["likes"]} }},')
        lines.append("  ],")
    lines.append("};")
    new_data_block = "\n".join(lines)

    html = INDEX_HTML.read_text(encoding="utf-8")
    html = re.sub(r"const DATA = \{.*?\};", new_data_block, html, flags=re.DOTALL)

    total = sum(len(v) for v in by_cat.values())
    max_likes = max(
        (t["likes"] for tweets in by_cat.values() for t in tweets),
        default=0,
    )
    top_str = (
        f"{max_likes / 1000:.1f}K".replace(".0K", "K") if max_likes >= 1000 else str(max_likes)
    )

    html = re.sub(
        r'(<div class="stat-num">)\d+(<\/div>\s*<div class="stat-label">Total Posts)',
        rf"\g<1>{total}\g<2>",
        html,
    )
    html = re.sub(
        r'(<div class="stat-num">)[^<]+(</div>\s*<div class="stat-label">Top Likes)',
        rf"\g<1>{top_str}\g<2>",
        html,
    )
    html = re.sub(
        r"(Compiled \d+ · )\d+( posts · \d+ chapters)",
        rf"\g<1>{total}\g<2>",
        html,
    )

    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"Rebuilt index.html — {total} tweets, top likes: {top_str}")


def main() -> None:
    if not BEARER_TOKEN:
        raise EnvironmentError("TWITTER_BEARER_TOKEN environment variable is not set.")

    store = load_store()

    if store["last_updated"]:
        since = store["last_updated"]
    else:
        # First API run: look back 30 days to catch recent posts
        since = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetching @{USERNAME} tweets since {since} …")
    user_id = get_user_id()
    api_tweets = fetch_tweets_since(user_id, since)
    print(f"  API returned {len(api_tweets)} tweets")

    added = merge(store, api_tweets)
    store["last_updated"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    save_store(store)
    print(f"  Added {added} new tweets. Dataset total: {len(store['tweets'])}")

    rebuild_html(store)


if __name__ == "__main__":
    main()
