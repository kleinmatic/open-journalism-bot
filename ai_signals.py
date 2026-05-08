"""
ai_signals.py — scan a single GitHub repo for AI-coding artifacts.

Designed to plug into the open-journalism-bot's discovery pipeline. For each
non-fork repo, inspect the default-branch tree and record signals (CLAUDE.md,
AGENTS.md, .claude/, MCP configs, Cursor/Copilot/Aider, etc.) plus AI-tool
credits in recent commit messages, returned as a URL-shaped JSON-ready dict
suitable for storing in repos.ai_signals_json.

Each artifact signal is recorded as either:
  - None (not present),
  - a string URL pointing at the matched file or directory, or
  - a list of URL strings (when several files match a single signal).

Public surface:
  enrich_ai_signals(full_name, token=None) -> dict | None
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# --- regex patterns over tree paths -----------------------------------------

RE_CLAUDE_MD = re.compile(r"^CLAUDE\.md$", re.IGNORECASE)
RE_AGENTS_MD = re.compile(r"^AGENTS?\.md$", re.IGNORECASE)
RE_GEMINI_MD = re.compile(r"^GEMINI\.md$", re.IGNORECASE)
RE_SKILLS_MD = re.compile(r"^SKILLS?\.md$", re.IGNORECASE)
RE_CURSORRULES = re.compile(r"^\.cursorrules$", re.IGNORECASE)
RE_WINDSURFRULES = re.compile(r"^\.windsurfrules$", re.IGNORECASE)
RE_COPILOT = re.compile(r"^\.github/copilot-instructions\.md$", re.IGNORECASE)
RE_AIDER_CONF = re.compile(r"^\.aider\.conf\.ya?ml$", re.IGNORECASE)
RE_AIDER_ROOT = re.compile(r"^\.aider[^/]*$", re.IGNORECASE)
RE_MCP_JSON = re.compile(r"^\.?mcp\.json$", re.IGNORECASE)


# --- AI-commit credit patterns ----------------------------------------------

COMMIT_PATTERNS: dict[str, list[re.Pattern]] = {
    "claude": [
        re.compile(r"Claude Code", re.IGNORECASE),
        re.compile(r"Co-Authored-By:\s*Claude", re.IGNORECASE),
        re.compile(r"Generated with \[Claude Code\]", re.IGNORECASE),
        re.compile(r"noreply@anthropic\.com", re.IGNORECASE),
    ],
    "cursor": [
        re.compile(r"Co-Authored-By:\s*Cursor", re.IGNORECASE),
        re.compile(r"\bcursor\.com\b", re.IGNORECASE),
    ],
    "aider": [
        re.compile(r"\baider:", re.IGNORECASE),
        re.compile(r"\[aider\]", re.IGNORECASE),
        re.compile(r"^aider:", re.IGNORECASE | re.MULTILINE),
        re.compile(r"aider-AI/aider", re.IGNORECASE),
    ],
    "codex": [
        re.compile(r"Co-Authored-By:\s*Codex", re.IGNORECASE),
        re.compile(r"Generated with OpenAI Codex", re.IGNORECASE),
        re.compile(r"Co-Authored-By:\s*openai", re.IGNORECASE),
        re.compile(r"noreply@openai\.com", re.IGNORECASE),
    ],
    "copilot": [
        re.compile(r"Co-Authored-By:\s*GitHub Copilot", re.IGNORECASE),
        re.compile(r"copilot-pull-request-reviewer", re.IGNORECASE),
        re.compile(r"noreply\.github\.com.*[Cc]opilot", re.IGNORECASE),
        re.compile(r"[Cc]opilot.*noreply\.github\.com", re.IGNORECASE),
    ],
    "devin": [
        re.compile(r"Co-Authored-By:\s*Devin", re.IGNORECASE),
        re.compile(r"devin-ai-integration", re.IGNORECASE),
    ],
    "gemini": [
        re.compile(r"Co-Authored-By:\s*Gemini", re.IGNORECASE),
        re.compile(r"\bgemini-cli\b", re.IGNORECASE),
    ],
}


# --- HTTP helpers ------------------------------------------------------------

def _headers(token: Optional[str]) -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_get(path: str, token: Optional[str]) -> tuple[int, object]:
    """GET https://api.github.com/{path}. Returns (status, parsed_json | None)."""
    url = f"https://api.github.com/{path.lstrip('/')}"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=30)
    except requests.exceptions.RequestException as e:
        logger.warning(f"ai_signals: GET {path} raised {type(e).__name__}: {e}")
        return 0, None
    try:
        body = resp.json() if resp.text else None
    except ValueError:
        body = None
    return resp.status_code, body


# --- URL builders ------------------------------------------------------------

def _file_url(full_name: str, branch: str, path: str) -> str:
    return f"https://github.com/{full_name}/blob/{branch}/{path}"


def _tree_url(full_name: str, branch: str, path: str) -> str:
    return f"https://github.com/{full_name}/tree/{branch}/{path}"


def _first_or_list(urls: list[str]):
    if not urls:
        return None
    if len(urls) == 1:
        return urls[0]
    return urls


