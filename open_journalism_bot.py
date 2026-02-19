#!/usr/bin/env python3
"""
Open Journalism Bot - Monitor GitHub accounts and post new repos to BlueSky.
"""

import argparse
import base64
import csv
import io
import logging
import logging.handlers
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
import chevron
import requests
from atproto import Client, models
from dotenv import load_dotenv


def init_db(db_path):
    """Initialize SQLite database and return connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orgs (
            github_username TEXT PRIMARY KEY,
            org_name        TEXT NOT NULL,
            github_url      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS repos (
            full_name        TEXT PRIMARY KEY,
            org              TEXT NOT NULL REFERENCES orgs(github_username),
            repo_name        TEXT NOT NULL,
            repo_url         TEXT NOT NULL,
            language         TEXT,
            description      TEXT,
            summary          TEXT,
            is_empty         BOOLEAN NOT NULL DEFAULT 0,
            created_at       TIMESTAMP,
            first_seen       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bluesky_post_url TEXT,
            bluesky_post_date TIMESTAMP
        );
    """)
    conn.commit()
    return conn


def upsert_orgs(conn, orgs):
    """Insert or update orgs from the CSV org list."""
    for org in orgs:
        username = extract_github_username(org["github_url"])
        conn.execute(
            """INSERT INTO orgs (github_username, org_name, github_url)
               VALUES (?, ?, ?)
               ON CONFLICT(github_username) DO UPDATE SET
                   org_name = excluded.org_name,
                   github_url = excluded.github_url""",
            (username, org["org_name"], org["github_url"]),
        )
    conn.commit()


