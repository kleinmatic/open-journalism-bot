# Open Journalism Bot

Monitor GitHub accounts from journalism organizations and post to BlueSky when new public repositories are created.

## Background

This is a spiritual successor to [@newsnerdrepos](https://x.com/newsnerdrepos), a Twitter bot that tracked open source releases from news organizations. With Twitter's API changes, the bot went dormant. This project brings it back on BlueSky.

Uses the [silva-shih/open-journalism](https://github.com/silva-shih/open-journalism) list of news organization GitHub accounts.

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- GitHub account (for API token)
- BlueSky account

### 1. Clone and install

```bash
git clone https://github.com/kleinmatic/open-journalism-bot.git
cd open-journalism-bot
uv sync
```

### 2. Get API credentials

**GitHub token** (increases rate limit from 60 to 5,000 requests/hour):
1. Go to https://github.com/settings/tokens?type=beta
2. Click "Generate new token"
3. Name it (e.g., "open-journalism-bot")
4. Select "Public Repositories (read-only)"
5. Generate and copy the token

**BlueSky app password**:
1. Go to https://bsky.app/settings/app-passwords
2. Click "Add App Password"
3. Name it (e.g., "open-journalism-bot")
4. Copy the generated password

**Mastodon access token** (optional — enables cross-posting):
1. Go to `<your-server>/settings/applications` and click "New application"
2. Name it (e.g., "open-journalism-bot")
3. Uncheck every scope except **`write:statuses`**
4. Submit, reopen the application, and copy "Your access token"

Set `MASTODON_API_URL` to the server's **API host**, which is not always the same
as the handle domain — `@you@palewi.re` accounts are served from
`https://mastodon.palewi.re`. Leave `MASTODON_ACCESS_TOKEN` blank and the bot
posts to BlueSky only.

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
CSV_URL=https://raw.githubusercontent.com/silva-shih/open-journalism/master/orgs.csv
GITHUB_TOKEN=github_pat_xxxxx
BLUESKY_HANDLE=your-handle.bsky.social
BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
CHECK_MINUTES=59
TEST_MODE=true
```

### 4. Test it

```bash
# Test mode (prints to stdout, doesn't post)
uv run open_journalism_bot.py --limit 10 --dry-run

# Test a specific org
uv run open_journalism_bot.py --org striblab --minutes 1440 --dry-run
```

**Important:** Always use `--dry-run` when testing if `TEST_MODE=false` in your `.env`, otherwise posts will go live.

### 5. Run for real

Set `TEST_MODE=false` in `.env`, then:

```bash
uv run open_journalism_bot.py
```

## Command Line Options

```
--limit N, -l N     Limit to first N organizations (useful for testing)
--minutes N, -m N   Override CHECK_MINUTES from .env
--org HANDLE, -o    Test a single org by GitHub handle (e.g., "nytimes")
--name NAME, -n     Display name when using --org for orgs not in CSV
--dry-run           Force test mode (no posting) regardless of TEST_MODE in .env
```

Examples:

```bash
# Check all orgs, last 59 minutes (production)
uv run open_journalism_bot.py

# Check first 20 orgs only (dry run)
uv run open_journalism_bot.py --limit 20 --dry-run

# Check specific org, last 24 hours (dry run)
uv run open_journalism_bot.py --org propublica --minutes 1440 --dry-run

# Check org not in CSV (dry run)
uv run open_journalism_bot.py --org someorg --name "Some Organization" --minutes 60 --dry-run
```

## Cron Setup

Run hourly (use 59 minutes to avoid duplicate posts at boundaries):

```cron
0 * * * * cd /path/to/open-journalism-bot && uv run open_journalism_bot.py >> /var/log/open-journalism-bot.log 2>&1
```

## Customizing Posts

Edit `templates/post.mustache` to change the post format. Available variables:

- `{{org_name}}` - Organization name from CSV
- `{{repo_name}}` - Repository name
- `{{description}}` - Repository description
- `{{repo_url}}` - Repository URL
- `{{language}}` - Primary programming language

Posts include an embedded link card with the repo title, description, and URL.

## Mastodon Cross-Posting

When `MASTODON_API_URL` and `MASTODON_ACCESS_TOKEN` are both set, every BlueSky
post also goes out as a Mastodon status with the same text. Mastodon builds its
own link preview by crawling the repo URL, so there's no card to construct.

BlueSky remains the gate for *what* gets posted. Mastodon failures are logged and
alerted but never interrupt the BlueSky path, and a missed post is retried on the
next hourly run — bounded to the last 24 hours, so switching Mastodon on seeds a
day of posts rather than replaying the whole back catalog. Posts are tracked
separately in `repos.mastodon_post_url` / `mastodon_post_date`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CSV_URL` | Yes | URL to CSV of GitHub accounts to monitor |
| `GITHUB_TOKEN` | No | GitHub PAT for higher rate limits (recommended) |
| `BLUESKY_HANDLE` | When posting | Your BlueSky handle |
| `BLUESKY_APP_PASSWORD` | When posting | BlueSky app password |
| `MASTODON_API_URL` | For Mastodon | Server API host, e.g. `https://mastodon.palewi.re` |
| `MASTODON_ACCESS_TOKEN` | For Mastodon | Token with the `write:statuses` scope |
| `CHECK_MINUTES` | No | Time window to check (default: 15) |
| `TEST_MODE` | No | Set to `false` to post for real (default: true) |

## Rate Limits

| Mode | Requests/hour |
|------|---------------|
| No GitHub token | 60 |
| With GitHub token | 5,000 |

The open-journalism CSV has ~300 organizations, so a GitHub token is recommended.

## Future Plans

- [ ] GitHub Actions workflow for scheduled runs (no local cron needed)
- [ ] SQLite database to track posted repos (better duplicate prevention)
- [ ] Thumbnail images in link cards

## License

MIT