# --- signal extraction -------------------------------------------------------

def _extract_signals(full_name: str, tree_paths: list[str], path_types: dict[str, str],
                     topics: list[str], default_branch: str,
                     archived: bool, truncated: bool) -> dict:
    paths = tree_paths

    def kind(p: str) -> str:
        return path_types.get(p, "blob")

    def url_for(p: str) -> str:
        if kind(p) == "tree":
            return _tree_url(full_name, default_branch, p)
        return _file_url(full_name, default_branch, p)

    def matches(regex: re.Pattern) -> list[str]:
        return [url_for(p) for p in paths if regex.match(p)]

    # .claude/ direct subdirs
    dot_claude_url = None
    dot_claude_subdirs: list[str] = []
    has_dot_claude = False
    for p in paths:
        if p == ".claude" or p.startswith(".claude/"):
            has_dot_claude = True
            if p.startswith(".claude/"):
                rest = p[len(".claude/"):]
                if rest:
                    first = rest.split("/", 1)[0]
                    if first not in dot_claude_subdirs:
                        sub_full = f".claude/{first}"
                        is_dir = (path_types.get(sub_full) == "tree" or
                                  any(p2.startswith(sub_full + "/") for p2 in paths))
                        if is_dir:
                            dot_claude_subdirs.append(first)
    if has_dot_claude:
        dot_claude_url = _tree_url(full_name, default_branch, ".claude")
    dot_claude_subdirs.sort()

    cursor_urls: list[str] = []
    for p in paths:
        if RE_CURSORRULES.match(p):
            cursor_urls.append(_file_url(full_name, default_branch, p))
    if any(p == ".cursor" for p in paths) or any(p.startswith(".cursor/") for p in paths):
        cursor_urls.append(_tree_url(full_name, default_branch, ".cursor"))

    windsurf_urls: list[str] = []
    for p in paths:
        if RE_WINDSURFRULES.match(p):
            windsurf_urls.append(_file_url(full_name, default_branch, p))
    if any(p == ".windsurf" for p in paths) or any(p.startswith(".windsurf/") for p in paths):
        windsurf_urls.append(_tree_url(full_name, default_branch, ".windsurf"))

    aider_urls: list[str] = []
    for p in paths:
        if RE_AIDER_CONF.match(p) or RE_AIDER_ROOT.match(p):
            aider_urls.append(url_for(p))

    dot_codex_url = None
    if any(p == ".codex" for p in paths) or any(p.startswith(".codex/") for p in paths):
        dot_codex_url = _tree_url(full_name, default_branch, ".codex")

    continue_url = None
    if any(p == ".continue" for p in paths) or any(p.startswith(".continue/") for p in paths):
        continue_url = _tree_url(full_name, default_branch, ".continue")

    specstory_url = None
    if any(p == ".specstory" for p in paths) or any(p.startswith(".specstory/") for p in paths):
        specstory_url = _tree_url(full_name, default_branch, ".specstory")

    root_skills_url = None
    if any(p == "skills" for p in paths) or any(p.startswith("skills/") for p in paths):
        root_skills_url = _tree_url(full_name, default_branch, "skills")

    root_agents_url = None
    if any(p == "agents" for p in paths) or any(p.startswith("agents/") for p in paths):
        root_agents_url = _tree_url(full_name, default_branch, "agents")

    mcp_urls = [_file_url(full_name, default_branch, p) for p in paths
                if RE_MCP_JSON.match(p)]

    copilot_urls = [_file_url(full_name, default_branch, p) for p in paths
                    if RE_COPILOT.match(p)]

    return {
        "claude_md": _first_or_list(matches(RE_CLAUDE_MD)),
        "agents_md": _first_or_list(matches(RE_AGENTS_MD)),
        "gemini_md": _first_or_list(matches(RE_GEMINI_MD)),
        "skills_md": _first_or_list(matches(RE_SKILLS_MD)),
        "dot_claude": dot_claude_url,
        "dot_claude_subdirs": dot_claude_subdirs if has_dot_claude else None,
        "cursor": _first_or_list(cursor_urls),
        "windsurf": _first_or_list(windsurf_urls),
        "copilot_instructions": _first_or_list(copilot_urls),
        "aider": _first_or_list(aider_urls),
        "dot_codex": dot_codex_url,
        "continue_dir": continue_url,
        "specstory": specstory_url,
        "mcp_json": _first_or_list(mcp_urls),
        "root_skills_dir": root_skills_url,
        "root_agents_dir": root_agents_url,
        "topics": list(topics),
        "default_branch": default_branch,
        "tree_truncated": truncated,
        "archived": archived,
    }


