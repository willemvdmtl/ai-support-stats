#!/usr/bin/env python3
"""Generate a monthly markdown report with configurable Jira flow metrics."""

import argparse
import datetime as dt
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

from common.setup_utils import read_json

REPORTS_DIR = "reports"
GITHUB_CACHE_DIR = "cache/github"
JIRA_CACHE_DIR = "cache/jira"
JIRA_CANONICAL_CREATED_DIR = "cache/jira/by-created"
JIRA_CONFIG_FILE = "config/jira-minimal.json"
GITHUB_CONFIG_FILE = "config/github-minimal.json"
METRIC_GROUPS_CONFIG_FILE = "config/jira-metric-groups.json"
INTERNAL_TEAM_FILE = "config/github-internal-team.json"
OUTLIER_IQR_MULTIPLIER = 3.0


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
        return parsed.year, parsed.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def _normalize_values(values: List[str]) -> set:
    normalized = set()
    for raw in values or []:
        cleaned = str(raw).strip().lower()
        if cleaned:
            normalized.add(cleaned)
    return normalized


def _extract_vertical_support_filters_from_jira_minimal() -> Tuple[set, set]:
    data = read_json(JIRA_CONFIG_FILE)
    vertical = data.get("vertical_support") if isinstance(data, dict) else {}
    issue_types = []
    tags = []
    if isinstance(vertical, dict):
        issue_types = vertical.get("issue_types") or []
        tags = vertical.get("tags") or []
    if not issue_types and isinstance(data, dict):
        issue_types = data.get("issue_types") or []
    return _normalize_values(issue_types), _normalize_values(tags)


def _fallback_metric_groups_policy() -> Dict[str, object]:
    issue_types, tags = _extract_vertical_support_filters_from_jira_minimal()
    return {
        "version": 1,
        "defaults": {
            "event_anchor": "resolved_at",
            "window_policy": {
                "mode": "auto",
                "rolling_days_if_month_data_lt_days": 14,
                "month_window_type_if_ready": "month_to_date",
            },
            "validity": {
                "require_created": True,
                "require_resolved": True,
                "exclude_negative_durations": True,
            },
        },
        "groups": [
            {
                "id": "vertical_support",
                "label": "Vertical Support",
                "filter": {
                    "include_any": {
                        "issue_types": sorted(issue_types),
                        "labels": sorted(tags),
                    }
                },
            },
            {
                "id": "ktlo",
                "label": "KTLO",
                "filter": {
                    "exclude_any": {
                        "issue_types": sorted(issue_types),
                        "labels": [],
                    },
                    "force_include_any": {
                        "issue_types": [],
                        "labels": ["ktlo"],
                    },
                },
            },
        ],
    }


def _normalize_filter(raw_filter: object) -> Dict[str, set]:
    if not isinstance(raw_filter, dict):
        return {"issue_types": set(), "labels": set()}
    return {
        "issue_types": _normalize_values(raw_filter.get("issue_types") or []),
        "labels": _normalize_values(raw_filter.get("labels") or []),
    }


def _normalize_window_policy(raw: object, default_raw: Dict[str, object]) -> Dict[str, object]:
    policy = dict(default_raw)
    if isinstance(raw, dict):
        policy.update(raw)

    mode = str(policy.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "month_to_date", "full_month", "rolling"}:
        mode = "auto"
    rolling_days = int(policy.get("rolling_days_if_month_data_lt_days") or 14)
    if rolling_days < 1:
        rolling_days = 14
    month_when_ready = str(policy.get("month_window_type_if_ready") or "month_to_date").strip().lower()
    if month_when_ready not in {"month_to_date", "full_month"}:
        month_when_ready = "month_to_date"

    return {
        "mode": mode,
        "rolling_days_if_month_data_lt_days": rolling_days,
        "month_window_type_if_ready": month_when_ready,
    }