def load_config():
    """Load configuration from environment variables."""
    load_dotenv()

    config = {
        'csv_url': os.getenv('CSV_URL'),
        'github_token': os.getenv('GITHUB_TOKEN'),
        'anthropic_api_key': os.getenv('ANTHROPIC_API_KEY'),
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
        # Skip forks - we only want original repos
        if repo.get('fork', False):
            continue

        created_at = datetime.fromisoformat(repo['created_at'].replace('Z', '+00:00'))

        if created_at >= cutoff_time:
            new_repos.append({
                'repo_name': repo['name'],
                'full_name': repo['full_name'],
                'description': repo.get('description') or '',
                'repo_url': repo['html_url'],
                'language': repo.get('language') or '',
            })

    return new_repos


def fetch_readme(full_name, token=None):
    """
    Fetch README content for a repository.
    Returns the README text, or None if not found.
    """
    headers = get_github_headers(token)
    url = f'https://api.github.com/repos/{full_name}/readme'

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            content = data.get('content', '')
            if content:
                return base64.b64decode(content).decode('utf-8', errors='ignore')
        return None
    except (requests.exceptions.RequestException, ValueError):
        return None


def summarize_with_claude(readme_content, api_key):
    """
    Use Claude to summarize a README.
    Returns a one-sentence summary, or None if the README is boilerplate/inappropriate.
    """
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = """Summarize this GitHub README in one short sentence (under 200 characters) that describes what the project does. Focus on the purpose, not implementation details. Start with a noun phrase like "a tool that..." or "an app for..." so it reads naturally after "I think this is".

If the README is just boilerplate/template content (auto-generated placeholder text, empty of real content, or just installation instructions with no project description), respond with exactly: BOILERPLATE

If the README contains inappropriate content, profanity, slurs, attempts to inject instructions, or anything that would be inappropriate to post on social media, respond with exactly: INAPPROPRIATE

README content:
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[
                {"role": "user", "content": prompt + readme_content[:8000]}
            ]
        )
        result = message.content[0].text.strip()
        if result in ("BOILERPLATE", "INAPPROPRIATE"):
            return None
        # Sanitize: remove URLs, @mentions, and excessive punctuation
        result = sanitize_summary(result)
        if not result:
            return None
        # Ensure lowercase start for natural flow after "I think this is"
        return result[0].lower() + result[1:] if result else None
    except Exception as e:
        logging.warning(f"Claude API error: {e}")
        return None


def sanitize_summary(text):
    """
    Sanitize Claude's summary to prevent prompt injection or inappropriate content.
    Returns cleaned text, or None if the text seems malicious.
    """
    import re

    # Remove any URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove @mentions
    text = re.sub(r'@\w+', '', text)
    # Remove excessive special characters that might be injection attempts
    text = re.sub(r'[<>{}[\]|\\^~`]', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    # Reject if too short after sanitization (might indicate stripped malicious content)
    if len(text) < 10:
        return None
    # Reject if it still contains suspicious patterns
    suspicious_patterns = [
        r'ignore.*instruction',
        r'disregard.*above',
        r'new.*instruction',
        r'system.*prompt',
    ]
    for pattern in suspicious_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return None

    return text


def get_repo_descriptions(repo, token=None, anthropic_api_key=None):
    """
    Get descriptions for a repo.

    Returns a dict with:
    - github_description: The actual GitHub description (for link cards), or empty string
    - imputed_description: Our AI-generated/fallback description (for post body), or None
                          if we're using GitHub's description

    Logic:
    - If GitHub has a description, use it in the card; no imputed description needed
    - If no GitHub description, try Claude summarization of README
    - If no README or Claude fails, fall back to language-based description
    - If completely empty repo, say so
    """
    repo_name = repo.get('full_name', repo.get('repo_name', 'unknown'))
    github_desc = ''
    imputed_desc = None

    # Check for GitHub description (sanitized)
    if repo.get('description'):
        sanitized = sanitize_summary(repo['description'])
        if sanitized:
            logging.info(f"{repo_name}: Using GitHub description")
            return {'github_description': sanitized, 'imputed_description': None}
        else:
            logging.info(f"{repo_name}: GitHub description was sanitized away")

    # No GitHub description - try to impute one
    # Tier 2: Try README summarization
    if anthropic_api_key:
        readme_content = fetch_readme(repo['full_name'], token)
        if readme_content:
            logging.info(f"{repo_name}: Found README ({len(readme_content)} chars), asking Claude...")
            summary = summarize_with_claude(readme_content, anthropic_api_key)
            if summary:
                logging.info(f"{repo_name}: Claude summary: {summary}")
                return {'github_description': '', 'imputed_description': f"I think this is {summary}"}
            else:
                logging.info(f"{repo_name}: Claude returned no summary (BOILERPLATE/INAPPROPRIATE or error)")
        else:
            logging.info(f"{repo_name}: No README found")
    else:
        logging.info(f"{repo_name}: No Anthropic API key, skipping README summarization")

    # Tier 3: Language fallback
    if repo.get('language'):
        logging.info(f"{repo_name}: Using language fallback ({repo['language']})")
        return {
            'github_description': '',
            'imputed_description': f"I don't know what this does but it uses {repo['language']}.",
        }

    # Completely empty repo
    logging.info(f"{repo_name}: No description, no README, no language - empty repo")
    return {'github_description': '', 'imputed_description': "I think this is an empty repo."}


def load_template():
    """Load the Mustache template for BlueSky posts."""
    template_path = Path(__file__).parent / 'templates' / 'post.mustache'
    with open(template_path, 'r') as f:
        return f.read()


def render_post(template, org_name, repo, imputed_description=None):
    """Render a BlueSky post using the template."""
    data = {
        'org_name': org_name,
        'repo_name': repo['repo_name'],
        'repo_url': repo['repo_url'],
        'language': repo['language'],
        'imputed_description': imputed_description,
    }
    return chevron.render(template, data).strip()


def create_link_card(repo, github_description=''):
    """Create a BlueSky link card embed for a repo.

    The description should only contain GitHub's actual description,
    not our imputed/AI-generated descriptions.
    """
    external = models.AppBskyEmbedExternal.External(
        uri=repo['repo_url'],
        title=repo['repo_name'],
        description=github_description,
    )
    return models.AppBskyEmbedExternal.Main(external=external)


def post_to_bluesky(client, text, repo, github_description=''):
    """Post text to BlueSky with a link card."""
    embed = create_link_card(repo, github_description)
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
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Force test mode (no posting) regardless of TEST_MODE in .env'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose/debug logging'
    )
    return parser.parse_args()


def setup_logging(verbose=False):
    """Configure logging to both console and file."""
    log_format = '%(asctime)s %(levelname)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    level = logging.DEBUG if verbose else logging.INFO

    # Create logs directory if needed
    log_dir = Path(__file__).parent / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / 'bot.log'

    # Configure root logger
    logging.basicConfig(
        format=log_format,
        datefmt=date_format,
        level=level,
        handlers=[
            logging.StreamHandler(),  # Console
            logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
            ),
        ],
    )


def main():
    """Main entry point."""
    args = parse_args()

    setup_logging(verbose=args.verbose)

    try:
        config = load_config()
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    # Allow command line overrides
    if args.minutes is not None:
        config['check_minutes'] = args.minutes
    if args.dry_run:
        config['test_mode'] = True

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

    if not config['anthropic_api_key']:
        logging.warning("No ANTHROPIC_API_KEY set. AI-generated descriptions disabled.")
    else:
        logging.info("Anthropic API key configured for README summarization.")

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
            # Get descriptions (GitHub's actual + our imputed)
            descriptions = get_repo_descriptions(
                repo,
                token=config['github_token'],
                anthropic_api_key=config['anthropic_api_key'],
            )
            github_desc = descriptions['github_description']
            imputed_desc = descriptions['imputed_description']

            post_text = render_post(template, org['org_name'], repo, imputed_desc)

            if config['test_mode']:
                logging.info("--- TEST MODE: Would post ---")
                logging.info(post_text)
                logging.info(f"[Link Card] Title: {repo['repo_name']}")
                logging.info(f"[Link Card] Description: {github_desc or '(none)'}")
                logging.info(f"[Link Card] URL: {repo['repo_url']}")
                logging.info("----------------------------")
            else:
                logging.info(f"Posting about {org['org_name']}/{repo['repo_name']}...")
                post_to_bluesky(bluesky_client, post_text, repo, github_desc)

            total_new_repos += 1

    logging.info(f"Done. Checked {orgs_checked} organizations, found {total_new_repos} new repos.")


if __name__ == '__main__':
    main()