def _fetch_top_level_paths(full_name: str, token: Optional[str]) -> tuple[list[str], dict[str, str], bool]:
    """Fallback when the recursive tree is unavailable."""
    paths: list[str] = []
    path_types: dict[str, str] = {}
    status, body = _gh_get(f"repos/{full_name}/contents", token)
    if status != 200 or not isinstance(body, list):
        return paths, path_types, False
    for entry in body:
        name = entry.get("name", "")
        if not name:
            continue
        paths.append(name)
        path_types[name] = "tree" if entry.get("type") == "dir" else "blob"
    for sub in (".claude", ".cursor", ".windsurf", ".github", ".codex",
                ".continue", ".specstory", "skills", "agents"):
        if path_types.get(sub) == "tree":
            s2, b2 = _gh_get(f"repos/{full_name}/contents/{sub}", token)
            if s2 == 200 and isinstance(b2, list):
                for child in b2:
                    cname = child.get("name", "")
                    ctype = child.get("type", "")
                    if not cname:
                        continue
                    full = f"{sub}/{cname}"
                    paths.append(full)
                    path_types[full] = "tree" if ctype == "dir" else "blob"
    return paths, path_types, True


def _scan_commits(full_name: str, token: Optional[str]) -> Optional[dict]:
    status, body = _gh_get(f"repos/{full_name}/commits?per_page=100", token)
    if status != 200 or not isinstance(body, list):
        return None

    counts: dict[str, dict] = {tool: {"count": 0} for tool in COMMIT_PATTERNS}
    for commit in body:
        if not isinstance(commit, dict):
            continue
        commit_obj = commit.get("commit") or {}
        if isinstance(commit_obj, dict):
            message = commit_obj.get("message") or ""
            author_obj = commit_obj.get("author") or {}
            committer_obj = commit_obj.get("committer") or {}
        else:
            message = ""
            author_obj = {}
            committer_obj = {}
        top_author = commit.get("author") or {}
        top_committer = commit.get("committer") or {}

        haystack_parts = [
            message,
            author_obj.get("name", "") if isinstance(author_obj, dict) else "",
            author_obj.get("email", "") if isinstance(author_obj, dict) else "",
            committer_obj.get("name", "") if isinstance(committer_obj, dict) else "",
            committer_obj.get("email", "") if isinstance(committer_obj, dict) else "",
            top_author.get("login", "") if isinstance(top_author, dict) else "",
            top_committer.get("login", "") if isinstance(top_committer, dict) else "",
        ]
        haystack = "\n".join(p for p in haystack_parts if p)
        if not haystack:
            continue

        sha = commit.get("sha") or ""
        html_url = commit.get("html_url") or (
            f"https://github.com/{full_name}/commit/{sha}" if sha else ""
        )
        for tool, patterns in COMMIT_PATTERNS.items():
            if any(pat.search(haystack) for pat in patterns):
                counts[tool]["count"] += 1
                if "first_url" not in counts[tool] and html_url:
                    counts[tool]["first_url"] = html_url

    return {"checked_n": len(body), **counts}


def enrich_ai_signals(full_name: str, token: Optional[str] = None) -> Optional[dict]:
    """Scan a repo for AI-coding artifacts and commit-message AI credits.

    Returns a dict suitable for json.dumps into repos.ai_signals_json, or None
    if the repo metadata couldn't be fetched at all. For forks returns
    {"fork": True}. For 404/permission failures returns
    {"unreachable": True, "status": N, ...}.
    """
    status, repo = _gh_get(f"repos/{full_name}", token)
    if status == 404:
        return {"unreachable": True, "status": 404, "ai_commits": None}
    if status >= 400 or not isinstance(repo, dict):
        return {"unreachable": True, "status": status, "ai_commits": None}

    if repo.get("fork"):
        return {"fork": True}

    default_branch = repo.get("default_branch") or "main"
    topics = repo.get("topics") or []
    archived = bool(repo.get("archived"))

    s2, tree = _gh_get(
        f"repos/{full_name}/git/trees/{default_branch}?recursive=1", token,
    )
    if s2 == 404 or not isinstance(tree, dict):
        paths, path_types, ok = _fetch_top_level_paths(full_name, token)
        if not ok:
            return {
                "unreachable": True,
                "status": s2,
                "default_branch": default_branch,
                "topics": list(topics),
                "archived": archived,
                "ai_commits": _scan_commits(full_name, token),
            }
        signals = _extract_signals(full_name, paths, path_types, topics,
                                   default_branch, archived, False)
        signals["ai_commits"] = _scan_commits(full_name, token)
        return signals

    truncated = bool(tree.get("truncated"))
    items = tree.get("tree") or []
    paths: list[str] = []
    path_types: dict[str, str] = {}
    for t in items:
        p = t.get("path", "")
        if not p:
            continue
        paths.append(p)
        ttype = t.get("type", "blob")
        path_types[p] = "tree" if ttype == "tree" else "blob"

    if truncated:
        extra, extra_types, _ = _fetch_top_level_paths(full_name, token)
        seen = set(paths)
        for p in extra:
            if p not in seen:
                paths.append(p)
                seen.add(p)
            if p in extra_types and p not in path_types:
                path_types[p] = extra_types[p]

    signals = _extract_signals(full_name, paths, path_types, topics,
                               default_branch, archived, truncated)
    signals["ai_commits"] = _scan_commits(full_name, token)
    return signals