def load_metric_groups_policy() -> Dict[str, object]:
    if os.path.exists(METRIC_GROUPS_CONFIG_FILE):
        raw = read_json(METRIC_GROUPS_CONFIG_FILE)
    else:
        raw = _fallback_metric_groups_policy()

    if not isinstance(raw, dict):
        raw = _fallback_metric_groups_policy()

    defaults_raw = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    event_anchor = str(defaults_raw.get("event_anchor") or "resolved_at").strip().lower()
    if event_anchor not in {"resolved_at"}:
        event_anchor = "resolved_at"

    validity_raw = defaults_raw.get("validity") if isinstance(defaults_raw.get("validity"), dict) else {}
    validity = {
        "require_created": bool(validity_raw.get("require_created", True)),
        "require_resolved": bool(validity_raw.get("require_resolved", True)),
        "exclude_negative_durations": bool(validity_raw.get("exclude_negative_durations", True)),
    }

    default_window = _normalize_window_policy(defaults_raw.get("window_policy"), {
        "mode": "auto",
        "rolling_days_if_month_data_lt_days": 14,
        "month_window_type_if_ready": "month_to_date",
    })

    groups: List[Dict[str, object]] = []
    for idx, group in enumerate(raw.get("groups") or []):
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "").strip() or f"Group {idx + 1}"
        group_filter = group.get("filter") if isinstance(group.get("filter"), dict) else {}
        include_any = _normalize_filter(group_filter.get("include_any"))
        exclude_any = _normalize_filter(group_filter.get("exclude_any"))
        force_include_any = _normalize_filter(group_filter.get("force_include_any"))
        window_policy = _normalize_window_policy(group.get("window_policy"), default_window)
        groups.append(
            {
                "id": str(group.get("id") or f"group_{idx + 1}").strip(),
                "label": label,
                "include_any": include_any,
                "exclude_any": exclude_any,
                "force_include_any": force_include_any,
                "window_policy": window_policy,
            }
        )

    if not groups:
        fallback = _fallback_metric_groups_policy()
        groups = []
        for idx, group in enumerate(fallback.get("groups") or []):
            if not isinstance(group, dict):
                continue
            group_filter = group.get("filter") if isinstance(group.get("filter"), dict) else {}
            groups.append(
                {
                    "id": str(group.get("id") or f"group_{idx + 1}").strip(),
                    "label": str(group.get("label") or f"Group {idx + 1}").strip(),
                    "include_any": _normalize_filter(group_filter.get("include_any")),
                    "exclude_any": _normalize_filter(group_filter.get("exclude_any")),
                    "force_include_any": _normalize_filter(group_filter.get("force_include_any")),
                    "window_policy": _normalize_window_policy(group.get("window_policy"), default_window),
                }
            )

    return {
        "event_anchor": event_anchor,
        "validity": validity,
        "groups": groups,
    }


def ticket_issue_type(ticket: Dict) -> str:
    fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
    issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    return str(issue_type.get("name") or "").strip()


def ticket_labels(ticket: Dict) -> set:
    fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
    labels = fields.get("labels") if isinstance(fields.get("labels"), list) else []
    return {str(label).strip().lower() for label in labels if str(label).strip()}


def ticket_resolution_name(ticket: Dict) -> str:
    fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
    resolution = fields.get("resolution") if isinstance(fields.get("resolution"), dict) else {}
    return str(resolution.get("name") or "").strip()


def normalized_resolution_name(ticket: Dict) -> str:
    normalized = ticket_resolution_name(ticket).strip().lower().replace("'", "")
    return " ".join(normalized.split())


def ticket_matches_filter(ticket: Dict, filter_spec: Dict[str, set]) -> bool:
    issue_types = filter_spec.get("issue_types") or set()
    labels = filter_spec.get("labels") or set()
    by_type = bool(issue_types) and ticket_issue_type(ticket).lower() in issue_types
    by_label = bool(labels) and bool(ticket_labels(ticket) & labels)
    return by_type or by_label


def filter_is_empty(filter_spec: Dict[str, set]) -> bool:
    return not filter_spec.get("issue_types") and not filter_spec.get("labels")


def chart_files(year: int, month: int) -> Dict[str, str]:
    return {
        "github_heatmap": os.path.join(REPORTS_DIR, f"github_pr_heatmap_{year}_{month:02d}.png"),
        "github_split": os.path.join(REPORTS_DIR, f"github_pr_internal_external_{year}_{month:02d}.png"),
        "jira_service": os.path.join(REPORTS_DIR, f"jira_service_heatmap_{year}_{month:02d}.png"),
        "jira_team": os.path.join(REPORTS_DIR, f"jira_requesting_team_heatmap_{year}_{month:02d}.png"),
        "jira_flow_trend_vs": os.path.join(REPORTS_DIR, f"jira_flow_trend_vs_{year}_{month:02d}.png"),
        "jira_flow_trend_vs_individual": os.path.join(REPORTS_DIR, f"jira_flow_trend_vs_individual_{year}_{month:02d}.png"),
        "jira_flow_trend_ktlo": os.path.join(REPORTS_DIR, f"jira_flow_trend_ktlo_{year}_{month:02d}.png"),
    }


