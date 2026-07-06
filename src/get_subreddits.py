"""
Pull Reddit posts from Arctic Shift API across defined time periods.

Output columns:
subreddit, title, selftext, score, created_utc, num_comments

Install:
    pip install requests pandas

Run:
    python arctic_shift_pull_posts.py
"""

import time
import random
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


# -----------------------------
# CONFIG
# -----------------------------

SUBREDDITS = [
    # Add your subreddit names here, without
    "vinyl",
    "AnalogCommunity",
    "cassetteculture",
    "ipod",
    "BuyItForLife",
    "GenZ",
]

PERIODS = {
    "2018_2019_pre_pandemic": ("2018-01-01", "2020-01-01"),
    "2022_2023_post_pandemic_resurgence": ("2022-01-01", "2024-01-01"),
    "2024_2025_current_peak": ("2024-01-01", "2026-01-01"),
}

POSTS_PER_SUBREDDIT_PER_PERIOD = 1000   # Change to 500 if you want fewer
PAGE_LIMIT = 100                        # Safer page size for API pagination
OUTPUT_CSV = "reddit_arctic_shift_posts.csv"

BASE_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"

REQUEST_TIMEOUT = 30
BASE_SLEEP_SECONDS = 1.0
MAX_RETRIES = 5


# -----------------------------
# HELPERS
# -----------------------------

def to_epoch(date_str: str) -> int:
    """
    Convert YYYY-MM-DD to UTC epoch seconds.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def extract_items(response_json: Any) -> List[Dict[str, Any]]:
    """
    Arctic Shift responses may be a list directly or wrapped in a dict.
    This function handles the common shapes defensively.
    """
    if isinstance(response_json, list):
        return response_json

    if isinstance(response_json, dict):
        for key in ["data", "results", "posts"]:
            if key in response_json and isinstance(response_json[key], list):
                return response_json[key]

    return []


def get_with_retries(params: Dict[str, Any]) -> Optional[Any]:
    """
    GET request with retry/backoff and basic rate-limit handling.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                BASE_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "academic-reddit-research-script/1.0"
                },
            )

            if response.status_code == 429:
                reset = response.headers.get("X-RateLimit-Reset")
                sleep_for = 30

                if reset:
                    try:
                        sleep_for = max(5, int(reset))
                    except ValueError:
                        pass

                print(f"Rate limited. Sleeping {sleep_for}s...")
                time.sleep(sleep_for)
                continue

            if 500 <= response.status_code < 600:
                sleep_for = min(60, 2 ** attempt)
                print(
                    f"Server error {response.status_code}. "
                    f"Retry {attempt}/{MAX_RETRIES} after {sleep_for}s..."
                )
                time.sleep(sleep_for)
                continue

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            sleep_for = min(60, 2 ** attempt)
            print(
                f"Request error: {e}. "
                f"Retry {attempt}/{MAX_RETRIES} after {sleep_for}s..."
            )
            time.sleep(sleep_for)

    print("Failed after max retries:", params)
    return None


def normalize_post(post: Dict[str, Any], fallback_subreddit: str) -> Dict[str, Any]:
    """
    Keep only required columns.
    """
    return {
        "subreddit": post.get("subreddit") or fallback_subreddit,
        "title": post.get("title") or "",
        "selftext": post.get("selftext") or "",
        "score": post.get("score"),
        "created_utc": post.get("created_utc"),
        "num_comments": post.get("num_comments"),
    }


def fetch_posts_for_period(
    subreddit: str,
    period_name: str,
    start_date: str,
    end_date: str,
    target_count: int,
) -> List[Dict[str, Any]]:
    """
    Fetch posts for one subreddit and one period using ascending created_utc pagination.
    """
    after = to_epoch(start_date)
    before = to_epoch(end_date)

    collected: List[Dict[str, Any]] = []
    seen_ids = set()

    print(
        f"\nFetching r/{subreddit} | {period_name} | "
        f"{start_date} to {end_date} | target={target_count}"
    )

    while len(collected) < target_count:
        remaining = target_count - len(collected)
        limit = min(PAGE_LIMIT, remaining)

        params = {
            "subreddit": subreddit,
            "after": after,
            "before": before,
            "limit": limit,
            "sort": "asc",
        }

        response_json = get_with_retries(params)

        if response_json is None:
            break

        items = extract_items(response_json)

        if not items:
            print("No more posts returned.")
            break

        new_items = 0
        max_created_utc = after

        for post in items:
            post_id = post.get("id") or post.get("name") or (
                post.get("title", "") + str(post.get("created_utc", ""))
            )

            if post_id in seen_ids:
                continue

            seen_ids.add(post_id)

            created_utc = post.get("created_utc")
            if created_utc is None:
                continue

            try:
                created_utc_int = int(float(created_utc))
            except (TypeError, ValueError):
                continue

            if created_utc_int >= before:
                continue

            collected.append(normalize_post(post, subreddit))
            new_items += 1
            max_created_utc = max(max_created_utc, created_utc_int)

            if len(collected) >= target_count:
                break

        print(
            f"Collected {len(collected)}/{target_count} "
            f"for r/{subreddit} | {period_name}"
        )

        if new_items == 0:
            print("No new unique posts found. Stopping this batch.")
            break

        # Move cursor forward by one second to avoid repeated last record.
        after = max_created_utc + 1

        # Respectful delay with small jitter.
        time.sleep(BASE_SLEEP_SECONDS + random.uniform(0, 0.5))

    return collected


def main():
    if not SUBREDDITS:
        raise ValueError(
            "Please add subreddit names to SUBREDDITS before running the script."
        )

    all_rows: List[Dict[str, Any]] = []

    for subreddit in SUBREDDITS:
        subreddit = subreddit.strip().replace("r/", "").strip("/")

        for period_name, (start_date, end_date) in PERIODS.items():
            rows = fetch_posts_for_period(
                subreddit=subreddit,
                period_name=period_name,
                start_date=start_date,
                end_date=end_date,
                target_count=POSTS_PER_SUBREDDIT_PER_PERIOD,
            )

            # Optional: add period column internally if useful later.
            # Not saved unless you add it to final_columns.
            for row in rows:
                row["_period"] = period_name

            all_rows.extend(rows)

    df = pd.DataFrame(all_rows)

    final_columns = [
        "subreddit",
        "title",
        "selftext",
        "score",
        "created_utc",
        "num_comments",
    ]

    df = df.reindex(columns=final_columns)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\nDone. Saved {len(df)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()