#!/usr/bin/env python3
"""
Open Journalism Bot - Monitor GitHub accounts and post new repos to BlueSky.
"""

import argparse
import csv
import io
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import chevron
import requests
from atproto import Client, models
from dotenv import load_dotenv


def load_config():
    """Load configuration from environment variables."""
    load_dotenv()

    config = {
        'csv_url': os.getenv('CSV_URL'),
        'github_token': os.getenv('GITHUB_TOKEN'),
        'bluesky_handle': os.getenv('BLUESKY_HANDLE'),
        'bluesky_password': os.getenv('BLUESKY_APP_PASSWORD'),
        'check_minutes': int(os.getenv('CHECK_MINUTES', '15')),
        'test_mode': os.getenv('TEST_MODE', 'true').lower() == 'true',
    }

    if not config['csv_url']:
        raise ValueError("CSV_URL environment variable is required")

    if not config['test_mode']:
        if not config['bluesky_handle'] or not config['bluesky_password']:
            raise ValueError("BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are required when TEST_MODE is false")

    return config


def fetch_csv(url):
    """Fetch CSV content from URL."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_csv(csv_content):
    """
    Parse CSV and extract organization names and GitHub URLs.
    Skips lines starting with #.
    Returns list of dicts with 'org_name' and 'github_url' keys.
    """
    orgs = []

    lines = csv_content.strip().split('\n')
    clean_lines = [line for line in lines if not line.strip().startswith('#')]
    clean_content = '\n'.join(clean_lines)

    reader = csv.DictReader(io.StringIO(clean_content))

    for row in reader:
        org_name = row.get('Organization', '').strip()
        github_url = row.get('Github', '').strip()

        if org_name and github_url:
            orgs.append({
                'org_name': org_name,
                'github_url': github_url,
            })

    return orgs


def extract_github_username(github_url):
    """Extract username/org name from GitHub URL."""
    url = github_url.rstrip('/')
    return url.split('/')[-1]


def get_github_headers(token=None):
    """Build headers for GitHub API requests."""
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


class RateLimitError(Exception):
    """Raised when GitHub API rate limit is exceeded."""
    def __init__(self, reset_time):
        self.reset_time = reset_time
        super().__init__(f"Rate limit exceeded. Resets at {reset_time}")


def fetch_recent_repos(github_url, token=None, minutes=15):
    """
    Fetch repos created within the last N minutes.
    Returns list of repo dicts with name, description, url, language.
    Raises RateLimitError if rate limited.
    """
    username = extract_github_username(github_url)
    headers = get_github_headers(token)

    # Try user endpoint first, fall back to org endpoint
    urls_to_try = [
        f'https://api.github.com/users/{username}/repos',
        f'https://api.github.com/orgs/{username}/repos',
    ]

    repos_data = None
    for api_url in urls_to_try:
        try:
            response = requests.get(
                api_url,
                headers=headers,
                params={'sort': 'created', 'direction': 'desc', 'per_page': 10},
                timeout=30,
            )
            if response.status_code == 200:
                repos_data = response.json()
                break
            elif response.status_code == 403:
                # Check if rate limited
                remaining = response.headers.get('X-RateLimit-Remaining', '?')
                if remaining == '0':
                    reset_ts = int(response.headers.get('X-RateLimit-Reset', 0))
                    reset_time = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                    raise RateLimitError(reset_time)
                continue
            elif response.status_code == 404:
                continue
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError:
            continue

    if repos_data is None:
        logging.warning(f"Could not fetch repos for {username}")
        return []

    # Filter to repos created within the time window
    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    new_repos = []

    for repo in repos_data:
        created_at = datetime.fromisoformat(repo['created_at'].replace('Z', '+00:00'))

        if created_at >= cutoff_time:
            new_repos.append({
                'repo_name': repo['name'],
                'description': repo.get('description') or '',
                'repo_url': repo['html_url'],
                'language': repo.get('language') or '',
            })

    return new_repos


def load_template():
    """Load the Mustache template for BlueSky posts."""
    template_path = Path(__file__).parent / 'templates' / 'post.mustache'
    with open(template_path, 'r') as f:
        return f.read()


def render_post(template, org_name, repo):
    """Render a BlueSky post using the template."""
    data = {
        'org_name': org_name,
        'repo_name': repo['repo_name'],
        'description': repo['description'],
        'repo_url': repo['repo_url'],
        'language': repo['language'],
    }
    return chevron.render(template, data).strip()


def create_link_card(repo):
    """Create a BlueSky link card embed for a repo."""
    title = repo['repo_name']
    description = repo['description'] or 'A GitHub repository'

    external = models.AppBskyEmbedExternal.External(
        uri=repo['repo_url'],
        title=title,
        description=description,
    )
    return models.AppBskyEmbedExternal.Main(external=external)


def post_to_bluesky(client, text, repo):
    """Post text to BlueSky with a link card."""
    embed = create_link_card(repo)
    client.send_post(text=text, embed=embed)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Monitor GitHub accounts and post new repos to BlueSky'
    )
    parser.add_argument(
        '--limit', '-l',
        type=int,
        default=0,
        help='Limit to first N organizations (0 = no limit, useful for testing)'
    )
    parser.add_argument(
        '--minutes', '-m',
        type=int,
        default=None,
        help='Override CHECK_MINUTES from .env'
    )
    parser.add_argument(
        '--org', '-o',
        type=str,
        default=None,
        help='Test a single GitHub org/user by handle (e.g., "nytimes" or "https://github.com/nytimes")'
    )
    parser.add_argument(
        '--name', '-n',
        type=str,
        default=None,
        help='Display name for the org when using --org (e.g., "Star Tribune")'
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    logging.basicConfig(
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO,
    )

    args = parse_args()

    try:
        config = load_config()
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    # Allow command line override of minutes
    if args.minutes is not None:
        config['check_minutes'] = args.minutes

    # Fetch CSV (needed for both normal mode and --org lookup)
    logging.info(f"Fetching CSV from {config['csv_url']}...")
    try:
        csv_content = fetch_csv(config['csv_url'])
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch CSV: {e}")
        sys.exit(1)

    all_orgs = parse_csv(csv_content)

    # If --org is specified, find that org in the CSV
    if args.org:
        handle = args.org.lower().rstrip('/')
        # Allow either "striblab" or "https://github.com/striblab"
        if handle.startswith('http'):
            handle = handle.split('/')[-1]

        # Find matching org in CSV
        matching = [o for o in all_orgs if extract_github_username(o['github_url']).lower() == handle]

        if matching:
            orgs = matching
            logging.info(f"Testing single org: {orgs[0]['org_name']}")
        else:
            # Not in CSV, use handle as fallback
            github_url = f'https://github.com/{handle}'
            display_name = args.name if args.name else handle
            orgs = [{'org_name': display_name, 'github_url': github_url}]
            logging.info(f"Org '{handle}' not found in CSV, using handle as name")
    else:
        orgs = all_orgs

        # Apply limit if specified
        if args.limit > 0:
            orgs = orgs[:args.limit]
            logging.info(f"Limited to first {args.limit} organizations")

    logging.info(f"Checking {len(orgs)} organizations for repos created in last {config['check_minutes']} minutes...")

    if not config['github_token']:
        logging.warning("No GITHUB_TOKEN set. Rate limited to 60 requests/hour.")

    template = load_template()

    # Initialize BlueSky client if not in test mode
    bluesky_client = None
    if not config['test_mode']:
        logging.info("Logging into BlueSky...")
        bluesky_client = Client()
        bluesky_client.login(config['bluesky_handle'], config['bluesky_password'])

    total_new_repos = 0
    orgs_checked = 0

    for org in orgs:
        try:
            repos = fetch_recent_repos(
                org['github_url'],
                token=config['github_token'],
                minutes=config['check_minutes'],
            )
            orgs_checked += 1
        except RateLimitError as e:
            logging.error("GitHub API rate limit exceeded!")
            logging.error(f"Checked {orgs_checked} of {len(orgs)} organizations before hitting limit.")
            logging.error(f"Rate limit resets at: {e.reset_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            logging.error("Tip: Set GITHUB_TOKEN in .env for 5000 requests/hour.")
            sys.exit(1)

        for repo in repos:
            post_text = render_post(template, org['org_name'], repo)

            if config['test_mode']:
                logging.info("--- TEST MODE: Would post ---")
                logging.info(post_text)
                logging.info(f"[Link Card] Title: {repo['repo_name']}")
                logging.info(f"[Link Card] Description: {repo['description'] or 'A GitHub repository'}")
                logging.info(f"[Link Card] URL: {repo['repo_url']}")
                logging.info("----------------------------")
            else:
                logging.info(f"Posting about {org['org_name']}/{repo['repo_name']}...")
                post_to_bluesky(bluesky_client, post_text, repo)

            total_new_repos += 1

    logging.info(f"Done. Checked {orgs_checked} organizations, found {total_new_repos} new repos.")


if __name__ == '__main__':
    main()