def consolidated_github_file(year: int, month: int) -> str:
    return os.path.join(GITHUB_CACHE_DIR, f"prs-{year}-{month:02d}.json")


def load_pr_date_anchor() -> str:
    data = read_json(GITHUB_CONFIG_FILE) if os.path.exists(GITHUB_CONFIG_FILE) else {}
    raw = str(
        (data.get("pr_date_anchor") if isinstance(data, dict) else "")
        or (data.get("pr_event_anchor") if isinstance(data, dict) else "")
        or "created"
    ).strip().lower()
    if raw in {"closed", "closed_at", "close"}:
        return "closed"
    return "created"


def pr_anchor_field(date_anchor: str) -> str:
    return "closed_at" if date_anchor == "closed" else "created_at"


def load_prs(year: int, month: int, date_anchor: str = "created") -> List[Dict]:
    path = consolidated_github_file(year, month)
    if not os.path.exists(path):
        print(f"ERROR: No consolidated data found at {path}")
        print(f"Run first:  python3 scripts/fetch-data.py github --month {year}-{month:02d}")
        sys.exit(1)
    data = read_json(path)
    prs = data.get("pull_requests", [])
    target_month = f"{year}-{month:02d}"
    anchor_field = pr_anchor_field(date_anchor)
    return [pr for pr in prs if str(pr.get(anchor_field) or "").startswith(target_month)]


def load_internal_teams() -> Dict[str, List[str]]:
    data = read_json(INTERNAL_TEAM_FILE)
    if not data:
        legacy = "data/config/github-internal-team.json"
        if os.path.exists(legacy):
            data = read_json(legacy)

    teams_payload = data.get("internal_teams") if isinstance(data, dict) else None
    normalized: Dict[str, List[str]] = {}

    if isinstance(teams_payload, dict):
        for team_name, raw_users in teams_payload.items():
            users = []
            seen = set()
            if not isinstance(raw_users, list):
                continue
            for user in raw_users:
                login = str(user).strip()
                if not login:
                    continue
                key = login.lower()
                if key in seen:
                    continue
                users.append(login)
                seen.add(key)
            if users:
                normalized[str(team_name).strip() or "Internal"] = users

    if normalized:
        return normalized

    fallback_users = []
    if isinstance(data, dict):
        fallback_users = data.get("internal_github_users", [])
    users = []
    seen = set()
    for user in fallback_users:
        login = str(user).strip()
        if not login:
            continue
        key = login.lower()
        if key in seen:
            continue
        users.append(login)
        seen.add(key)
    return {"Internal": users} if users else {}


def pr_author_login(pr: Dict) -> str:
    user = pr.get("user")
    if isinstance(user, dict):
        return user.get("login", "")
    return pr.get("login", "")


def classify_pr_split(prs: List[Dict], internal_teams: Dict[str, List[str]]) -> Dict[str, object]:
    login_to_team: Dict[str, str] = {}
    for team_name, users in internal_teams.items():
        for user in users:
            key = user.lower()
            if key in login_to_team:
                continue
            login_to_team[key] = team_name

    team_counts: Dict[str, int] = {name: 0 for name in internal_teams.keys()}
    external_count = 0
    unknown_count = 0

    for pr in prs:
        login = pr_author_login(pr).lower()
        if not login:
            unknown_count += 1
        elif login in login_to_team:
            team_counts[login_to_team[login]] += 1
        else:
            external_count += 1

    internal_count = sum(team_counts.values())
    total = internal_count + external_count + unknown_count
    return {
        "team_counts": team_counts,
        "internal_count": internal_count,
        "external_count": external_count,
        "unknown_count": unknown_count,
        "total": total,
    }


def parse_jira_datetime(raw: str) -> Optional[dt.datetime]:
    if not raw:
        return None

    # Jira commonly returns timestamps like 2026-04-02T12:41:10.032+0100.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue

    return None


def month_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    target_month_start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        target_month_end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        target_month_end = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)

    return target_month_start, target_month_end


