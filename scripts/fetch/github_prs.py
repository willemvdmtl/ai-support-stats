#!/usr/bin/env python3
"""Fetch GitHub pull requests for owned repositories."""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from common.setup_utils import check_github_auth, read_json, write_json

REPOS_FILE = "config/owned-repositories.json"
MANIFEST_FILE = "cache/github/manifest.json"
RAW_DIR = "cache/github/raw"
UPDATES_DIR = "cache/github/updates"
CONSOLIDATED_DIR = "cache/github"
DEFAULT_MAX_AGE_HOURS = 4
SEARCH_DELAY_SECONDS = 2


def _github_request(token: str, url: str, params: Optional[Dict] = None) -> Dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_prs_for_repo_month(token: str, repo: str, year: int, month: int) -> List[Dict]:
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

    query = f"repo:{repo} is:pr created:{start.isoformat()}..{end.isoformat()}"
    all_items: List[Dict] = []
    page = 1

    while True:
        payload = _github_request(
            token,
            "https://api.github.com/search/issues",
            {"q": query, "per_page": 100, "page": page},
        )
        items = payload.get("items", [])
        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1
        time.sleep(SEARCH_DELAY_SECONDS)

    return all_items


def search_prs_updated_since(token: str, org: str, since: str) -> List[Dict]:
    query = f"org:{org} is:pr updated:>{since}"
    all_items: List[Dict] = []
    page = 1

    while True:
        payload = _github_request(
            token,
            "https://api.github.com/search/issues",
            {"q": query, "per_page": 100, "page": page, "sort": "updated", "order": "asc"},
        )
        items = payload.get("items", [])
        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1
        time.sleep(SEARCH_DELAY_SECONDS)

    return all_items


def repo_from_pr(pr: Dict) -> str:
    repo_url = pr.get("repository_url", "")
    if "/repos/" in repo_url:
        return repo_url.split("/repos/", 1)[1]
    html_url = pr.get("html_url", "")
    parts = html_url.replace("https://github.com/", "").split("/pull/")
    if len(parts) == 2:
        return parts[0]
    return "unknown"


def pr_key(pr: Dict) -> Tuple[str, int]:
    return (repo_from_pr(pr), pr.get("number", 0))


def load_manifest() -> Dict:
    m = read_json(MANIFEST_FILE)
    m.setdefault("months", {})
    m.setdefault("updates_sweeps", [])
    m.setdefault("last_updates_sweep_at", "")
    return m


def save_manifest(manifest: Dict) -> None:
    write_json(MANIFEST_FILE, manifest)


def month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def raw_dir_for_month(year: int, month: int) -> str:
    return os.path.join(RAW_DIR, month_key(year, month))


def raw_file_for_repo(year: int, month: int, repo: str) -> str:
    slug = repo.replace("/", "__")
    return os.path.join(raw_dir_for_month(year, month), f"{slug}.json")


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"prs-{month_key(year, month)}.json")


def month_is_in_past(year: int, month: int) -> bool:
    today = dt.date.today()
    return (year, month) < (today.year, today.month)


def repo_fetch_status(manifest: Dict, year: int, month: int, repo: str) -> str:
    mk = month_key(year, month)
    entry = manifest["months"].get(mk, {}).get("repos", {}).get(repo)
    if not entry:
        return "missing"

    status = entry.get("status", "missing")
    if status == "complete":
        return "complete"

    if month_is_in_past(year, month) and entry.get("month_was_live_at_fetch"):
        return "stale"

    fetched_at = entry.get("fetched_at", "")
    if fetched_at:
        age = dt.datetime.utcnow() - dt.datetime.fromisoformat(fetched_at.rstrip("Z"))
        if age.total_seconds() > DEFAULT_MAX_AGE_HOURS * 3600:
            return "stale"

    return status


def mark_repo_fetched(manifest: Dict, year: int, month: int, repo: str, pr_count: int) -> None:
    mk = month_key(year, month)
    manifest["months"].setdefault(mk, {"repos": {}})
    now = dt.datetime.utcnow().isoformat() + "Z"
    past = month_is_in_past(year, month)
    manifest["months"][mk]["repos"][repo] = {
        "status": "complete" if past else "partial",
        "fetched_at": now,
        "month_was_live_at_fetch": not past,
        "pr_count": pr_count,
    }


