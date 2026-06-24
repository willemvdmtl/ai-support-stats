#!/usr/bin/env python3
"""Fetch Jira tickets for deterministic Jira caching and derived month files.

Cache layout:

  cache/jira/raw/YYYY-MM/<run_id>/search_page_001.json
      Immutable raw Jira search responses for a month fetch run.

  cache/jira/updates/YYYY-MM-DDTHHMMSS.json
      Append-only update sweep results (raw pages).

  cache/jira/manifest.json
      Fetch coverage and sweep history.

  cache/jira/by-created/issues-YYYY-MM.json
      Canonical issue storage partitioned by issue created month.

  cache/jira/index-updated/updated-YYYY-MM.json
      Thin index of keys updated in the month, for activity tracking.
"""

import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from common.setup_utils import JIRA_CREDENTIAL_SERVICE, credential_get, read_json, write_json

CONFIG_FILE = "config/jira-minimal.json"
MANIFEST_FILE = "cache/jira/manifest.json"
RAW_DIR = "cache/jira/raw"
UPDATES_DIR = "cache/jira/updates"
CONSOLIDATED_DIR = "cache/jira"
CANONICAL_CREATED_DIR = "cache/jira/by-created"
UPDATED_INDEX_DIR = "cache/jira/index-updated"

DEFAULT_MAX_AGE_HOURS = 4
REQUEST_DELAY_SECONDS = 1
DEFAULT_PAGE_SIZE = 100


def normalize_site(site: str) -> str:
    if site.startswith("http://") or site.startswith("https://"):
        return site.rstrip("/")
    return f"https://{site}".rstrip("/")


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        d = dt.datetime.strptime(raw, "%Y-%m")
        return d.year, d.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def month_bounds(year: int, month: int) -> Tuple[str, str]:
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def month_is_in_past(year: int, month: int) -> bool:
    today = dt.date.today()
    return (year, month) < (today.year, today.month)


def quote_jql(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def load_jira_config(config_file: str) -> Dict:
    resolved_config_file = config_file
    if not os.path.exists(resolved_config_file) and config_file == CONFIG_FILE:
        legacy = "data/config/jira-minimal.json"
        if os.path.exists(legacy):
            resolved_config_file = legacy

    config = read_json(resolved_config_file)
    if not config:
        print(f"ERROR: Could not read Jira config file: {resolved_config_file}")
        print("Run setup first:  python3 scripts/setup.py --capabilities 3")
        sys.exit(1)
    return config


def load_jira_runtime(config_file: str) -> Tuple[str, str, str, str, List[str]]:
    config = load_jira_config(config_file)

    site = (config.get("site") or "").strip()
    email = (config.get("email") or "").strip()
    project = (config.get("project") or "").strip()
    issue_types = config.get("issue_types") or []

    credential_usernames = config.get("credential_usernames") or {}
    token_username = credential_usernames.get("api_token", "ai-support-stats.jira.api-token")
    api_token = credential_get(JIRA_CREDENTIAL_SERVICE, token_username)

    missing = []
    if not site:
        missing.append("site")
    if not email:
        missing.append("email")
    if not project:
        missing.append("project")
    if not api_token:
        missing.append(f"jira api token in keyring username '{token_username}'")

    if missing:
        print("ERROR: Jira runtime config is incomplete:")
        for m in missing:
            print(f"  - {m}")
        print("Fix:  python3 scripts/setup.py --capabilities 3")
        sys.exit(1)

    return normalize_site(site), email, api_token, project, issue_types


def jira_search(site: str, email: str, api_token: str, jql: str, max_results: int, next_page_token: Optional[str] = None) -> Dict:
    token = base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("utf-8")
    payload = {
        "jql": jql,
        "maxResults": max_results,
        "fields": ["*all"],
        "expand": "changelog",
    }
    if next_page_token:
        payload["nextPageToken"] = next_page_token

    body = json.dumps(payload).encode("utf-8")
    url = f"{site}/rest/api/3/search/jql"

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {token}")

    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def build_month_jql(project: str, year: int, month: int) -> str:
    start, end = month_bounds(year, month)
    return (
        f"project = {quote_jql(project)} "
        f"AND ((updated >= {quote_jql(start)} AND updated < {quote_jql(end)}) "
        f"OR (created >= {quote_jql(start)} AND created < {quote_jql(end)}) "
        f"OR (resolved >= {quote_jql(start)} AND resolved < {quote_jql(end)})) "
        f"ORDER BY updated ASC"
    )


def build_updates_jql(project: str, since_iso: str) -> str:
    since_dt = dt.datetime.fromisoformat(since_iso.rstrip("Z"))
    since_jira = since_dt.strftime("%Y-%m-%d %H:%M")
    return (
        f"project = {quote_jql(project)} "
        f"AND updated >= {quote_jql(since_jira)} "
        f"ORDER BY updated ASC"
    )


def load_manifest() -> Dict:
    manifest = read_json(MANIFEST_FILE)
    manifest.setdefault("months", {})
    manifest.setdefault("updates_sweeps", [])
    manifest.setdefault("last_updates_sweep_at", "")
    return manifest


def save_manifest(manifest: Dict) -> None:
    write_json(MANIFEST_FILE, manifest)


def month_fetch_status(manifest: Dict, year: int, month: int) -> str:
    mk = month_key(year, month)
    entry = manifest["months"].get(mk)
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


def mark_month_fetched(manifest: Dict, year: int, month: int, run_id: str, page_count: int, issue_count: int) -> None:
    mk = month_key(year, month)
    now = dt.datetime.utcnow().isoformat() + "Z"
    past = month_is_in_past(year, month)
    manifest["months"][mk] = {
        "status": "complete" if past else "partial",
        "fetched_at": now,
        "month_was_live_at_fetch": not past,
        "active_run_id": run_id,
        "page_count": page_count,
        "issue_count": issue_count,
    }


def month_run_dir(year: int, month: int, run_id: str) -> str:
    return os.path.join(RAW_DIR, month_key(year, month), run_id)


def run_page_file(year: int, month: int, run_id: str, page_index: int) -> str:
    return os.path.join(month_run_dir(year, month, run_id), f"search_page_{page_index:03d}.json")


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"issues-{month_key(year, month)}.json")


