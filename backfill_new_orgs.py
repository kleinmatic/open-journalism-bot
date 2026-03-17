#!/usr/bin/env python3
"""
Backfill new orgs: discover and insert repos for newly-added organizations.
This fetches repos from GitHub and stores them in the database WITHOUT posting to BlueSky.

Usage:
    uv run backfill_new_orgs.py localangle PublicLedger Verso-Lab
    uv run backfill_new_orgs.py --help
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

from open_journalism_bot import (
    fetch_latest_repos,
    fetch_repo_metadata,
    fetch_readme,
    generate_claude_summary,
    init_db,
    insert_repo,
    load_config,
    repo_exists,
    upsert_orgs,
    WHALE_ORGS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill repos for new organizations without BlueSky posting"
    )
    parser.add_argument(
        "orgs",
        nargs="+",
        help="GitHub org handles to backfill (e.g., localangle PublicLedger Verso-Lab)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/oj-bot.db",
        help="Database path"
    )
    parser.add_argument(
        "--skip-summaries",
        action="store_true",
        help="Skip Claude summary generation"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be inserted without writing"
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    # Connect to database
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(args.db)

    new_count = 0
    empty_count = 0

    for handle in args.orgs:
        username = handle.lower().rstrip('/')
        if username.startswith('http'):
            username = username.split('/')[-1]

        logging.info(f"\n📦 Backfilling repos for {username}...")

        # Add org to database if not present
        github_url = f'https://github.com/{username}'
        org_data = [{
            'org_name': username,
            'github_url': github_url
        }]
        upsert_orgs(conn, org_data)

        # Fetch repos
        per_page = 100 if username.lower() in WHALE_ORGS else 10
        try:
            repos = fetch_latest_repos(
                github_url,
                token=config['github_token'],
                per_page=per_page,
            )
            logging.info(f"Found {len(repos)} repos")
        except Exception as e:
            logging.error(f"Failed to fetch repos for {username}: {e}")
            continue

        # Insert repos
        for repo in repos:
            if repo_exists(conn, repo['full_name']):
                logging.debug(f"{repo['full_name']}: already in database")
                continue

            logging.info(f"  → {repo['full_name']}")

            # Fetch metadata
            metadata = fetch_repo_metadata(repo['full_name'], token=config['github_token'])

            if args.dry_run:
                logging.info(f"  [DRY RUN] Would insert {repo['full_name']}")
            else:
                # Insert the repo
                insert_repo(conn, repo, org_username=username, is_empty=False, metadata=metadata)

                # Mark as backfilled so it won't be auto-posted until manually approved
                conn.execute(
                    "UPDATE repos SET backfill_source = ? WHERE full_name = ?",
                    ("new-org-backfill", repo['full_name']),
                )
                conn.commit()

                # Optionally generate summary
                if not args.skip_summaries and config.get('anthropic_api_key'):
                    readme_content = fetch_readme(repo['full_name'], token=config['github_token'])
                    if readme_content:
                        try:
                            claude_summary = generate_claude_summary(
                                readme_content,
                                config['anthropic_api_key']
                            )
                            if claude_summary:
                                conn.execute(
                                    "UPDATE repos SET claude_summary = ? WHERE full_name = ?",
                                    (claude_summary, repo['full_name']),
                                )
                                conn.commit()
                                logging.info(f"  → generated summary ({len(claude_summary)} chars)")
                        except Exception as e:
                            logging.warning(f"  → summary generation failed: {e}")
                    time.sleep(0.5)  # Be nice to APIs

            new_count += 1
            time.sleep(0.5)  # Rate limiting

    logging.info(f"\n✅ Backfill complete. Discovered {new_count} repos for {len(args.orgs)} orgs.")
    conn.close()


if __name__ == "__main__":
    main()