def month_to_date_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    month_start, month_end = month_window(year, month)
    now_utc = dt.datetime.now(dt.timezone.utc)
    return month_start, min(now_utc, month_end)


def rolling_window(year: int, month: int, days: int) -> Tuple[dt.datetime, dt.datetime]:
    _, month_end = month_window(year, month)
    today_utc = dt.datetime.now(dt.timezone.utc)
    window_end = min(today_utc, month_end)
    window_start = window_end - dt.timedelta(days=days)
    return window_start, window_end


def resolve_window(year: int, month: int, policy: Dict[str, object]) -> Tuple[Tuple[dt.datetime, dt.datetime], str]:
    mode = str(policy.get("mode") or "auto")
    rolling_days = int(policy.get("rolling_days_if_month_data_lt_days") or 14)
    month_when_ready = str(policy.get("month_window_type_if_ready") or "month_to_date")

    def rolling_suffix(days: int) -> str:
        if days == 14:
            return " (2-week rolling)"
        return f" ({days}-day rolling)"

    if mode == "rolling":
        return rolling_window(year, month, rolling_days), rolling_suffix(rolling_days)
    if mode == "full_month":
        return month_window(year, month), ""
    if mode == "month_to_date":
        return month_to_date_window(year, month), ""

    window_start, window_end = month_to_date_window(year, month)
    if window_end <= window_start:
        return (window_start, window_end), ""

    elapsed_days = (window_end - window_start).total_seconds() / 86400.0
    if elapsed_days < float(rolling_days):
        return rolling_window(year, month, rolling_days), rolling_suffix(rolling_days)
    if month_when_ready == "full_month":
        return month_window(year, month), ""
    return (window_start, window_end), ""


def all_jira_tickets() -> List[Dict]:
    scan_dir = JIRA_CANONICAL_CREATED_DIR if os.path.exists(JIRA_CANONICAL_CREATED_DIR) else JIRA_CACHE_DIR
    if not os.path.exists(scan_dir):
        return []

    tickets_by_key: Dict[str, Dict] = {}
    for name in sorted(os.listdir(scan_dir)):
        if not name.startswith("issues-") or not name.endswith(".json"):
            continue
        path = os.path.join(scan_dir, name)
        payload = read_json(path)
        for ticket in payload.get("issues", []):
            key = str(ticket.get("key") or "")
            if not key:
                continue
            tickets_by_key[key] = ticket

    return list(tickets_by_key.values())


def github_pr_split_stats(year: int, month: int) -> Optional[Dict[str, object]]:
    date_anchor = load_pr_date_anchor()
    try:
        prs = load_prs(year, month, date_anchor=date_anchor)
    except SystemExit:
        return None

    internal_teams = load_internal_teams()
    if not internal_teams:
        return None

    split = classify_pr_split(prs, internal_teams)
    total = int(split.get("total") or 0)
    if total == 0:
        return None

    internal_count = int(split.get("internal_count") or 0)
    external_count = int(split.get("external_count") or 0)
    unknown_count = int(split.get("unknown_count") or 0)
    return {
        "internal_count": internal_count,
        "external_count": external_count,
        "unknown_count": unknown_count,
        "total": total,
        "internal_pct": internal_count / total * 100,
        "external_pct": external_count / total * 100,
    }


def select_group_tickets(all_tickets: List[Dict], group: Dict[str, object]) -> List[Dict]:
    include_any = group.get("include_any") if isinstance(group.get("include_any"), dict) else {"issue_types": set(), "labels": set()}
    exclude_any = group.get("exclude_any") if isinstance(group.get("exclude_any"), dict) else {"issue_types": set(), "labels": set()}
    force_include_any = group.get("force_include_any") if isinstance(group.get("force_include_any"), dict) else {"issue_types": set(), "labels": set()}

    selected: Dict[str, Dict] = {}

    for ticket in all_tickets:
        if not filter_is_empty(include_any) and not ticket_matches_filter(ticket, include_any):
            continue
        if not filter_is_empty(exclude_any) and ticket_matches_filter(ticket, exclude_any):
            continue
        key = str(ticket.get("key") or "")
        if key:
            selected[key] = ticket

    if not filter_is_empty(force_include_any):
        for ticket in all_tickets:
            if not ticket_matches_filter(ticket, force_include_any):
                continue
            key = str(ticket.get("key") or "")
            if key:
                selected[key] = ticket

    return list(selected.values())