def consolidate_month(year: int, month: int) -> int:
    mk = month_key(year, month)
    raw_month_dir = raw_dir_for_month(year, month)
    prs_by_key: Dict[Tuple, Dict] = {}
    source_files: List[str] = []

    if os.path.isdir(raw_month_dir):
        for fname in sorted(os.listdir(raw_month_dir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(raw_month_dir, fname)
            source_files.append(fpath)
            raw = read_json(fpath)
            for pr in raw.get("pull_requests", []):
                key = pr_key(pr)
                existing = prs_by_key.get(key)
                if not existing or pr.get("updated_at", "") > existing.get("updated_at", ""):
                    prs_by_key[key] = pr

    if os.path.isdir(UPDATES_DIR):
        for fname in sorted(os.listdir(UPDATES_DIR)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(UPDATES_DIR, fname)
            updates = read_json(fpath)
            for pr in updates.get("pull_requests", []):
                created = pr.get("created_at", "")
                if not created.startswith(mk):
                    continue
                key = pr_key(pr)
                existing = prs_by_key.get(key)
                if not existing or pr.get("updated_at", "") > existing.get("updated_at", ""):
                    prs_by_key[key] = pr
                    if fpath not in source_files:
                        source_files.append(fpath)

    sorted_prs = sorted(prs_by_key.values(), key=lambda p: p.get("created_at", ""))

    payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "month": mk,
        "pr_count": len(sorted_prs),
        "source_files": source_files,
        "pull_requests": sorted_prs,
    }
    write_json(consolidated_file(year, month), payload)
    return len(sorted_prs)


def cmd_fetch(repos: List[str], year: int, month: int, token: str, force: bool) -> None:
    manifest = load_manifest()
    mk = month_key(year, month)
    os.makedirs(raw_dir_for_month(year, month), exist_ok=True)

    needs_consolidate = False
    total_skipped = 0

    for repo in repos:
        status = repo_fetch_status(manifest, year, month, repo)
        if status == "complete" and not force:
            total_skipped += 1
            continue

        print(f"  [{'force' if status == 'complete' and force else status}] {repo}")

        try:
            prs = search_prs_for_repo_month(token, repo, year, month)
        except Exception as exc:
            print(f"  ERROR fetching {repo}: {exc}")
            continue

        raw_payload = {
            "fetched_at": dt.datetime.utcnow().isoformat() + "Z",
            "repo": repo,
            "month": mk,
            "date_range": {
                "start": dt.date(year, month, 1).isoformat(),
                "end": (
                    dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
                    if month == 12
                    else dt.date(year, month + 1, 1) - dt.timedelta(days=1)
                ).isoformat(),
            },
            "pr_count": len(prs),
            "pull_requests": prs,
        }
        write_json(raw_file_for_repo(year, month, repo), raw_payload)
        mark_repo_fetched(manifest, year, month, repo, len(prs))
        save_manifest(manifest)

        needs_consolidate = True
        time.sleep(SEARCH_DELAY_SECONDS)

    if total_skipped:
        print(f"  Skipped {total_skipped} repo(s) with complete cache (use --force to re-fetch)")

    if needs_consolidate:
        pr_count = consolidate_month(year, month)
        print(f"\nConsolidated: {pr_count} PR(s) in {mk}  -> {consolidated_file(year, month)}")
    else:
        print(f"\nAll repos already complete for {mk}. Nothing to fetch.")


def cmd_check_updates(token: str, org: str) -> None:
    manifest = load_manifest()
    since = manifest.get("last_updates_sweep_at", "")

    if not since:
        since = (dt.datetime.utcnow() - dt.timedelta(days=30)).isoformat() + "Z"
        print(f"No previous sweep found. Sweeping back to {since}")
    else:
        print(f"Sweeping for PRs updated since {since}")

    try:
        prs = search_prs_updated_since(token, org, since)
    except Exception as exc:
        print(f"ERROR during update sweep: {exc}")
        sys.exit(1)

    now = dt.datetime.utcnow()
    now_str = now.isoformat() + "Z"
    fname = now.strftime("%Y-%m-%dT%H%M%S") + ".json"
    fpath = os.path.join(UPDATES_DIR, fname)

    os.makedirs(UPDATES_DIR, exist_ok=True)
    write_json(fpath, {
        "swept_at": now_str,
        "since": since,
        "pr_count": len(prs),
        "pull_requests": prs,
    })

    manifest["last_updates_sweep_at"] = now_str
    manifest["updates_sweeps"].append({
        "swept_at": now_str,
        "since": since,
        "pr_count": len(prs),
        "file": fpath,
    })
    save_manifest(manifest)

    print(f"Update sweep complete: {len(prs)} PR(s) updated since {since}")
    print(f"Saved to: {fpath}")

    affected_months = set()
    for pr in prs:
        created = pr.get("created_at", "")
        if len(created) >= 7:
            affected_months.add(created[:7])

    if affected_months:
        print(f"\nRegenerating consolidated files for: {', '.join(sorted(affected_months))}")
        for mk in sorted(affected_months):
            y, m = int(mk[:4]), int(mk[5:7])
            pr_count = consolidate_month(y, m)
            print(f"  {mk}: {pr_count} PR(s)  ->  {consolidated_file(y, m)}")


def cmd_consolidate_only(year: int, month: int) -> None:
    pr_count = consolidate_month(year, month)
    mk = month_key(year, month)
    print(f"Consolidated: {pr_count} PR(s) for {mk}  ->  {consolidated_file(year, month)}")


def cmd_status() -> None:
    manifest = load_manifest()
    months = manifest.get("months", {})
    if not months:
        print("No cached months found.")
        return

    for mk in sorted(months.keys()):
        repos = months[mk].get("repos", {})
        total = len(repos)
        complete = sum(1 for r in repos.values() if r.get("status") == "complete")
        stale = sum(1 for r in repos.values() if r.get("month_was_live_at_fetch"))
        c_file = consolidated_file(int(mk[:4]), int(mk[5:7]))
        c_exists = "yes" if os.path.exists(c_file) else "no"
        print(f"  {mk}  repos: {complete}/{total} complete  stale: {stale}  consolidated: {c_exists}")

    last_sweep = manifest.get("last_updates_sweep_at", "none")
    print(f"\nLast update sweep: {last_sweep}")


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        d = dt.datetime.strptime(raw, "%Y-%m")
        return d.year, d.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def resolve_repos_file(path: str) -> str:
    if os.path.exists(path):
        return path
    if path == REPOS_FILE:
        legacy = "data/config/owned-repositories.json"
        if os.path.exists(legacy):
            return legacy
    return path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Fetch GitHub PRs for owned repositories.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cache is marked complete.")
    parser.add_argument("--check-updates", action="store_true", help="Sweep for PRs updated since last sweep.")
    parser.add_argument("--consolidate-only", action="store_true", help="Rebuild derived files without fetching.")
    parser.add_argument("--status", action="store_true", help="Show cache coverage and exit.")
    parser.add_argument("--repos-file", default=REPOS_FILE, help=f"Path to owned-repositories.json (default: {REPOS_FILE})")
    args = parser.parse_args(argv)

    if args.status:
        cmd_status()
        return

    today = dt.date.today()
    year, month = parse_month(args.month) if args.month else (today.year, today.month)

    if args.consolidate_only:
        cmd_consolidate_only(year, month)
        return

    github_ok, token, login_or_error = check_github_auth()
    if not github_ok:
        print("ERROR: GitHub auth check failed.")
        print(f"Reason: {login_or_error}")
        print("\nTo fix:  gh auth login")
        sys.exit(1)

    repos_file = resolve_repos_file(args.repos_file)
    repos_data = read_json(repos_file)
    if not repos_data:
        print(f"ERROR: Could not read {repos_file}")
        print("Run setup first:  python3 scripts/setup.py --capabilities 1")
        sys.exit(1)

    repos: List[str] = repos_data.get("repositories", [])
    org: str = repos_data.get("organization", "")

    if not repos:
        print(f"ERROR: No repositories found in {repos_file}")
        sys.exit(1)

    mk = month_key(year, month)
    print(f"GitHub PR fetch  |  month: {mk}  |  org: {org}  |  repos: {len(repos)}")

    if args.check_updates:
        cmd_check_updates(token, org)
        return

    print()
    cmd_fetch(repos, year, month, token, force=args.force)


if __name__ == "__main__":
    main()
