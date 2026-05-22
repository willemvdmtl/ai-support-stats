#!/usr/bin/env python3
"""Generate a monthly markdown report with configurable cycle-time groups."""

import argparse
import datetime as dt
import os
import sys
from typing import Dict, List, Optional, Tuple

from common.setup_utils import read_json

REPORTS_DIR = "reports"
GITHUB_CACHE_DIR = "cache/github"
JIRA_CACHE_DIR = "cache/jira"
JIRA_CONFIG_FILE = "config/jira-minimal.json"
METRIC_GROUPS_CONFIG_FILE = "config/jira-metric-groups.json"


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
    if not os.path.exists(JIRA_CACHE_DIR):
        return []

    tickets_by_key: Dict[str, Dict] = {}
    for name in sorted(os.listdir(JIRA_CACHE_DIR)):
        if not name.startswith("issues-") or not name.endswith(".json"):
            continue
        path = os.path.join(JIRA_CACHE_DIR, name)
        payload = read_json(path)
        for ticket in payload.get("issues", []):
            key = str(ticket.get("key") or "")
            if not key:
                continue
            tickets_by_key[key] = ticket

    return list(tickets_by_key.values())


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


def image_block(path: str, alt_text: str, report_dir: str) -> str:
    if os.path.exists(path):
        rel_path = os.path.relpath(path, start=report_dir or ".").replace(os.sep, "/")
        return f"![{alt_text}]({rel_path})"
    return f"_Not available: {path}_"


def build_report(year: int, month: int, report_dir: str) -> str:
    month_label = dt.date(year, month, 1).strftime("%B %Y")
    charts = chart_files(year, month)

    all_tickets = all_jira_tickets()
    policy = load_metric_groups_policy()
    validity = policy.get("validity") if isinstance(policy.get("validity"), dict) else {}

    group_lines: List[str] = []
    longest_wait: Optional[Dict[str, object]] = None

    groups = policy.get("groups") if isinstance(policy.get("groups"), list) else []
    for idx, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "Group")
        group_tickets = select_group_tickets(all_tickets, group)
        window_policy = group.get("window_policy") if isinstance(group.get("window_policy"), dict) else {}
        (window_start, window_end), suffix = resolve_window(year, month, window_policy)
        durations, longest = jira_cycle_stats_in_window(group_tickets, window_start, window_end, validity)

        if durations:
            avg_days = sum(durations) / len(durations)
            group_lines.append(
                f"**Cycle Time - {label}:** {avg_days:.2f} days"
                f"{suffix} (average from {len(durations)} tickets)"
            )
        else:
            group_lines.append(f"**Cycle Time - {label}:** unavailable")

        if idx == 0:
            longest_wait = longest

    if not group_lines:
        group_lines.append("**Cycle Time:** unavailable (no metric groups configured)")

    if longest_wait is None:
        longest_wait_line = "**Longest Wait:** unavailable"
    else:
        longest_wait_line = (
            "**Longest Wait:** "
            f"{float(longest_wait['days']):.2f} days "
            f"({longest_wait['key']}, {longest_wait['status']})"
        )

    lines = [
        f"# Support Stats - {month_label}",
        "",
    ]

    for line in group_lines:
        lines.extend([line, ""])

    lines.extend([
        longest_wait_line,
        "",
        "## GitHub Charts",
        "",
        "### PR Heatmap",
        image_block(charts["github_heatmap"], f"GitHub PR Heatmap {month_label}", report_dir),
        "",
        "### Internal vs External PRs",
        image_block(charts["github_split"], f"GitHub Internal vs External {month_label}", report_dir),
        "",
        "## Jira Charts",
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
    parser = argparse.ArgumentParser(description="Generate a markdown report with charts and cycle time stat.")
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