def jira_lead_stats_in_window(
    tickets: List[Dict],
    window_start: dt.datetime,
    window_end: dt.datetime,
    validity: Dict[str, bool],
) -> Tuple[List[float], Optional[Dict[str, object]]]:
    durations_days: List[float] = []
    longest_wait: Optional[Dict[str, object]] = None

    for ticket in tickets:
        fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
        created = parse_jira_datetime(str(fields.get("created") or ""))
        if created is None and validity.get("require_created", True):
            continue
        if created is None:
            continue

        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if resolution is None and validity.get("require_resolved", True):
            continue
        if resolution is None:
            continue

        resolution_utc = resolution.astimezone(dt.timezone.utc)
        if not (window_start <= resolution_utc < window_end):
            continue

        delta = (resolution - created).total_seconds()
        if delta < 0 and validity.get("exclude_negative_durations", True):
            continue

        wait_days = delta / 86400.0
        durations_days.append(wait_days)

        if longest_wait is None or wait_days > float(longest_wait["days"]):
            longest_wait = {
                "key": ticket.get("key", ""),
                "days": wait_days,
                "status": "resolved",
            }

    return durations_days, longest_wait


def is_cycle_start_status(status_name: str) -> bool:
    name = status_name.strip().lower()
    if not name:
        return False
    if name in {"in progress", "in development", "development in progress", "doing"}:
        return True
    return "in progress" in name


def is_cycle_fallback_review_status(status_name: str) -> bool:
    return status_name.strip().lower() == "in review"


def first_in_progress_transition_at(ticket: Dict) -> Optional[dt.datetime]:
    changelog = ticket.get("changelog") if isinstance(ticket.get("changelog"), dict) else {}
    histories = changelog.get("histories") if isinstance(changelog.get("histories"), list) else []

    transition_times: List[dt.datetime] = []
    fallback_review_times: List[dt.datetime] = []
    for history in histories:
        if not isinstance(history, dict):
            continue

        changed_at = parse_jira_datetime(str(history.get("created") or ""))
        if changed_at is None:
            continue

        items = history.get("items") if isinstance(history.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("field") or "").strip().lower() != "status":
                continue

            to_status = str(item.get("toString") or "")
            if is_cycle_start_status(to_status):
                transition_times.append(changed_at)
                break
            if is_cycle_fallback_review_status(to_status):
                fallback_review_times.append(changed_at)
                break

    if transition_times:
        return min(transition_times)

    if fallback_review_times:
        return min(fallback_review_times)

    return None


def jira_cycle_stats_in_window(
    tickets: List[Dict],
    window_start: dt.datetime,
    window_end: dt.datetime,
    validity: Dict[str, bool],
) -> Tuple[List[float], Optional[Dict[str, object]]]:
    durations_days: List[float] = []
    longest_wait: Optional[Dict[str, object]] = None

    for ticket in tickets:
        fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if resolution is None and validity.get("require_resolved", True):
            continue
        if resolution is None:
            continue

        resolution_utc = resolution.astimezone(dt.timezone.utc)
        if not (window_start <= resolution_utc < window_end):
            continue

        cycle_start = first_in_progress_transition_at(ticket)
        if cycle_start is None:
            continue

        delta = (resolution - cycle_start).total_seconds()
        if delta < 0 and validity.get("exclude_negative_durations", True):
            continue

        cycle_days = delta / 86400.0
        durations_days.append(cycle_days)

        if longest_wait is None or cycle_days > float(longest_wait["days"]):
            longest_wait = {
                "key": ticket.get("key", ""),
                "days": cycle_days,
                "status": "resolved",
            }

    return durations_days, longest_wait


def is_pr_like_ticket(ticket: Dict) -> bool:
    issue_type = ticket_issue_type(ticket).strip().lower()
    if issue_type in {"pr request", "pull request", "pr"}:
        return True
    return issue_type.startswith("pr ") or issue_type.endswith(" pr")


def resolved_tickets_in_window(tickets: List[Dict], window_start: dt.datetime, window_end: dt.datetime) -> List[Dict]:
    selected: List[Dict] = []
    for ticket in tickets:
        fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if resolution is None:
            continue
        resolution_utc = resolution.astimezone(dt.timezone.utc)
        if window_start <= resolution_utc < window_end:
            selected.append(ticket)
    return selected