def canonical_created_file(mk: str) -> str:
    return os.path.join(CANONICAL_CREATED_DIR, f"issues-{mk}.json")


def updated_index_file(mk: str) -> str:
    return os.path.join(UPDATED_INDEX_DIR, f"updated-{mk}.json")


def issue_key(issue: Dict) -> str:
    return issue.get("key", "")


def issue_created(issue: Dict) -> str:
    return (issue.get("fields") or {}).get("created", "")


def issue_updated(issue: Dict) -> str:
    return (issue.get("fields") or {}).get("updated", "")


def issue_type_name(issue: Dict) -> str:
    fields = issue.get("fields") or {}
    issue_type = fields.get("issuetype") or {}
    name = issue_type.get("name") if isinstance(issue_type, dict) else ""
    return str(name or "").strip()


def normalize_issue_types(issue_types: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for issue_type in issue_types:
        cleaned = str(issue_type).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(cleaned)
    return normalized


def load_vertical_support_filters(config: Dict) -> Tuple[List[str], List[str]]:
    vertical = config.get("vertical_support") if isinstance(config, dict) else {}
    issue_types = []
    tags = []
    if isinstance(vertical, dict):
        issue_types = normalize_issue_types(vertical.get("issue_types") or [])
        tags = normalize_issue_types(vertical.get("tags") or [])
    if not issue_types:
        issue_types = normalize_issue_types(config.get("issue_types") or [])
    return issue_types, tags


def load_issues_from_run(year: int, month: int, run_id: str) -> Tuple[List[Dict], List[str]]:
    run_dir = month_run_dir(year, month, run_id)
    issues: List[Dict] = []
    source_files: List[str] = []

    if not os.path.isdir(run_dir):
        return issues, source_files

    for fname in sorted(os.listdir(run_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(run_dir, fname)
        payload = read_json(fpath)
        source_files.append(fpath)
        issues.extend(payload.get("issues", []))

    return issues, source_files


def issue_updated_month(issue: Dict) -> str:
    updated = issue_updated(issue)
    return updated[:7] if len(updated) >= 7 else ""


def issue_created_month(issue: Dict) -> str:
    created = issue_created(issue)
    return created[:7] if len(created) >= 7 else ""


def ensure_created_cache_seeded() -> None:
    os.makedirs(CANONICAL_CREATED_DIR, exist_ok=True)
    existing_created = [
        name for name in os.listdir(CANONICAL_CREATED_DIR)
        if name.startswith("issues-") and name.endswith(".json")
    ]
    if existing_created:
        return

    legacy_files = [
        name for name in os.listdir(CONSOLIDATED_DIR)
        if name.startswith("issues-") and name.endswith(".json")
    ] if os.path.isdir(CONSOLIDATED_DIR) else []
    if not legacy_files:
        return

    issues_by_key: Dict[str, Dict] = {}
    for name in sorted(legacy_files):
        path = os.path.join(CONSOLIDATED_DIR, name)
        payload = read_json(path)
        for issue in payload.get("issues", []):
            key = issue_key(issue)
            if not key:
                continue
            existing = issues_by_key.get(key)
            if not existing or issue_updated(issue) > issue_updated(existing):
                issues_by_key[key] = issue

    partitions: Dict[str, Dict[str, Dict]] = {}
    for issue in issues_by_key.values():
        mk = issue_created_month(issue)
        if not mk:
            mk = issue_updated_month(issue)
        if not mk:
            continue
        bucket = partitions.setdefault(mk, {})
        bucket[issue_key(issue)] = issue

    for mk, by_key in partitions.items():
        issues = sorted(by_key.values(), key=issue_created)
        write_json(
            canonical_created_file(mk),
            {
                "generated_at": dt.datetime.utcnow().isoformat() + "Z",
                "month": mk,
                "partition": "created",
                "issue_count": len(issues),
                "issues": issues,
            },
        )


def upsert_canonical_created(issues: List[Dict]) -> Dict[str, int]:
    os.makedirs(CANONICAL_CREATED_DIR, exist_ok=True)
    touched_counts: Dict[str, int] = {}
    grouped: Dict[str, List[Dict]] = {}
    for issue in issues:
        mk = issue_created_month(issue)
        if not mk:
            mk = issue_updated_month(issue)
        if not mk:
            continue
        grouped.setdefault(mk, []).append(issue)

    for mk, month_issues in grouped.items():
        path = canonical_created_file(mk)
        payload = read_json(path) if os.path.exists(path) else {}
        by_key: Dict[str, Dict] = {}
        for issue in payload.get("issues", []):
            key = issue_key(issue)
            if key:
                by_key[key] = issue

        changed = 0
        for issue in month_issues:
            key = issue_key(issue)
            if not key:
                continue
            existing = by_key.get(key)
            if not existing or issue_updated(issue) >= issue_updated(existing):
                by_key[key] = issue
                changed += 1

        merged = sorted(by_key.values(), key=issue_created)
        write_json(
            path,
            {
                "generated_at": dt.datetime.utcnow().isoformat() + "Z",
                "month": mk,
                "partition": "created",
                "issue_count": len(merged),
                "issues": merged,
            },
        )
        touched_counts[mk] = changed

    return touched_counts


def write_updated_index(year: int, month: int, issues: List[Dict], run_id: str, source_files: List[str]) -> None:
    mk = month_key(year, month)
    os.makedirs(UPDATED_INDEX_DIR, exist_ok=True)

    by_key: Dict[str, Dict] = {}
    for issue in issues:
        key = issue_key(issue)
        if not key:
            continue
        existing = by_key.get(key)
        if not existing or issue_updated(issue) > issue_updated(existing):
            by_key[key] = issue

    entries = []
    for key in sorted(by_key.keys()):
        issue = by_key[key]
        entries.append(
            {
                "key": key,
                "created": issue_created(issue),
                "updated": issue_updated(issue),
                "resolutiondate": (issue.get("fields") or {}).get("resolutiondate", ""),
            }
        )

    write_json(
        updated_index_file(mk),
        {
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
            "month": mk,
            "partition": "updated_index",
            "active_run_id": run_id,
            "issue_count": len(entries),
            "source_files": source_files,
            "issues": entries,
        },
    )


def consolidate_month(year: int, month: int) -> int:
    mk = month_key(year, month)
    manifest = load_manifest()
    month_entry = manifest.get("months", {}).get(mk, {})
    run_id = month_entry.get("active_run_id", "")

    if not run_id:
        month_root = os.path.join(RAW_DIR, mk)
        if os.path.isdir(month_root):
            runs = sorted([x for x in os.listdir(month_root) if os.path.isdir(os.path.join(month_root, x))])
            if runs:
                run_id = runs[-1]

    issues_by_key: Dict[str, Dict] = {}
    source_files: List[str] = []

    if run_id:
        run_issues, run_sources = load_issues_from_run(year, month, run_id)
        source_files.extend(run_sources)
        for issue in run_issues:
            key = issue_key(issue)
            if not key:
                continue
            existing = issues_by_key.get(key)
            if not existing or issue_updated(issue) > issue_updated(existing):
                issues_by_key[key] = issue

    if os.path.isdir(UPDATES_DIR):
        for fname in sorted(os.listdir(UPDATES_DIR)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(UPDATES_DIR, fname)
            update_payload = read_json(fpath)
            pages = update_payload.get("pages", [])
            for page in pages:
                for issue in page.get("issues", []):
                    if issue_updated_month(issue) != mk:
                        continue
                    key = issue_key(issue)
                    if not key:
                        continue
                    existing = issues_by_key.get(key)
                    if not existing or issue_updated(issue) > issue_updated(existing):
                        issues_by_key[key] = issue
                        if fpath not in source_files:
                            source_files.append(fpath)

    issues = sorted(issues_by_key.values(), key=issue_updated)

    ensure_created_cache_seeded()
    upsert_canonical_created(issues)
    write_updated_index(year, month, issues, run_id, source_files)
    return len(issues)


def fetch_month(
    site: str,
    email: str,
    api_token: str,
    project: str,
    year: int,
    month: int,
    force: bool,
) -> None:
    manifest = load_manifest()
    mk = month_key(year, month)
    status = month_fetch_status(manifest, year, month)

    if status == "complete" and not force:
        print(f"Month {mk} already complete. Use --force to re-fetch.")
        return

    if status == "stale":
        print(f"Month {mk} cache is stale. Refreshing...")
    elif status == "complete" and force:
        print(f"Month {mk} marked complete but --force specified. Refreshing...")
    else:
        print(f"Fetching month {mk}...")

    jql = build_month_jql(project, year, month)
    run_id = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(month_run_dir(year, month, run_id), exist_ok=True)

    page = 0
    next_page_token = None
    issue_total = 0

    while True:
        payload = jira_search(site, email, api_token, jql, max_results=DEFAULT_PAGE_SIZE, next_page_token=next_page_token)
        page += 1
        write_json(run_page_file(year, month, run_id, page), payload)

        issues = payload.get("issues", [])
        issue_total += len(issues)

        if payload.get("isLast", False):
            break

        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

        time.sleep(REQUEST_DELAY_SECONDS)

    mark_month_fetched(manifest, year, month, run_id, page_count=page, issue_count=issue_total)
    save_manifest(manifest)

    issue_count = consolidate_month(year, month)
    print(f"Fetched {issue_total} ticket(s) across {page} page(s).")
    print(f"Updated index: {issue_count} issue(s) -> {updated_index_file(month_key(year, month))}")


def check_updates(site: str, email: str, api_token: str, project: str) -> None:
    manifest = load_manifest()
    since = manifest.get("last_updates_sweep_at", "")

    if not since:
        since = (dt.datetime.utcnow() - dt.timedelta(days=30)).isoformat() + "Z"
        print(f"No previous Jira update sweep found. Sweeping back to {since}")
    else:
        print(f"Sweeping Jira updates since {since}")

    jql = build_updates_jql(project, since)
    pages: List[Dict] = []

    next_page_token = None
    issue_total = 0

    while True:
        payload = jira_search(site, email, api_token, jql, max_results=DEFAULT_PAGE_SIZE, next_page_token=next_page_token)
        pages.append(payload)

        issues = payload.get("issues", [])
        issue_total += len(issues)

        if payload.get("isLast", False):
            break

        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

        time.sleep(REQUEST_DELAY_SECONDS)

    swept_at = dt.datetime.utcnow().isoformat() + "Z"
    updates_file = os.path.join(UPDATES_DIR, dt.datetime.utcnow().strftime("%Y-%m-%dT%H%M%S") + ".json")
    os.makedirs(UPDATES_DIR, exist_ok=True)
    write_json(
        updates_file,
        {
            "swept_at": swept_at,
            "since": since,
            "jql": jql,
            "page_count": len(pages),
            "issue_count": issue_total,
            "pages": pages,
        },
    )

    manifest["last_updates_sweep_at"] = swept_at
    manifest["updates_sweeps"].append(
        {
            "swept_at": swept_at,
            "since": since,
            "issue_count": issue_total,
            "file": updates_file,
        }
    )
    save_manifest(manifest)

    print(f"Update sweep complete: {issue_total} updated ticket(s)")
    print(f"Saved raw updates file: {updates_file}")

    affected_months = set()
    for page in pages:
        for issue in page.get("issues", []):
            mk = issue_updated_month(issue)
            if mk:
                affected_months.add(mk)

    if affected_months:
        print(f"Regenerating updated indexes: {', '.join(sorted(affected_months))}")
        for mk in sorted(affected_months):
            y, m = int(mk[:4]), int(mk[5:7])
            issue_count = consolidate_month(y, m)
            print(f"  {mk}: {issue_count} issue(s) -> {updated_index_file(mk)}")


def show_status() -> None:
    manifest = load_manifest()
    months = manifest.get("months", {})
    if not months:
        print("No Jira cached months found.")
        return

    for mk in sorted(months.keys()):
        entry = months[mk]
        c_file = canonical_created_file(mk)
        canonical = "yes" if os.path.exists(c_file) else "no"
        u_file = updated_index_file(mk)
        updated_index = "yes" if os.path.exists(u_file) else "no"
        print(
            f"  {mk}  status: {entry.get('status', 'unknown')}  "
            f"issues: {entry.get('issue_count', 0)}  pages: {entry.get('page_count', 0)}  "
            f"canonical_created: {canonical}  updated_index: {updated_index}"
        )

    print(f"\nLast Jira update sweep: {manifest.get('last_updates_sweep_at', 'none')}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Fetch Jira tickets and derive monthly issue files.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cache is marked complete.")
    parser.add_argument("--check-updates", action="store_true", help="Sweep for tickets updated since last sweep.")
    parser.add_argument("--consolidate-only", action="store_true", help="Rebuild derived files without fetching.")
    parser.add_argument("--status", action="store_true", help="Show cache coverage and exit.")
    parser.add_argument("--config-file", default=CONFIG_FILE, help=f"Path to Jira minimal config (default: {CONFIG_FILE})")
    args = parser.parse_args(argv)

    if args.status:
        show_status()
        return

    today = dt.date.today()
    year, month = parse_month(args.month) if args.month else (today.year, today.month)

    if args.consolidate_only:
        issue_count = consolidate_month(year, month)
        print(f"Updated index: {issue_count} issue(s) -> {updated_index_file(month_key(year, month))}")
        return

    site, email, api_token, project, _ = load_jira_runtime(args.config_file)
    config = load_jira_config(args.config_file)
    issue_types, tags = load_vertical_support_filters(config)

    print(
        f"Jira ticket fetch  |  month: {month_key(year, month)}  |  "
        f"project: {project}  |  fetch_scope: all project issues"
    )
    print(
        "Vertical Support filter config  |  "
        f"issue_types: {', '.join(issue_types) if issue_types else 'none'}  |  "
        f"tags: {', '.join(tags) if tags else 'none'}"
    )

    if args.check_updates:
        check_updates(site, email, api_token, project)
        return

    fetch_month(site, email, api_token, project, year, month, force=args.force)


if __name__ == "__main__":
    main()
