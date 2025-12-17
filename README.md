# Open Journalism Bot

Monitor GitHub accounts from journalism organizations and post to BlueSky when new public repositories are created.

## Background

This is a spiritual successor to [@newsnerdrepos](https://x.com/newsnerdrepos), a Twitter bot that tracked open source releases from news organizations. With Twitter's API changes, the bot went dormant. This project brings it back on BlueSky.

Uses the [palewire/open-journalism](https://github.com/palewire/open-journalism) list of news organization GitHub accounts.

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

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
CSV_URL=https://raw.githubusercontent.com/palewire/open-journalism/master/orgs.csv
GITHUB_TOKEN=github_pat_xxxxx
BLUESKY_HANDLE=openjournalism.bsky.social
BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
CHECK_MINUTES=59
TEST_MODE=true
```

### 4. Test it

```bash
# Test mode (prints to stdout, doesn't post)
uv run open_journalism_bot.py --limit 10

# Test a specific org
uv run open_journalism_bot.py --org striblab --minutes 1440
```

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
```

Examples:

```bash
# Check all orgs, last 59 minutes
uv run open_journalism_bot.py

# Check first 20 orgs only
uv run open_journalism_bot.py --limit 20

# Check specific org, last 24 hours
uv run open_journalism_bot.py --org propublica --minutes 1440

# Check org not in CSV
uv run open_journalism_bot.py --org someorg --name "Some Organization" --minutes 60
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

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CSV_URL` | Yes | URL to CSV of GitHub accounts to monitor |
| `GITHUB_TOKEN` | No | GitHub PAT for higher rate limits (recommended) |
| `BLUESKY_HANDLE` | When posting | Your BlueSky handle |
| `BLUESKY_APP_PASSWORD` | When posting | BlueSky app password |
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