def image_block(path: str, alt_text: str, report_dir: str) -> str:
    if os.path.exists(path):
        rel_path = os.path.relpath(path, start=report_dir or ".").replace(os.sep, "/")
        return f"![{alt_text}]({rel_path})"
    return f"_Not available: {path}_"


def markdown_table_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").rstrip()


def format_metric_value(durations: List[float], suffix: str) -> str:
    if not durations:
        return "unavailable"
    avg_days = sum(durations) / len(durations)
    unit = "ticket" if len(durations) == 1 else "tickets"
    return f"{avg_days:.2f} days{suffix} ({len(durations)} {unit})"


def format_metric_value_plain(durations: List[float], suffix: str) -> str:
    if not durations:
        return "unavailable"
    avg_days = sum(durations) / len(durations)
    return f"{avg_days:.2f} days{suffix}"


def percentile(sorted_values: List[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    rank = (len(sorted_values) - 1) * (percent / 100.0)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))

    if lower_idx == upper_idx:
        return float(sorted_values[lower_idx])

    weight = rank - lower_idx
    lower_val = float(sorted_values[lower_idx])
    upper_val = float(sorted_values[upper_idx])
    return lower_val + (upper_val - lower_val) * weight


def filter_iqr_outliers(values: List[float]) -> Tuple[List[float], int]:
    if len(values) < 4:
        return list(values), 0

    ordered = sorted(float(v) for v in values)
    q1 = percentile(ordered, 25)
    q3 = percentile(ordered, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return list(values), 0

    upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
    filtered = [float(v) for v in values if float(v) <= upper]
    outlier_count = len(values) - len(filtered)
    if outlier_count <= 0:
        return list(values), 0
    if not filtered:
        return list(values), 0

    return filtered, outlier_count


def append_metric_rows(
    rows: List[Dict[str, str]],
    type_label: str,
    lead_durations: List[float],
    cycle_durations: List[float],
    suffix: str,
    level: int,
    emphasize: bool = False,
) -> None:
    if level <= 0:
        type_cell = f"**{type_label}**" if emphasize else type_label
    elif level == 1:
        type_cell = f"• {type_label}"
    else:
        type_cell = f"• {type_label}"

    lead_display = format_metric_value(lead_durations, suffix)
    cycle_display = format_metric_value(cycle_durations, suffix)
    if emphasize:
        lead_display = f"**{lead_display}**"
        cycle_display = f"**{cycle_display}**"

    rows.append({
        "type": type_cell,
        "lead": lead_display,
        "cycle": cycle_display,
    })


def build_report(year: int, month: int, report_dir: str) -> str:
    month_label = dt.date(year, month, 1).strftime("%B %Y")
    charts = chart_files(year, month)
    pr_split = github_pr_split_stats(year, month)

    all_tickets = all_jira_tickets()
    policy = load_metric_groups_policy()
    validity = policy.get("validity") if isinstance(policy.get("validity"), dict) else {}
    jira_flow_trend_vs_individual_chart = charts["jira_flow_trend_vs_individual"]

    metric_sections: List[Dict[str, object]] = []
    longest_lead: Optional[Dict[str, object]] = None
    longest_cycle: Optional[Dict[str, object]] = None

    groups = policy.get("groups") if isinstance(policy.get("groups"), list) else []
    for idx, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "").strip().lower()
        label = str(group.get("label") or "Group")
        group_tickets = select_group_tickets(all_tickets, group)

        if group_id == "ktlo" or label.strip().lower() == "ktlo":
            excluded_resolutions = {"wont do", "duplicate"}
            group_tickets = [
                ticket for ticket in group_tickets
                if normalized_resolution_name(ticket) not in excluded_resolutions
                and ticket_issue_type(ticket).strip().lower() != "epic"
            ]

        window_policy = group.get("window_policy") if isinstance(group.get("window_policy"), dict) else {}
        (window_start, window_end), suffix = resolve_window(year, month, window_policy)
        lead_durations, longest_group_lead = jira_lead_stats_in_window(group_tickets, window_start, window_end, validity)
        cycle_durations, longest_group_cycle = jira_cycle_stats_in_window(group_tickets, window_start, window_end, validity)

        section_rows: List[Dict[str, str]] = []
        filtered_section_rows: List[Dict[str, str]] = []
        append_metric_rows(section_rows, label, lead_durations, cycle_durations, suffix, level=0, emphasize=True)
        filtered_lead_durations, _ = filter_iqr_outliers(lead_durations)
        filtered_cycle_durations, _ = filter_iqr_outliers(cycle_durations)
        append_metric_rows(filtered_section_rows, label, filtered_lead_durations, filtered_cycle_durations, suffix, level=0, emphasize=True)

        if idx == 0:
            longest_lead = longest_group_lead
            longest_cycle = longest_group_cycle

        if group_id == "vertical_support" or label.strip().lower() == "vertical support":
            pr_tickets = [ticket for ticket in group_tickets if is_pr_like_ticket(ticket)]
            other_tickets = [ticket for ticket in group_tickets if not is_pr_like_ticket(ticket)]

            pr_lead_durations, _ = jira_lead_stats_in_window(pr_tickets, window_start, window_end, validity)
            pr_cycle_durations, _ = jira_cycle_stats_in_window(pr_tickets, window_start, window_end, validity)
            other_lead_durations, _ = jira_lead_stats_in_window(other_tickets, window_start, window_end, validity)
            other_cycle_durations, _ = jira_cycle_stats_in_window(other_tickets, window_start, window_end, validity)

            append_metric_rows(section_rows, "PR Tickets", pr_lead_durations, pr_cycle_durations, suffix, level=1)
            append_metric_rows(section_rows, "Other Tickets", other_lead_durations, other_cycle_durations, suffix, level=1)

            filtered_pr_lead_durations, _ = filter_iqr_outliers(pr_lead_durations)
            filtered_pr_cycle_durations, _ = filter_iqr_outliers(pr_cycle_durations)
            filtered_other_lead_durations, _ = filter_iqr_outliers(other_lead_durations)
            filtered_other_cycle_durations, _ = filter_iqr_outliers(other_cycle_durations)

            append_metric_rows(filtered_section_rows, "PR Tickets", filtered_pr_lead_durations, filtered_pr_cycle_durations, suffix, level=1)
            append_metric_rows(filtered_section_rows, "Other Tickets", filtered_other_lead_durations, filtered_other_cycle_durations, suffix, level=1)

        if group_id == "ktlo" or label.strip().lower() == "ktlo":
            issue_type_buckets: Dict[str, List[Dict]] = {}
            for ticket in group_tickets:
                issue_type = ticket_issue_type(ticket) or "Unspecified"
                issue_type_buckets.setdefault(issue_type, []).append(ticket)

            ranked_issue_types = sorted(
                issue_type_buckets.items(),
                key=lambda item: (-len(jira_lead_stats_in_window(item[1], window_start, window_end, validity)[0]), item[0].lower()),
            )

            for issue_type, issue_type_tickets in ranked_issue_types:
                type_lead_durations, _ = jira_lead_stats_in_window(issue_type_tickets, window_start, window_end, validity)
                type_cycle_durations, _ = jira_cycle_stats_in_window(issue_type_tickets, window_start, window_end, validity)
                if not type_lead_durations and not type_cycle_durations:
                    continue
                append_metric_rows(section_rows, issue_type, type_lead_durations, type_cycle_durations, suffix, level=1)

                filtered_type_lead_durations, _ = filter_iqr_outliers(type_lead_durations)
                filtered_type_cycle_durations, _ = filter_iqr_outliers(type_cycle_durations)
                append_metric_rows(filtered_section_rows, issue_type, filtered_type_lead_durations, filtered_type_cycle_durations, suffix, level=1)

        metric_sections.append({
            "id": group_id,
            "label": label,
            "rows": section_rows,
            "filtered_rows": filtered_section_rows,
        })

    if not metric_sections:
        metric_sections.append({
            "id": "",
            "label": "No metric groups configured",
            "rows": [{
                "type": "**No metric groups configured**",
                "lead": "**unavailable**",
                "cycle": "**unavailable**",
            }],
            "filtered_rows": [{
                "type": "**No metric groups configured**",
                "lead": "**unavailable**",
                "cycle": "**unavailable**",
            }],
        })

    if longest_lead is None:
        longest_lead_line = "**Longest Lead Time:** unavailable"
    else:
        longest_lead_line = (
            "**Longest Lead Time:** "
            f"{float(longest_lead['days']):.2f} days "
            f"({longest_lead['key']}, {longest_lead['status']})"
        )

    if longest_cycle is None:
        longest_cycle_line = "**Longest Cycle Time:** unavailable"
    else:
        longest_cycle_line = (
            "**Longest Cycle Time:** "
            f"{float(longest_cycle['days']):.2f} days "
            f"({longest_cycle['key']}, {longest_cycle['status']})"
        )

    lines = [
        f"# Support Stats - {month_label}",
        "",
    ]

    lines.extend([
        "## Jira Flow Metrics",
        "",
        "### Flow Trend (Individual PR Lead Time)",
        image_block(jira_flow_trend_vs_individual_chart, f"Vertical Support Individual PR Lead Time {month_label}", report_dir),
        "",
        "| Type | Lead Time (avg.) | Cycle Time (avg.) |",
        "| --- | --- | --- |",
    ])

    for section in metric_sections:
        rows = section.get("rows") if isinstance(section.get("rows"), list) else []
        for row in rows:
            type_value = row.get("type", "") if isinstance(row, dict) else ""
            lead_value = row.get("lead", "unavailable") if isinstance(row, dict) else "unavailable"
            cycle_value = row.get("cycle", "unavailable") if isinstance(row, dict) else "unavailable"
            lines.append(
                "| "
                f"{markdown_table_cell(type_value)} | "
                f"{markdown_table_cell(lead_value)} | "
                f"{markdown_table_cell(cycle_value)} |"
            )

        lines.append("|  |  |  |")

    lines.append("")

    lines.extend([
        "### Jira Flow Metrics (Excluding Outliers)",
        "",
        "| Type | Lead Time (avg.) | Cycle Time (avg.) |",
        "| --- | --- | --- |",
    ])

    for section in metric_sections:
        rows = section.get("filtered_rows") if isinstance(section.get("filtered_rows"), list) else []
        for row in rows:
            type_value = row.get("type", "") if isinstance(row, dict) else ""
            lead_value = row.get("lead", "unavailable") if isinstance(row, dict) else "unavailable"
            cycle_value = row.get("cycle", "unavailable") if isinstance(row, dict) else "unavailable"
            lines.append(
                "| "
                f"{markdown_table_cell(type_value)} | "
                f"{markdown_table_cell(lead_value)} | "
                f"{markdown_table_cell(cycle_value)} |"
            )

        lines.append("|  |  |  |")

    lines.append("")

    if pr_split is None:
        lines.extend(["**Internal vs External PRs:** unavailable", ""])
    else:
        stat_line = (
            "**Internal vs External PRs:** "
            f"External {pr_split['external_pct']:.1f}% ({pr_split['external_count']}/{pr_split['total']}), "
            f"Internal {pr_split['internal_pct']:.1f}% ({pr_split['internal_count']}/{pr_split['total']})"
        )
        if pr_split["unknown_count"]:
            stat_line += f", Unknown {pr_split['unknown_count']}"
        lines.extend([stat_line, ""])

    lines.extend([
        longest_lead_line,
        "",
        longest_cycle_line,
        "",
        "## GitHub Charts",
        "",
        "### PR Heatmap",
        image_block(charts["github_heatmap"], f"GitHub PR Heatmap {month_label}", report_dir),
        "",
        "### Internal vs External PRs",
        image_block(charts["github_split"], f"GitHub Internal vs External {month_label}", report_dir),
        "",
        "## Jira Charts - Vertical Support",
        "",
        "### Service Heatmap",
        image_block(charts["jira_service"], f"Jira Service Heatmap {month_label}", report_dir),
        "",
        "### Requesting Team Heatmap",
        image_block(charts["jira_team"], f"Jira Requesting Team Heatmap {month_label}", report_dir),
        "",
    ])

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a markdown report with charts and Jira lead/cycle time stats.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument("--output", default="", help="Optional output markdown file path.")
    args = parser.parse_args(argv)

    today = dt.date.today()
    year, month = parse_month(args.month) if args.month else (today.year, today.month)

    output = args.output.strip() or os.path.join(REPORTS_DIR, f"report_{year}_{month:02d}.md")
    report_dir = os.path.dirname(output) or "."
    os.makedirs(report_dir, exist_ok=True)

    content = build_report(year, month, report_dir)
    with open(output, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Saved report: {output}")


if __name__ == "__main__":
    main()
