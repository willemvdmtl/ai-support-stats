#!/usr/bin/env python3
"""Generate Jira ticket charts for a given month."""

import argparse
import calendar
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from common.palette import build_colormap
from common.setup_utils import read_json

REPORTS_DIR = "reports"
CONSOLIDATED_DIR = "cache/jira"
CANONICAL_CREATED_DIR = "cache/jira/by-created"
UPDATED_INDEX_DIR = "cache/jira/index-updated"
TEAM_NORMALIZATION_FILE = "config/jira-team-normalization.json"
ORG_STRUCTURE_FILE = "config/jira-org-structure.json"
OVERRIDES_FILE = "config/jira-team-overrides.json"
SERVICE_NORMALIZATION_FILE = "config/jira-service-normalization.json"
SERVICE_OVERRIDES_FILE = "config/jira-service-overrides.json"
JIRA_CONFIG_FILE = "config/jira-minimal.json"
DEFAULT_BASIC_GROUP_FIELDS: List[Tuple[str, str]] = [
    ("customfield_16011", "Service Name"),
]
METRIC_GROUPS_CONFIG_FILE = "config/jira-metric-groups.json"
OUTLIER_IQR_MULTIPLIER = 5.0


def load_heatmap_date_anchor() -> str:
    """Return heatmap date anchor from config; defaults to 'created'."""
    data = _read_optional_json(JIRA_CONFIG_FILE)
    raw = str(
        (data.get("heatmap_date_anchor") if isinstance(data, dict) else "")
        or (data.get("heatmap_event_anchor") if isinstance(data, dict) else "")
        or "created"
    ).strip().lower()

    if raw in {"resolved", "resolution", "resolved_at", "resolutiondate"}:
        return "resolved"
    if raw in {"created", "creation", "created_at"}:
        return "created"
    return "created"


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"issues-{year}-{month:02d}.json")


def canonical_created_file(year: int, month: int) -> str:
    return os.path.join(CANONICAL_CREATED_DIR, f"issues-{year}-{month:02d}.json")


def updated_index_file(year: int, month: int) -> str:
    return os.path.join(UPDATED_INDEX_DIR, f"updated-{year}-{month:02d}.json")


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
        return parsed.year, parsed.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def load_vertical_support_filters() -> Tuple[set, set]:
    data = _read_optional_json(JIRA_CONFIG_FILE)
    vertical = data.get("vertical_support") if isinstance(data, dict) else {}
    issue_types = []
    tags = []
    if isinstance(vertical, dict):
        issue_types = vertical.get("issue_types") or []
        tags = vertical.get("tags") or []
    if not issue_types and isinstance(data, dict):
        issue_types = data.get("issue_types") or []

    def normalize(values: List[str]) -> set:
        normalized = set()
        for raw in values or []:
            cleaned = str(raw).strip().lower()
            if cleaned:
                normalized.add(cleaned)
        return normalized

    return normalize(issue_types), normalize(tags)


def ticket_issue_type(ticket: Dict) -> str:
    fields = ticket_fields(ticket)
    issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    return str(issue_type.get("name") or "").strip()


def ticket_labels(ticket: Dict) -> set:
    fields = ticket_fields(ticket)
    labels = fields.get("labels") if isinstance(fields.get("labels"), list) else []
    return {str(label).strip().lower() for label in labels if str(label).strip()}


def is_vertical_support_ticket(ticket: Dict, issue_types: set, tags: set) -> bool:
    by_type = bool(issue_types) and ticket_issue_type(ticket).lower() in issue_types
    by_tag = bool(tags) and bool(ticket_labels(ticket) & tags)
    return by_type or by_tag


def _ticket_anchor_field(date_anchor: str) -> str:
    return "resolutiondate" if date_anchor == "resolved" else "created"


def _updated_index_keys(year: int, month: int) -> set:
    path = updated_index_file(year, month)
    if not os.path.exists(path):
        return set()
    payload = read_json(path)
    if isinstance(payload, dict):
        issues = payload.get("issues", [])
    elif isinstance(payload, list):
        issues = payload
    else:
        issues = []

    keys = set()
    if isinstance(issues, list):
        for item in issues:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                keys.add(key)
    return keys


def _hydrate_issues_by_keys(keys: set) -> List[Dict]:
    if not keys:
        return []
    scan_dir = CANONICAL_CREATED_DIR if os.path.exists(CANONICAL_CREATED_DIR) else CONSOLIDATED_DIR
    if not os.path.exists(scan_dir):
        return []

    tickets_by_key: Dict[str, Dict] = {}
    for name in sorted(os.listdir(scan_dir)):
        if not name.startswith("issues-") or not name.endswith(".json"):
            continue
        path = os.path.join(scan_dir, name)
        payload = read_json(path)
        for ticket in payload.get("issues", []):
            key = str(ticket.get("key") or "").strip()
            if key and key in keys:
                tickets_by_key[key] = ticket

    return list(tickets_by_key.values())


def load_tickets(year: int, month: int, date_anchor: str = "created") -> List[Dict]:
    updated_keys = _updated_index_keys(year, month)
    issues = _hydrate_issues_by_keys(updated_keys)

    if not issues:
        path = canonical_created_file(year, month)
        if not os.path.exists(path):
            path = consolidated_file(year, month)
        if not os.path.exists(path):
            print(f"ERROR: No consolidated Jira data found at {path}")
            print(f"Run first:  python3 scripts/fetch-data.py jira --month {year}-{month:02d}")
            sys.exit(1)
        data = read_json(path)
        issues = data.get("issues", [])

    issue_types, tags = load_vertical_support_filters()
    selected = issues
    if issue_types or tags:
        selected = [ticket for ticket in issues if is_vertical_support_ticket(ticket, issue_types, tags)]

    target_month = f"{year}-{month:02d}"
    anchor_field = _ticket_anchor_field(date_anchor)
    return [
        ticket
        for ticket in selected
        if str(ticket_fields(ticket).get(anchor_field) or "").startswith(target_month)
    ]


def ticket_fields(ticket: Dict) -> Dict:
    fields = ticket.get("fields")
    return fields if isinstance(fields, dict) else {}


def ticket_anchor_day(ticket: Dict, date_anchor: str) -> Optional[int]:
    anchor_value = ticket_fields(ticket).get(_ticket_anchor_field(date_anchor), "")
    if not anchor_value:
        return None
    try:
        return dt.datetime.strptime(anchor_value[:19], "%Y-%m-%dT%H:%M:%S").day
    except ValueError:
        return None


def ticket_components(ticket: Dict, include_unassigned: bool = True) -> List[str]:
    components = ticket_fields(ticket).get("components") or []
    names: List[str] = []
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                name = str(component.get("name", "")).strip()
                if name:
                    names.append(name)
    if names:
        return names
    return ["Unassigned"] if include_unassigned else []


def _split_csv_like(values: List[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        parts = [chunk.strip() for chunk in value.split(",")]
        for part in parts:
            if part:
                out.append(part)
    return out


def ticket_basic_groups(ticket: Dict, field_specs: List[Tuple[str, str]]) -> Tuple[List[str], str]:
    fields = ticket_fields(ticket)

    for field_id, label in field_specs:
        if field_id == "components":
            groups = ticket_components(ticket, include_unassigned=False)
        else:
            groups = _split_csv_like(_flatten_values(fields.get(field_id)))
        if groups:
            return groups, label

    # Deterministic fallback: infer service from GitHub repo links in the Jira
    # description when the dedicated service field is empty.
    desc_repo_groups = ticket_description_repositories(ticket)
    if desc_repo_groups:
        return desc_repo_groups, "Description Repo"

    return ["Unassigned"], "Unassigned"


def ticket_description_repositories(ticket: Dict) -> List[str]:
    fields = ticket_fields(ticket)
    description = fields.get("description")
    if not description:
        return []

    raw = json.dumps(description)
    repos = re.findall(r'https?://github\.com/[^/\"]+/([^/\"#?]+)', raw, flags=re.IGNORECASE)
    if not repos:
        return []

    deduped: List[str] = []
    seen = set()
    for repo in repos:
        name = str(repo).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _flatten_values(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        flattened: List[str] = []
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    if isinstance(value, dict):
        for key in ("value", "displayName", "name"):
            if key in value:
                return _flatten_values(value.get(key))
    return []


def _read_optional_json(path: str) -> Dict:
    return read_json(path) if os.path.exists(path) else {}


def _normalize_values(values: List[str]) -> set:
    normalized = set()
    for raw in values or []:
        cleaned = str(raw).strip().lower()
        if cleaned:
            normalized.add(cleaned)
    return normalized


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
    raw = _read_optional_json(METRIC_GROUPS_CONFIG_FILE)
    if not isinstance(raw, dict):
        raw = {}

    defaults_raw = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
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
        groups.append(
            {
                "id": str(group.get("id") or f"group_{idx + 1}").strip(),
                "label": label,
                "include_any": _normalize_filter(group_filter.get("include_any")),
                "exclude_any": _normalize_filter(group_filter.get("exclude_any")),
                "force_include_any": _normalize_filter(group_filter.get("force_include_any")),
                "window_policy": _normalize_window_policy(group.get("window_policy"), default_window),
            }
        )

    return {
        "validity": validity,
        "groups": groups,
    }


def _filter_is_empty(filter_spec: Dict[str, set]) -> bool:
    return not filter_spec.get("issue_types") and not filter_spec.get("labels")


def _ticket_matches_filter(ticket: Dict, filter_spec: Dict[str, set]) -> bool:
    issue_types = filter_spec.get("issue_types") or set()
    labels = filter_spec.get("labels") or set()
    by_type = bool(issue_types) and ticket_issue_type(ticket).lower() in issue_types
    by_label = bool(labels) and bool(ticket_labels(ticket) & labels)
    return by_type or by_label


def select_group_tickets(all_tickets: List[Dict], group: Dict[str, object]) -> List[Dict]:
    include_any = group.get("include_any") if isinstance(group.get("include_any"), dict) else {"issue_types": set(), "labels": set()}
    exclude_any = group.get("exclude_any") if isinstance(group.get("exclude_any"), dict) else {"issue_types": set(), "labels": set()}
    force_include_any = group.get("force_include_any") if isinstance(group.get("force_include_any"), dict) else {"issue_types": set(), "labels": set()}

    selected: Dict[str, Dict] = {}
    for ticket in all_tickets:
        if not _filter_is_empty(include_any) and not _ticket_matches_filter(ticket, include_any):
            continue
        if not _filter_is_empty(exclude_any) and _ticket_matches_filter(ticket, exclude_any):
            continue
        key = str(ticket.get("key") or "")
        if key:
            selected[key] = ticket

    if not _filter_is_empty(force_include_any):
        for ticket in all_tickets:
            if not _ticket_matches_filter(ticket, force_include_any):
                continue
            key = str(ticket.get("key") or "")
            if key:
                selected[key] = ticket

    return list(selected.values())


def parse_jira_datetime(raw: str) -> Optional[dt.datetime]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def normalized_resolution_name(ticket: Dict) -> str:
    fields = ticket_fields(ticket)
    resolution = fields.get("resolution") if isinstance(fields.get("resolution"), dict) else {}
    normalized = str(resolution.get("name") or "").strip().lower().replace("'", "")
    return " ".join(normalized.split())


def _is_cycle_start_status(status_name: str) -> bool:
    name = status_name.strip().lower()
    if not name:
        return False
    if name in {"in progress", "in development", "development in progress", "doing"}:
        return True
    return "in progress" in name


def _is_cycle_fallback_review_status(status_name: str) -> bool:
    return status_name.strip().lower() == "in review"


def _first_in_progress_transition_at(ticket: Dict) -> Optional[dt.datetime]:
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
            if _is_cycle_start_status(to_status):
                transition_times.append(changed_at)
                break
            if _is_cycle_fallback_review_status(to_status):
                fallback_review_times.append(changed_at)
                break

    if transition_times:
        return min(transition_times)
    if fallback_review_times:
        return min(fallback_review_times)
    return None


def _month_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    month_start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        month_end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        month_end = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)
    return month_start, month_end


def _month_to_date_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    month_start, month_end = _month_window(year, month)
    now_utc = dt.datetime.now(dt.timezone.utc)
    return month_start, min(now_utc, month_end)


def _rolling_window(year: int, month: int, days: int) -> Tuple[dt.datetime, dt.datetime]:
    _, month_end = _month_window(year, month)
    today_utc = dt.datetime.now(dt.timezone.utc)
    window_end = min(today_utc, month_end)
    window_start = window_end - dt.timedelta(days=days)
    return window_start, window_end


def resolve_window(year: int, month: int, policy: Dict[str, object]) -> Tuple[dt.datetime, dt.datetime]:
    mode = str(policy.get("mode") or "auto")
    rolling_days = int(policy.get("rolling_days_if_month_data_lt_days") or 14)
    month_when_ready = str(policy.get("month_window_type_if_ready") or "month_to_date")

    if mode == "rolling":
        return _rolling_window(year, month, rolling_days)
    if mode == "full_month":
        return _month_window(year, month)
    if mode == "month_to_date":
        return _month_to_date_window(year, month)

    window_start, window_end = _month_to_date_window(year, month)
    if window_end <= window_start:
        return window_start, window_end
    elapsed_days = (window_end - window_start).total_seconds() / 86400.0
    if elapsed_days < float(rolling_days):
        return _rolling_window(year, month, rolling_days)
    if month_when_ready == "full_month":
        return _month_window(year, month)
    return window_start, window_end


def jira_lead_stats_in_window(
    tickets: List[Dict],
    window_start: dt.datetime,
    window_end: dt.datetime,
    validity: Dict[str, bool],
) -> List[float]:
    durations_days: List[float] = []
    for ticket in tickets:
        fields = ticket_fields(ticket)
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
        durations_days.append(delta / 86400.0)

    return durations_days


def jira_lead_durations_by_ticket_in_window(
    tickets: List[Dict],
    window_start: dt.datetime,
    window_end: dt.datetime,
    validity: Dict[str, bool],
) -> List[Tuple[Dict, float]]:
    durations: List[Tuple[Dict, float]] = []
    for ticket in tickets:
        fields = ticket_fields(ticket)
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

        durations.append((ticket, delta / 86400.0))

    return durations


def jira_cycle_stats_in_window(
    tickets: List[Dict],
    window_start: dt.datetime,
    window_end: dt.datetime,
    validity: Dict[str, bool],
) -> List[float]:
    durations_days: List[float] = []
    for ticket in tickets:
        fields = ticket_fields(ticket)
        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if resolution is None and validity.get("require_resolved", True):
            continue
        if resolution is None:
            continue

        resolution_utc = resolution.astimezone(dt.timezone.utc)
        if not (window_start <= resolution_utc < window_end):
            continue

        cycle_start = _first_in_progress_transition_at(ticket)
        if cycle_start is None:
            continue

        delta = (resolution - cycle_start).total_seconds()
        if delta < 0 and validity.get("exclude_negative_durations", True):
            continue
        durations_days.append(delta / 86400.0)

    return durations_days


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


def filter_iqr_outliers(values: List[float]) -> List[float]:
    if len(values) < 4:
        return list(values)

    ordered = sorted(float(v) for v in values)
    q1 = percentile(ordered, 25)
    q3 = percentile(ordered, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return list(values)

    upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
    filtered = [float(v) for v in values if float(v) <= upper]
    return filtered if filtered else list(values)


def filter_iqr_outlier_pairs(values: List[Tuple[Dict, float]]) -> List[Tuple[Dict, float]]:
    if len(values) < 4:
        return list(values)

    ordered = sorted(float(v) for _, v in values)
    q1 = percentile(ordered, 25)
    q3 = percentile(ordered, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return list(values)

    upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
    filtered = [(ticket, float(v)) for ticket, v in values if float(v) <= upper]
    return filtered if filtered else list(values)


def _avg_or_none(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _normalize_group_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _find_metric_group(groups: List[Dict[str, object]], target_id: str, target_label: str) -> Optional[Dict[str, object]]:
    wanted_id = _normalize_group_key(target_id)
    wanted_label = _normalize_group_key(target_label)
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = _normalize_group_key(str(group.get("id") or ""))
        label = _normalize_group_key(str(group.get("label") or ""))
        if group_id == wanted_id or label == wanted_label:
            return group
    return None


def _apply_ktlo_exclusions(tickets: List[Dict]) -> List[Dict]:
    excluded_resolutions = {"wont do", "duplicate"}
    return [
        ticket
        for ticket in tickets
        if normalized_resolution_name(ticket) not in excluded_resolutions
        and ticket_issue_type(ticket).strip().lower() not in {"epic", "sub-task", "subtask"}
    ]


def _shift_month(year: int, month: int, offset: int) -> Tuple[int, int]:
    base = dt.date(year, month, 1)
    idx = base.year * 12 + (base.month - 1) + offset
    shifted_year = idx // 12
    shifted_month = (idx % 12) + 1
    return shifted_year, shifted_month


def _month_sequence(year: int, month: int, count: int) -> List[Tuple[int, int]]:
    return [_shift_month(year, month, -(count - 1) + i) for i in range(count)]


def all_jira_tickets() -> List[Dict]:
    scan_dir = CANONICAL_CREATED_DIR if os.path.exists(CANONICAL_CREATED_DIR) else CONSOLIDATED_DIR
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
            if key:
                tickets_by_key[key] = ticket
    return list(tickets_by_key.values())


_POSITIVE_RESOLUTIONS_EXCLUDED = {"wont do", "won't do", "duplicate"}
_ECOMMERCE_TEAM_FIELD = "customfield_22934"
_ECOMMERCE_TEAM_EXCLUDED = {"feature"}
_PR_SIZE_FIELD = "customfield_22936"
_PR_SIZE_ORDER = ("XS", "S", "M", "L", "XL")
_PR_SIZE_MAP = {
    "EXTRA SMALL": "XS",
    "XSMALL": "XS",
    "XS": "XS",
    "SMALL": "S",
    "S": "S",
    "MEDIUM": "M",
    "M": "M",
    "LARGE": "L",
    "L": "L",
    "EXTRA LARGE": "XL",
    "XLARGE": "XL",
    "XL": "XL",
}
_PRIORITY_LEVELS = ("Critical", "High", "Medium", "Low")
_ISSUE_TYPE_COLOR_MAP = {
    "pr": "#1f77b4",
    "pr request": "#1f77b4",
    "pull request": "#1f77b4",
    "story": "#ff7f0e",
    "task": "#2ca02c",
    "incident": "#d62728",
    "spike": "#9467bd",
    "defect": "#8c564b",
    "tech improvement": "#e377c2",
    "sub-task": "#7f7f7f",
    "subtask": "#7f7f7f",
    "other": "#17becf",
}
_ISSUE_TYPE_FALLBACK_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _is_positive_resolution(ticket: Dict) -> bool:
    """Return True if the ticket was resolved positively (not Won't Do or Duplicate)."""
    return normalized_resolution_name(ticket) not in _POSITIVE_RESOLUTIONS_EXCLUDED


def _is_excluded_ecommerce_team(ticket: Dict) -> bool:
    """Return True when ECommerce Team is set to a value we explicitly exclude."""
    fields = ticket_fields(ticket)
    raw = fields.get(_ECOMMERCE_TEAM_FIELD)

    value = ""
    if isinstance(raw, dict):
        value = str(raw.get("value") or raw.get("name") or raw.get("displayName") or "").strip().lower()
    elif isinstance(raw, str):
        value = raw.strip().lower()

    return value in _ECOMMERCE_TEAM_EXCLUDED


def _is_excluded_assignee(ticket: Dict) -> bool:
    """Return True when ticket assignee should be excluded from charting."""
    fields = ticket_fields(ticket)
    assignee = fields.get("assignee")
    if not isinstance(assignee, dict):
        return False

    name = str(assignee.get("displayName") or assignee.get("name") or "").strip().lower()
    if not name:
        return False

    return ("willem" in name) or name == "em" or name.startswith("em ") or name.endswith(" em")


def _pr_size_bucket(ticket: Dict) -> str:
    """Normalize PR size value from Jira custom field to XS/S/M/L/XL or empty string."""
    fields = ticket_fields(ticket)
    raw = fields.get(_PR_SIZE_FIELD)

    value = ""
    if isinstance(raw, dict):
        value = str(raw.get("value") or raw.get("name") or raw.get("displayName") or "").strip().upper()
    elif isinstance(raw, str):
        value = raw.strip().upper()

    return _PR_SIZE_MAP.get(value, "")


def _priority_bucket(ticket: Dict) -> str:
    """Normalize Jira priority to Critical/High/Medium/Low or empty string."""
    fields = ticket_fields(ticket)
    raw = fields.get("priority")

    value = ""
    if isinstance(raw, dict):
        value = str(raw.get("name") or raw.get("value") or "").strip().lower()
    elif isinstance(raw, str):
        value = raw.strip().lower()

    if value in {"critical", "highest", "blocker"}:
        return "Critical"
    if value in {"high", "major"}:
        return "High"
    if value in {"medium", "normal"}:
        return "Medium"
    if value in {"low", "minor", "lowest", "trivial"}:
        return "Low"
    return ""


def _priority_icon_url(ticket: Dict) -> str:
    """Return Jira priority icon URL, if present."""
    fields = ticket_fields(ticket)
    raw = fields.get("priority")
    if isinstance(raw, dict):
        url = str(raw.get("iconUrl") or "").strip()
        if url:
            return url
    bucket = _priority_bucket(ticket)
    fallback = {
        "Critical": "https://trainline.atlassian.net/images/icons/priorities/critical.svg",
        "High": "https://trainline.atlassian.net/images/icons/priorities/major.svg",
        "Medium": "https://trainline.atlassian.net/images/icons/priorities/medium.svg",
        "Low": "https://trainline.atlassian.net/images/icons/priorities/minor.svg",
    }
    if bucket in fallback:
        return fallback[bucket]
    return ""


def _issue_type_color(label: str) -> str:
    key = str(label or "").strip().lower()
    if key in _ISSUE_TYPE_COLOR_MAP:
        return _ISSUE_TYPE_COLOR_MAP[key]
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(_ISSUE_TYPE_FALLBACK_PALETTE)
    return _ISSUE_TYPE_FALLBACK_PALETTE[idx]


def _vs_issue_type_group(ticket: Dict) -> str:
    """Classify a Vertical Support ticket into one of four display groups."""
    t = ticket_issue_type(ticket).strip().lower()
    if t in {"pr request", "pull request", "pr", "externalrequest", "external request"}:
        return "PR"
    if t == "spike":
        return "Spike"
    if t == "defect":
        return "Defect"
    return "Other"


def _plot_flow_trend_group(ax, x, labels, lead_all, lead_filt, cycle_all, cycle_filt):
    """Plot lead/cycle all vs filtered on a single axes with distinct colors and styles."""
    # Lead: blue shades. Cycle: green shades. All: solid+filled. Filtered: dashed+open marker.
    ax.plot(x, lead_all,   color="#1f77b4", linewidth=2.5, marker="o", markersize=7,
            linestyle="-",  label="Lead time (all)")
    ax.plot(x, lead_filt,  color="#6baed6", linewidth=2,   marker="o", markersize=7,
            linestyle="--", dashes=(6, 3), label="Lead time (excl outliers)")
    ax.plot(x, cycle_all,  color="#2ca02c", linewidth=2.5, marker="s", markersize=7,
            linestyle="-",  label="Cycle time (all)")
    ax.plot(x, cycle_filt, color="#74c476", linewidth=2,   marker="s", markersize=7,
            linestyle="--", dashes=(6, 3), label="Cycle time (excl outliers)")
    ax.set_ylabel("Days")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(loc="upper left", ncol=2, fontsize=9)


def generate_flow_trend_chart(year: int, month: int, lookback_months: int = 6) -> str:
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    from io import BytesIO
    from urllib.parse import urlparse
    from urllib.request import urlopen
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage

    policy = load_metric_groups_policy()
    groups = policy.get("groups") if isinstance(policy.get("groups"), list) else []
    validity = policy.get("validity") if isinstance(policy.get("validity"), dict) else {}
    all_tickets = all_jira_tickets()

    vertical_group = _find_metric_group(groups, "vertical_support", "Vertical Support")
    ktlo_group = _find_metric_group(groups, "ktlo", "KTLO")
    if vertical_group is None or ktlo_group is None:
        print("  Missing Vertical Support or KTLO metric group. Skipping flow trend chart.")
        return ""

    months = _month_sequence(year, month, lookback_months)
    labels = [dt.date(y, m, 1).strftime("%b %Y") for y, m in months]
    x = list(range(len(months)))

    # VS: PR reviews split by PR size (XS→XL)
    vs_size_avg: Dict[str, List[Optional[float]]] = {s: [] for s in _PR_SIZE_ORDER}
    vs_size_counts: Dict[str, List[int]] = {s: [] for s in _PR_SIZE_ORDER}
    vs_month_assignee_counts: List[int] = []
    vs_month_ticket_totals: List[int] = []
    vs_individual_by_month: List[Dict[str, List[Dict[str, object]]]] = []
    vs_individual_outliers_excluded = 0

    # KTLO: lead by ticket type (all and filtered)
    ktlo_issue_types: set = set()
    ktlo_month_groups: List[Tuple[dt.datetime, dt.datetime, Dict[str, List[Dict]]]] = []
    ktlo_month_ticket_counts: List[int] = []
    ktlo_month_assignee_counts: List[int] = []

    for y, m in months:
        v_window = vertical_group.get("window_policy") if isinstance(vertical_group.get("window_policy"), dict) else {}
        k_window = ktlo_group.get("window_policy") if isinstance(ktlo_group.get("window_policy"), dict) else {}
        v_start, v_end = resolve_window(y, m, v_window)
        k_start, k_end = resolve_window(y, m, k_window)

        # Vertical Support: positively resolved PR tickets only, excluding ECommerce Team=Feature.
        vs_tickets = [
            t for t in select_group_tickets(all_tickets, vertical_group)
            if _is_positive_resolution(t) and not _is_excluded_ecommerce_team(t)
        ]
        vs_pr_tickets = [t for t in vs_tickets if _vs_issue_type_group(t) == "PR"]

        month_pr_tickets: List[Dict] = []
        month_assignees: set = set()
        for t in vs_pr_tickets:
            fields = ticket_fields(t)
            resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
            if resolution is None:
                continue
            resolution_utc = resolution.astimezone(dt.timezone.utc)
            if not (v_start <= resolution_utc < v_end):
                continue
            month_pr_tickets.append(t)

            assignee = fields.get("assignee")
            if isinstance(assignee, dict):
                aid = assignee.get("accountId") or assignee.get("name") or assignee.get("displayName")
                if aid:
                    month_assignees.add(aid)

        vs_month_assignee_counts.append(len(month_assignees))
        vs_month_ticket_totals.append(len(month_pr_tickets))
        month_size_points: Dict[str, List[Dict[str, object]]] = {s: [] for s in _PR_SIZE_ORDER}

        for size in _PR_SIZE_ORDER:
            size_tickets = [t for t in month_pr_tickets if _pr_size_bucket(t) == size]
            lead_pairs = jira_lead_durations_by_ticket_in_window(size_tickets, v_start, v_end, validity)
            filtered_pairs = filter_iqr_outlier_pairs(lead_pairs)
            lead_filtered = [days for _, days in filtered_pairs]
            vs_individual_outliers_excluded += max(0, len(lead_pairs) - len(filtered_pairs))
            vs_size_avg[size].append(_avg_or_none(lead_filtered))
            vs_size_counts[size].append(len(lead_filtered))
            month_size_points[size] = [
                {
                    "lead_days": days,
                    "priority": _priority_bucket(ticket),
                    "priority_icon_url": _priority_icon_url(ticket),
                }
                for ticket, days in sorted(filtered_pairs, key=lambda item: float(item[1]))
            ]

        vs_individual_by_month.append(month_size_points)

        # KTLO: positively resolved tickets only, excluding ECommerce Team=Feature and excluded assignees.
        ktlo_tickets = [
            t for t in _apply_ktlo_exclusions(select_group_tickets(all_tickets, ktlo_group))
            if _is_positive_resolution(t)
            and not _is_excluded_ecommerce_team(t)
            and not _is_excluded_assignee(t)
        ]

        # Build month-scoped ticket list + assignee set for compact axis annotation.
        month_tickets: List[Dict] = []
        month_assignee_ids: set = set()
        for t in ktlo_tickets:
            fields = ticket_fields(t)
            resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
            if resolution is None:
                continue
            resolution_utc = resolution.astimezone(dt.timezone.utc)
            if not (k_start <= resolution_utc < k_end):
                continue
            month_tickets.append(t)
            assignee = fields.get("assignee")
            if isinstance(assignee, dict):
                aid = assignee.get("accountId") or assignee.get("name") or assignee.get("displayName")
                if aid:
                    month_assignee_ids.add(aid)

        ktlo_month_ticket_counts.append(len(month_tickets))
        ktlo_month_assignee_counts.append(len(month_assignee_ids))

        grouped: Dict[str, List[Dict]] = {}
        for t in month_tickets:
            issue_type = ticket_issue_type(t) or "Unspecified"
            grouped.setdefault(issue_type, []).append(t)
            ktlo_issue_types.add(issue_type)
        ktlo_month_groups.append((k_start, k_end, grouped))

    sorted_ktlo_types = sorted(ktlo_issue_types)
    ktlo_series_all: Dict[str, List[Optional[float]]] = {t: [] for t in sorted_ktlo_types}
    ktlo_series_filtered: Dict[str, List[Optional[float]]] = {t: [] for t in sorted_ktlo_types}
    ktlo_type_totals: Dict[str, int] = {t: 0 for t in sorted_ktlo_types}

    for k_start, k_end, grouped in ktlo_month_groups:
        for issue_type in sorted_ktlo_types:
            type_tickets = grouped.get(issue_type, [])
            lead = jira_lead_stats_in_window(type_tickets, k_start, k_end, validity)
            ktlo_series_all[issue_type].append(_avg_or_none(lead))
            ktlo_series_filtered[issue_type].append(_avg_or_none(filter_iqr_outliers(lead)))
            ktlo_type_totals[issue_type] += len(lead)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out_vs = os.path.join(REPORTS_DIR, f"jira_flow_trend_vs_{year}_{month:02d}.png")
    out_vs_individual = os.path.join(REPORTS_DIR, f"jira_flow_trend_vs_individual_{year}_{month:02d}.png")
    out_ktlo = os.path.join(REPORTS_DIR, f"jira_flow_trend_ktlo_{year}_{month:02d}.png")

    # --- Vertical Support chart: average lead time bars by PR size ---
    fig_vs, ax_vs = plt.subplots(figsize=(13, 5))
    group_centers = x
    bar_widths = [0.10, 0.13, 0.16, 0.19, 0.22]  # XS -> XL (increasing width)
    width_total = sum(bar_widths)
    offsets: List[float] = []
    cursor = -width_total / 2.0
    for w in bar_widths:
        offsets.append(cursor + (w / 2.0))
        cursor += w

    size_colors = {
        "XS": "#6baed6",
        "S": "#4292c6",
        "M": "#2171b5",
        "L": "#08519c",
        "XL": "#08306b",
    }

    max_bar_value = 0.0
    for idx, size in enumerate(_PR_SIZE_ORDER):
        xpos = [c + offsets[idx] for c in group_centers]
        yvals = [v if v is not None else 0.0 for v in vs_size_avg[size]]
        bars = ax_vs.bar(
            xpos,
            yvals,
            width=bar_widths[idx],
            color=size_colors.get(size, _issue_type_color(size)),
            alpha=0.85,
            label=size,
        )

        for bar, count in zip(bars, vs_size_counts[size]):
            ax_vs.annotate(
                size,
                xy=(bar.get_x() + bar.get_width() / 2.0, 0),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#333333",
                clip_on=False,
            )
            if count > 0:
                h = bar.get_height()
                max_bar_value = max(max_bar_value, h)
                ax_vs.annotate(
                    str(count),
                    xy=(bar.get_x() + bar.get_width() / 2.0, h),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color="#333333",
                )

    assignee_label_y = max(12, max_bar_value * 1.15)
    for month_x, dev_count, total_count in zip(group_centers, vs_month_assignee_counts, vs_month_ticket_totals):
        ax_vs.annotate(
            f"{total_count} tickets | {dev_count} devs",
            xy=(month_x, assignee_label_y),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#444444",
        )
    ax_vs.set_ylabel("Days")
    _, vs_top = ax_vs.get_ylim()
    ax_vs.set_ylim(bottom=0, top=max(12, assignee_label_y * 1.10))
    ax_vs.set_xticks(x)
    ax_vs.set_xticklabels(labels, rotation=0, ha="center")
    ax_vs.tick_params(axis="x", pad=8)
    ax_vs.grid(alpha=0.25, linestyle=":")
    ax_vs.set_title("Lead Time - PR Reviews", fontsize=12, fontweight="bold")
    fig_vs.tight_layout()
    fig_vs.subplots_adjust(bottom=0.16)
    fig_vs.savefig(out_vs, dpi=200, bbox_inches="tight")
    plt.close(fig_vs)

    # --- Vertical Support chart: one bar per PR ticket (month sections; size-group sorting) ---
    total_pr_count = sum(sum(len(month_data.get(s, [])) for s in _PR_SIZE_ORDER) for month_data in vs_individual_by_month)
    individual_width = 13.0
    fig_vs_individual, ax_vs_individual = plt.subplots(figsize=(individual_width, 5))

    if total_pr_count > 0:
        xpos: List[float] = []
        yvals: List[float] = []
        colors: List[str] = []
        priorities: List[str] = []
        priority_icon_urls: List[str] = []
        group_annotations: List[Tuple[float, str]] = []
        month_tick_positions: List[float] = []
        month_count_annotations: List[Tuple[float, int, int]] = []
        month_boundaries: List[float] = []
        size_bar_widths = {"XS": 0.40, "S": 0.56, "M": 0.74, "L": 0.94, "XL": 1.17}

        cursor = 0.0
        month_gap = 2.0
        for month_idx, month_data in enumerate(vs_individual_by_month):
            month_start = cursor

            for size in _PR_SIZE_ORDER:
                points = month_data.get(size, [])
                if not points:
                    continue

                bar_w = float(size_bar_widths.get(size, 0.88))
                group_start = cursor
                for point in points:
                    lead_days = float(point.get("lead_days") or 0.0)
                    xpos.append(cursor + (bar_w / 2.0))
                    yvals.append(lead_days)
                    colors.append(size_colors.get(size, "#4c78a8"))
                    priorities.append(str(point.get("priority") or ""))
                    priority_icon_urls.append(str(point.get("priority_icon_url") or ""))
                    cursor += bar_w
                group_end = cursor
                group_annotations.append(((group_start + group_end) / 2.0, size))

            if cursor > month_start:
                month_tick_positions.append((month_start + cursor) / 2.0)
                month_count_annotations.append(
                    ((month_start + cursor) / 2.0, vs_month_assignee_counts[month_idx], vs_month_ticket_totals[month_idx])
                )
            else:
                month_tick_positions.append(month_start)
                month_count_annotations.append((month_start, vs_month_assignee_counts[month_idx], vs_month_ticket_totals[month_idx]))

            month_boundaries.append(cursor)
            if month_idx < len(vs_individual_by_month) - 1:
                cursor += month_gap

        bar_widths: List[float] = []
        for month_data in vs_individual_by_month:
            for size in _PR_SIZE_ORDER:
                points = month_data.get(size, [])
                if not points:
                    continue
                w = float(size_bar_widths.get(size, 0.88))
                bar_widths.extend([w] * len(points))

        ax_vs_individual.bar(xpos, yvals, width=bar_widths, color=colors, alpha=0.88)

        if len(xpos) >= 2:
            x_mean = sum(xpos) / float(len(xpos))
            y_mean = sum(yvals) / float(len(yvals))
            num = sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(xpos, yvals))
            den = sum((xv - x_mean) * (xv - x_mean) for xv in xpos)
            if den > 0:
                slope = num / den
                intercept = y_mean - (slope * x_mean)
                trend_x_start = min(xpos)
                trend_x_end = max(xpos)
                trend_y_start = (slope * trend_x_start) + intercept
                trend_y_end = (slope * trend_x_end) + intercept
                ax_vs_individual.plot(
                    [trend_x_start, trend_x_end],
                    [trend_y_start, trend_y_end],
                    color="#9a9a9a",
                    linewidth=1.2,
                    linestyle=":",
                    alpha=0.7,
                    zorder=3,
                )

        for center_x, size in group_annotations:
            ax_vs_individual.annotate(
                size,
                xy=(center_x, 0),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=6,
                color="#333333",
                clip_on=False,
            )

        for boundary in month_boundaries[:-1]:
            ax_vs_individual.axvline(boundary + (month_gap / 2.0), color="#d8d8d8", linestyle=":", linewidth=0.8, zorder=0)

        max_y = max(yvals) if yvals else 0.0
        month_label_y = max(12.0, max_y * 1.12)

        icon_cache_dir = os.path.join(CONSOLIDATED_DIR, "priority-icons")
        os.makedirs(icon_cache_dir, exist_ok=True)
        icon_image_cache: Dict[str, Optional[object]] = {}

        def _load_priority_icon_image(icon_url: str):
            url = str(icon_url or "").strip()
            if not url:
                return None
            if url in icon_image_cache:
                return icon_image_cache[url]

            fetch_urls = [url]
            if url.lower().endswith(".svg"):
                fetch_urls = [url[:-4] + ".png", url]

            image_data = None
            for candidate_url in fetch_urls:
                ext = os.path.splitext(urlparse(candidate_url).path)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    ext = ".png"
                cache_name = f"{hashlib.md5(candidate_url.encode('utf-8')).hexdigest()}{ext}"
                cache_path = os.path.join(icon_cache_dir, cache_name)

                raw_bytes: Optional[bytes] = None
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path, "rb") as f:
                            raw_bytes = f.read()
                    except OSError:
                        raw_bytes = None
                else:
                    try:
                        with urlopen(candidate_url, timeout=4) as resp:
                            raw_bytes = resp.read()
                        if raw_bytes:
                            with open(cache_path, "wb") as f:
                                f.write(raw_bytes)
                    except Exception:
                        raw_bytes = None

                if not raw_bytes:
                    continue

                try:
                    image_data = mpimg.imread(BytesIO(raw_bytes), format="png")
                except Exception:
                    try:
                        image_data = mpimg.imread(cache_path)
                    except Exception:
                        image_data = None
                if image_data is not None:
                    break

            icon_image_cache[url] = image_data
            return image_data

        priority_styles = {
            "Critical": {"marker": "D", "color": "#b2182b"},
            "High": {"marker": "^", "color": "#ef8a62"},
            "Low": {"marker": "s", "color": "#2166ac"},
        }
        priority_icon_y = -0.88
        for xv, yv, pv, icon_url in zip(xpos, yvals, priorities, priority_icon_urls):
            if pv == "Medium":
                continue

            icon_y = priority_icon_y
            image_data = _load_priority_icon_image(icon_url)
            if image_data is not None:
                icon = OffsetImage(image_data, zoom=0.26)
                icon.set_alpha(0.72)
                marker = AnnotationBbox(
                    icon,
                    (xv, icon_y),
                    frameon=False,
                    box_alignment=(0.5, 0.0),
                    annotation_clip=False,
                    pad=0,
                    zorder=4,
                )
                ax_vs_individual.add_artist(marker)
                continue

            style = priority_styles.get(pv)
            if style:
                ax_vs_individual.scatter(
                    [xv],
                    [icon_y],
                    marker=style["marker"],
                    s=20,
                    facecolors="white",
                    edgecolors=style["color"],
                    linewidths=0.8,
                    alpha=0.65,
                    zorder=4,
                    clip_on=False,
                )

        for month_x, dev_count, total_count in month_count_annotations:
            ax_vs_individual.annotate(
                f"{total_count} tickets | {dev_count} devs",
                xy=(month_x, month_label_y),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8,
                color="#444444",
            )
        ax_vs_individual.set_ylim(bottom=0, top=max(12.0, month_label_y * 1.10))

        ax_vs_individual.set_xlim(-0.8, (month_boundaries[-1] + 0.8) if month_boundaries else 0.8)
        ax_vs_individual.set_xticks(month_tick_positions)
        ax_vs_individual.set_xticklabels(labels, rotation=0, ha="center")
        ax_vs_individual.tick_params(axis="x", pad=8)
    else:
        ax_vs_individual.text(0.5, 0.5, "No PR tickets in selected window", ha="center", va="center", transform=ax_vs_individual.transAxes)
        ax_vs_individual.set_xticks([])

    ax_vs_individual.text(
        0.01,
        0.98,
        f"Outliers excluded: {vs_individual_outliers_excluded}",
        transform=ax_vs_individual.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        color="#666666",
    )
    ax_vs_individual.text(
        0.99,
        0.98,
        f"{total_pr_count} PRs",
        transform=ax_vs_individual.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        color="#444444",
    )
    ax_vs_individual.set_ylabel("Days")
    ax_vs_individual.grid(axis="y", alpha=0.25, linestyle=":")
    ax_vs_individual.set_title("Lead Time - PR Reviews (Individual PRs)", fontsize=12, fontweight="bold")
    fig_vs_individual.tight_layout()
    fig_vs_individual.subplots_adjust(bottom=0.20)
    fig_vs_individual.savefig(out_vs_individual, dpi=200, bbox_inches="tight")
    plt.close(fig_vs_individual)

    # --- KTLO chart: lead time by ticket type ---
    fig_ktlo, ax_ktlo = plt.subplots(figsize=(13, 5))
    types_by_volume = sorted(sorted_ktlo_types, key=lambda t: (-ktlo_type_totals.get(t, 0), t.lower()))
    types_by_volume = [t for t in types_by_volume if ktlo_type_totals.get(t, 0) > 0]

    for idx, issue_type in enumerate(types_by_volume):
        color = _issue_type_color(issue_type)
        ax_ktlo.plot(x, ktlo_series_all[issue_type], color=color, linewidth=2.4,
                     marker="o", markersize=6, linestyle="-", label=f"{issue_type} (all)")
        ax_ktlo.plot(x, ktlo_series_filtered[issue_type], color=color, linewidth=1.9,
                     marker="o", markersize=6, linestyle="--", dashes=(6, 3), alpha=0.5,
                     label=f"{issue_type} (excl outliers)")

    ax_ktlo.set_ylabel("Days")
    _, ktlo_top = ax_ktlo.get_ylim()
    ax_ktlo.set_ylim(bottom=0, top=max(12, ktlo_top * 1.15))
    ax_ktlo.set_xticks(x)
    ktlo_labels = [
        f"{label}\nn={count} | devs={devs}"
        for label, count, devs in zip(labels, ktlo_month_ticket_counts, ktlo_month_assignee_counts)
    ]
    ax_ktlo.set_xticklabels(ktlo_labels, rotation=20, ha="right")
    ax_ktlo.grid(alpha=0.25, linestyle=":")
    ax_ktlo.legend(loc="upper left", ncol=2, fontsize=8)
    ax_ktlo.set_title("KTLO – Lead Time by Ticket Type (excl. outliers, Won't Do, Duplicate, Feature, Willem/EM)", fontsize=11, fontweight="bold")
    fig_ktlo.tight_layout()
    fig_ktlo.savefig(out_ktlo, dpi=200, bbox_inches="tight")
    plt.close(fig_ktlo)

    return f"{out_vs},{out_vs_individual},{out_ktlo}"


def _has_visible_cells(mask) -> bool:
    return (~mask).to_numpy().any()


_SEGMENT_SEP_RE = re.compile(r'\s+-\s+|\s+[\u2013\u2014]\s+')
_NORM_NONALNUM_RE = re.compile(r'[^a-z0-9 ]')
_SERVICE_NONALNUM_RE = re.compile(r'[^a-z0-9]')
_SERVICE_SUFFIX_RE = re.compile(r'(service|api|contract|domain)$')


def _norm_key(s: str) -> str:
    """Normalize a string for fuzzy matching: lowercase, collapse separators to spaces."""
    s = s.lower().strip()
    s = s.replace('&', 'and').replace('-', ' ').replace('_', ' ')
    s = _NORM_NONALNUM_RE.sub('', s)
    return re.sub(r'\s+', ' ', s).strip()


def _compact_service_key(s: str) -> str:
    return _SERVICE_NONALNUM_RE.sub('', str(s).strip().lower())


def _strip_service_suffix(s: str) -> str:
    return _SERVICE_SUFFIX_RE.sub('', s)


def _service_name_from_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    match = re.search(r'https?://github\.com/[^/\s]+/([^/\s?#]+)', value, flags=re.IGNORECASE)
    if not match:
        return value
    repo = str(match.group(1) or "").strip()
    return repo or value


def _build_structural_index(org_config: Dict) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """Build area→teams and team_key→canonical_name lookups from org structure."""
    area_to_teams: Dict[str, List[str]] = {}
    team_to_canonical: Dict[str, str] = {}
    for area in org_config.get('areas', []):
        area_keys = set()
        for field in ('area_name', 'area_slug'):
            key = _norm_key(area.get(field, ''))
            if key:
                area_keys.add(key)
        team_names: List[str] = []
        for team in area.get('teams', []):
            if team.get('retired'):
                continue
            canonical = team.get('team_name', '').strip()
            if not canonical:
                continue
            team_names.append(canonical)
            for field in ('team_name', 'team_slug'):
                key = _norm_key(team.get(field, ''))
                if key:
                    team_to_canonical.setdefault(key, canonical)
        for key in area_keys:
            area_to_teams[key] = team_names
    return area_to_teams, team_to_canonical


def _structural_lookup(
    raw: str,
    area_to_teams: Dict[str, List[str]],
    team_to_canonical: Dict[str, str],
) -> str:
    """Try to resolve raw value as 'Area - Team' or plain team name via org structure."""
    parts = _SEGMENT_SEP_RE.split(raw, maxsplit=1)
    if len(parts) == 2:
        # Try right segment as team first (most common: "Area - Team")
        right_team = team_to_canonical.get(_norm_key(parts[1]), '')
        if right_team:
            return right_team
        # Try left segment as team (unusual: "Team - something")
        left_team = team_to_canonical.get(_norm_key(parts[0]), '')
        if left_team:
            return left_team
    # Try whole string as a team name/slug
    return team_to_canonical.get(_norm_key(raw), '')


def load_team_configs() -> Tuple[Dict, Dict]:
    return _read_optional_json(TEAM_NORMALIZATION_FILE), _read_optional_json(ORG_STRUCTURE_FILE)


def _team_alias_map(team_config: Dict) -> Dict[str, str]:
    aliases = team_config.get("team_aliases") or {}
    normalized: Dict[str, str] = {}
    if isinstance(aliases, dict):
        for raw, canonical in aliases.items():
            raw_key = str(raw).strip().lower()
            canonical_name = str(canonical).strip()
            if raw_key and canonical_name:
                normalized[raw_key] = canonical_name
                normalized[canonical_name.lower()] = canonical_name
    return normalized


def _compose_team_display(parent_name: str, team_name: str) -> str:
    parent = str(parent_name or "").strip()
    team = str(team_name or "").strip()
    if parent and team:
        return f"{parent} - {team}"
    return team or parent


def _org_team_map(org_config: Dict) -> Dict[str, Dict[str, str]]:
    mapped: Dict[str, Dict[str, str]] = {}

    # Prefer detailed area records because they include sub-area information
    # needed to build the legacy-style "Parent - Team" normalized format.
    for area in org_config.get("areas", []):
        if not isinstance(area, dict):
            continue
        area_name = str(area.get("area_name") or "").strip()
        teams = area.get("teams") or []
        if not isinstance(teams, list):
            continue
        for team in teams:
            if not isinstance(team, dict):
                continue
            if team.get("retired"):
                continue
            canonical_name = str(team.get("team_name") or "").strip()
            if not canonical_name:
                continue
            sub_area_name = str(team.get("sub_area_name") or "").strip()
            parent_name = sub_area_name or area_name
            mapped[canonical_name] = {
                "display_name": _compose_team_display(parent_name, canonical_name),
                "area": area_name,
                "parent": parent_name,
            }

    teams = org_config.get("teams") or {}
    if not isinstance(teams, dict):
        return mapped

    for canonical, details in teams.items():
        canonical_name = str(canonical).strip()
        if not canonical_name:
            continue
        if canonical_name in mapped:
            continue
        if isinstance(details, str):
            area_name = details.strip()
            mapped[canonical_name] = {
                "display_name": _compose_team_display(area_name, canonical_name),
                "area": area_name,
                "parent": area_name,
            }
        elif isinstance(details, dict):
            area_name = str(details.get("area") or "").strip()
            parent_name = str(
                details.get("sub_area")
                or details.get("subArea")
                or details.get("parent")
                or area_name
                or ""
            ).strip()
            mapped[canonical_name] = {
                "display_name": str(
                    details.get("display_name")
                    or _compose_team_display(parent_name, canonical_name)
                    or canonical_name
                ).strip(),
                "area": area_name,
                "parent": parent_name,
            }
    return mapped


def _load_overrides() -> Dict[str, str]:
    """Load manually curated raw-value → canonical-team overrides. Never auto-generated."""
    raw = _read_optional_json(OVERRIDES_FILE)
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, str] = {}
    for k, v in raw.items():
        norm = str(k).strip().lower()
        canonical = str(v).strip()
        if norm and canonical:
            result[norm] = canonical
    return result



def _load_service_overrides() -> Dict[str, str]:
    """Load manually curated raw-value → canonical-service overrides."""
    raw = _read_optional_json(SERVICE_OVERRIDES_FILE)
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, str] = {}
    for k, v in raw.items():
        norm = str(k).strip().lower()
        canonical = str(v).strip()
        if norm and canonical:
            result[norm] = canonical
    return result


def _service_alias_map() -> Tuple[Dict[str, str], List[str]]:
    """
    Return (alias_map, canonical_names) from the generated service normalization file.

    alias_map: lowercase raw value → canonical service name
    canonical_names: list of known canonical service names (for exact-match tier)
    """
    data = _read_optional_json(SERVICE_NORMALIZATION_FILE)
    if not isinstance(data, dict):
        return {}, []
    aliases: Dict[str, str] = {}
    for k, v in (data.get("service_aliases") or {}).items():
        norm = str(k).strip().lower()
        canonical = str(v).strip()
        if norm and canonical:
            aliases[norm] = canonical
    canonical_names: List[str] = [str(n).strip() for n in (data.get("canonical_services") or []) if str(n).strip()]
    return aliases, canonical_names


def normalize_service_name(raw: str, overrides: Dict[str, str], aliases: Dict[str, str], canonical_names: List[str]) -> str:
    """
    Resolve a raw service field value to its canonical form.

    Tiers:
    1. Curated overrides (jira-service-overrides.json) — exact, case-insensitive
    2. Generated alias map (jira-service-normalization.json -> service_aliases)
    3. Exact case-insensitive match against known canonical names
    4. Raw value fallback
    """
    base = _service_name_from_url(raw)
    key = base.strip().lower()
    if not key:
        return base

    if key in overrides:
        return overrides[key]

    if key in aliases:
        return aliases[key]

    for name in canonical_names:
        if key == name.lower():
            return name

    # Fuzzy fallback for punctuation/case/spacing variants.
    compact = _compact_service_key(key)
    if not compact:
        return base.strip()

    # Match alias keys after compacting both sides.
    for alias_key, canonical in aliases.items():
        if compact == _compact_service_key(alias_key):
            return canonical

    # Match canonical names after compacting; allow optional common suffix removal.
    compact_stem = _strip_service_suffix(compact)
    for name in canonical_names:
        canonical_compact = _compact_service_key(name)
        if compact == canonical_compact:
            return name
        if compact_stem and compact_stem == _strip_service_suffix(canonical_compact):
            return name

    return base.strip()


def derive_normalized_tickets(tickets: List[Dict], year: int, month: int, date_anchor: str = "created") -> Dict:
    team_config, org_config = load_team_configs()
    requesting_fields = team_config.get("requesting_team_fields") or []
    alias_map = _team_alias_map(team_config)
    org_teams = _org_team_map(org_config)
    overrides = _load_overrides()
    area_to_teams, struct_team_map = _build_structural_index(org_config)

    normalized_tickets: List[Dict] = []
    mapped_count = 0
    raw_fallback_count = 0
    match_method_counts: Dict[str, int] = defaultdict(int)
    unmapped_values = defaultdict(int)

    for ticket in tickets:
        fields = ticket_fields(ticket)
        raw_team_values: List[str] = []
        for field_name in requesting_fields:
            raw_team_values.extend(_flatten_values(fields.get(field_name)))

        deduped_values: List[str] = []
        seen = set()
        for value in raw_team_values:
            key = value.strip().lower()
            if key and key not in seen:
                deduped_values.append(value.strip())
                seen.add(key)

        canonical_team = ""
        match_method = ""

        # Tier 1: curated overrides (exact, case-insensitive)
        for value in deduped_values:
            canonical_team = overrides.get(value.lower(), "")
            if canonical_team:
                match_method = "override"
                break

        # Tier 2: generated alias map
        if not canonical_team:
            for value in deduped_values:
                canonical_team = alias_map.get(value.lower(), "")
                if canonical_team:
                    match_method = "alias"
                    break

        # Tier 3: exact case-insensitive match against known team names
        if not canonical_team:
            for value in deduped_values:
                for known_team in org_teams:
                    if value.lower() == known_team.lower():
                        canonical_team = known_team
                        match_method = "exact"
                        break
                if canonical_team:
                    break

        # Tier 4: structural parse — "Area - Team" pattern via org structure
        if not canonical_team:
            for value in deduped_values:
                canonical_team = _structural_lookup(value, area_to_teams, struct_team_map)
                if canonical_team:
                    match_method = "structural"
                    break

        area = org_teams.get(canonical_team, {}).get("area", "") if canonical_team else ""
        normalized_display = org_teams.get(canonical_team, {}).get("display_name", "") if canonical_team else ""
        if canonical_team and not normalized_display:
            normalized_display = canonical_team

        raw_primary = deduped_values[0] if deduped_values else ""
        effective_team = normalized_display or raw_primary
        team_source = "mapped" if canonical_team else ("raw" if deduped_values else "missing")
        status = "mapped" if canonical_team else ("unmapped" if deduped_values else "missing")
        if canonical_team:
            match_method_counts[match_method] += 1
            mapped_count += 1
        elif deduped_values:
            match_method_counts["raw"] += 1
            raw_fallback_count += 1
            for value in deduped_values:
                unmapped_values[value] += 1
        else:
            match_method_counts["missing"] += 1

        normalized_tickets.append(
            {
                "key": ticket.get("key", ""),
                "created_at": fields.get("created", ""),
                "resolved_at": fields.get("resolutiondate", ""),
                "anchor_at": fields.get(_ticket_anchor_field(date_anchor), ""),
                "components": ticket_components(ticket),
                "requesting_team_raw": deduped_values,
                "requesting_team": normalized_display,
                "requesting_team_match_method": match_method or (team_source),
                "requesting_team_effective": effective_team,
                "requesting_team_source": team_source,
                "area": area,
                "normalization_status": status,
            }
        )

    payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "month": f"{year}-{month:02d}",
        "date_anchor": date_anchor,
        "ticket_count": len(normalized_tickets),
        "config_present": {
            "team_normalization": os.path.exists(TEAM_NORMALIZATION_FILE),
            "org_structure": os.path.exists(ORG_STRUCTURE_FILE),
        },
        "mapped_ticket_count": mapped_count,
        "raw_fallback_ticket_count": raw_fallback_count,
        "match_method_counts": dict(match_method_counts),
        "unmapped_values": [
            {"value": value, "count": count}
            for value, count in sorted(unmapped_values.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
        "tickets": normalized_tickets,
    }

    return payload


def generate_service_heatmap(tickets: List[Dict], year: int, month: int, date_anchor: str = "created") -> str:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from matplotlib.colors import ListedColormap

    month_name = dt.date(year, month, 1).strftime("%B %Y")
    days_in_month = calendar.monthrange(year, month)[1]
    today = dt.date.today()
    last_day = today.day if (today.year, today.month) == (year, month) else days_in_month
    weekend_days = {
        d for d in range(1, days_in_month + 1)
        if dt.date(year, month, d).weekday() >= 5
    }

    svc_overrides = _load_service_overrides()
    svc_aliases, svc_canonical = _service_alias_map()

    component_day: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    component_total: Dict[str, int] = defaultdict(int)
    source_label_count: Dict[str, int] = defaultdict(int)

    for ticket in tickets:
        day = ticket_anchor_day(ticket, date_anchor)
        if day is None or day > last_day:
            continue
        groups, source_label = ticket_basic_groups(ticket, DEFAULT_BASIC_GROUP_FIELDS)
        source_label_count[source_label] += 1
        for component in groups:
            normalized = normalize_service_name(component, svc_overrides, svc_aliases, svc_canonical)
            component_day[normalized][day] += 1
            component_total[normalized] += 1

    if not component_total:
        print(f"  No Jira tickets found for {month_name}. Skipping component heatmap.")
        return ""

    ranked_sources = sorted(source_label_count.items(), key=lambda item: (-item[1], item[0].lower()))
    dominant_source = ranked_sources[0][0] if ranked_sources else "Component"
    display_source = "Service" if dominant_source == "Description Repo" else dominant_source
    source_summary = ", ".join(f"{name}={count}" for name, count in ranked_sources)
    if source_summary:
        print(f"  Basic axis source usage: {source_summary}")

    components = sorted(component_total, key=lambda name: (component_total[name], name.lower()), reverse=True)
    day_cols = [str(d) for d in range(1, days_in_month + 1)]
    data_rows = []
    for component in components:
        row = [
            component_day[component][d] if d <= last_day else np.nan
            for d in range(1, days_in_month + 1)
        ]
        row.append(component_total[component])
        data_rows.append(row)

    day_totals = [
        sum(component_day[name][d] for name in components) if d <= last_day else np.nan
        for d in range(1, days_in_month + 1)
    ]
    day_totals.append(sum(component_total.values()))
    data_rows.append(day_totals)

    df = pd.DataFrame(data_rows, index=components + ["Total"], columns=day_cols + ["Total"])
    annot = df.map(lambda x: "" if pd.isna(x) else ("-" if int(x) == 0 else str(int(x))))
    data_only = df.iloc[:-1, :-1]
    vmax_data = max(3, int(data_only.max().max())) if not data_only.empty else 3

    row_totals = df.iloc[:-1, -1]
    col_totals = df.iloc[-1, :-1]
    totals_max = max(
        row_totals.max(skipna=True) if not row_totals.empty else 0,
        col_totals.max(skipna=True) if not col_totals.empty else 0,
    )
    vmax_totals = max(3, int(totals_max))

    data_mask = df.isna().copy()
    data_mask.loc["Total", :] = True
    data_mask.loc[:, "Total"] = True

    totals_mask = pd.DataFrame(True, index=df.index, columns=df.columns)
    totals_mask.loc["Total", :] = False
    totals_mask.loc[:, "Total"] = False
    totals_mask |= df.isna()

    fig_height = max(4, (len(components) + 1) * 0.5 + 1.5)
    fig, ax = plt.subplots(figsize=(17, fig_height))

    if _has_visible_cells(data_mask):
        sns.heatmap(
            df,
            annot=annot,
            fmt="",
            cmap=build_colormap("sky_purple"),
            cbar_kws={"label": "Tickets (daily cells)"},
            linewidths=0.5,
            linecolor="lightgray",
            vmin=0,
            vmax=vmax_data,
            mask=data_mask,
            ax=ax,
        )

    if _has_visible_cells(totals_mask):
        sns.heatmap(
            df,
            annot=annot,
            fmt="",
            cmap=build_colormap("peach_purple"),
            cbar=False,
            linewidths=0.5,
            linecolor="lightgray",
            vmin=0,
            vmax=vmax_totals,
            mask=totals_mask,
            ax=ax,
        )

    weekend_mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    for d in weekend_days:
        if str(d) in weekend_mask.columns:
            weekend_mask[str(d)] = True

    weekend_overlay_mask = ~(weekend_mask & ~df.isna())
    if _has_visible_cells(weekend_overlay_mask):
        sns.heatmap(
            df,
            cmap=ListedColormap(["#d0d0d0"]),
            cbar=False,
            linewidths=0.5,
            linecolor="lightgray",
            annot=False,
            mask=weekend_overlay_mask,
            ax=ax,
        )

    ax.hlines([len(components)], *ax.get_xlim(), colors="black", linewidths=2.5)
    ax.vlines([days_in_month], *ax.get_ylim(), colors="black", linewidths=2.5)

    for label in ax.get_yticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")
    for label in ax.get_xticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")

    plt.title(f"Jira Tickets by {display_source} - {month_name}", fontsize=14, fontweight="bold", pad=16)
    plt.xlabel("Day of month", fontsize=11, fontweight="bold")
    plt.ylabel(display_source, fontsize=11, fontweight="bold")
    plt.tight_layout()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out = os.path.join(REPORTS_DIR, f"jira_service_heatmap_{year}_{month:02d}.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    return out


def generate_team_heatmap(derived_payload: Dict, year: int, month: int) -> str:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from matplotlib.colors import ListedColormap

    if not derived_payload.get("config_present", {}).get("team_normalization"):
        print("  No Jira team normalization config. Skipping team heatmap.")
        print("  Expected: config/jira-team-normalization.json")
        return ""
    month_name = dt.date(year, month, 1).strftime("%B %Y")
    days_in_month = calendar.monthrange(year, month)[1]
    today = dt.date.today()
    last_day = today.day if (today.year, today.month) == (year, month) else days_in_month
    weekend_days = {
        d for d in range(1, days_in_month + 1)
        if dt.date(year, month, d).weekday() >= 5
    }

    team_day: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    team_total: Dict[str, int] = defaultdict(int)
    mapped_tickets = 0
    raw_tickets = 0

    for ticket in derived_payload.get("tickets", []):
        anchor_at = ticket.get("anchor_at") or ticket.get("created_at", "")
        if not anchor_at:
            continue
        try:
            day = dt.datetime.strptime(anchor_at[:19], "%Y-%m-%dT%H:%M:%S").day
        except ValueError:
            continue
        if day > last_day:
            continue
        team = ticket.get("requesting_team_effective", "")
        if not team:
            team = ticket.get("requesting_team", "")
        if not team:
            raw_values = ticket.get("requesting_team_raw") or []
            team = raw_values[0] if raw_values else ""
        if not team:
            continue
        team_day[team][day] += 1
        team_total[team] += 1
        if ticket.get("requesting_team_source") == "mapped":
            mapped_tickets += 1
        else:
            raw_tickets += 1

    if not team_total:
        print("  No usable Jira requesting-team values found. Skipping team heatmap.")
        unmapped = derived_payload.get("unmapped_values", [])
        if unmapped:
            print("  Top raw requesting-team values:")
            for item in unmapped[:5]:
                print(f"    - {item['value']} ({item['count']})")
        return ""

    print(f"  Team value usage: mapped={mapped_tickets}, raw_fallback={raw_tickets}")
    method_counts = derived_payload.get("match_method_counts") or {}
    if any(method_counts.get(m, 0) > 0 for m in ("override", "structural")):
        detail = ", ".join(
            f"{m}={method_counts[m]}"
            for m in ("override", "alias", "exact", "structural", "raw", "missing")
            if method_counts.get(m, 0) > 0
        )
        print(f"  Match method breakdown: {detail}")

    teams = sorted(team_total, key=lambda name: (team_total[name], name.lower()), reverse=True)
    day_cols = [str(d) for d in range(1, days_in_month + 1)]
    data_rows = []
    for team in teams:
        row = [team_day[team][d] if d <= last_day else np.nan for d in range(1, days_in_month + 1)]
        row.append(team_total[team])
        data_rows.append(row)

    day_totals = [sum(team_day[name][d] for name in teams) if d <= last_day else np.nan for d in range(1, days_in_month + 1)]
    day_totals.append(sum(team_total.values()))
    data_rows.append(day_totals)

    df = pd.DataFrame(data_rows, index=teams + ["Total"], columns=day_cols + ["Total"])
    annot = df.map(lambda x: "" if pd.isna(x) else ("-" if int(x) == 0 else str(int(x))))
    data_only = df.iloc[:-1, :-1]
    vmax_data = max(3, int(data_only.max().max())) if not data_only.empty else 3

    row_totals = df.iloc[:-1, -1]
    col_totals = df.iloc[-1, :-1]
    totals_max = max(
        row_totals.max(skipna=True) if not row_totals.empty else 0,
        col_totals.max(skipna=True) if not col_totals.empty else 0,
    )
    vmax_totals = max(3, int(totals_max))

    data_mask = df.isna().copy()
    data_mask.loc["Total", :] = True
    data_mask.loc[:, "Total"] = True

    totals_mask = pd.DataFrame(True, index=df.index, columns=df.columns)
    totals_mask.loc["Total", :] = False
    totals_mask.loc[:, "Total"] = False
    totals_mask |= df.isna()

    fig_height = max(4, (len(teams) + 1) * 0.5 + 1.5)
    fig, ax = plt.subplots(figsize=(17, fig_height))

    if _has_visible_cells(data_mask):
        sns.heatmap(
            df,
            annot=annot,
            fmt="",
            cmap=build_colormap("sky_purple"),
            cbar_kws={"label": "Tickets (daily cells)"},
            linewidths=0.5,
            linecolor="lightgray",
            vmin=0,
            vmax=vmax_data,
            mask=data_mask,
            ax=ax,
        )

    if _has_visible_cells(totals_mask):
        sns.heatmap(
            df,
            annot=annot,
            fmt="",
            cmap=build_colormap("peach_purple"),
            cbar=False,
            linewidths=0.5,
            linecolor="lightgray",
            vmin=0,
            vmax=vmax_totals,
            mask=totals_mask,
            ax=ax,
        )

    weekend_mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    for d in weekend_days:
        if str(d) in weekend_mask.columns:
            weekend_mask[str(d)] = True

    weekend_overlay_mask = ~(weekend_mask & ~df.isna())
    if _has_visible_cells(weekend_overlay_mask):
        sns.heatmap(
            df,
            cmap=ListedColormap(["#d0d0d0"]),
            cbar=False,
            linewidths=0.5,
            linecolor="lightgray",
            annot=False,
            mask=weekend_overlay_mask,
            ax=ax,
        )

    ax.hlines([len(teams)], *ax.get_xlim(), colors="black", linewidths=2.5)
    ax.vlines([days_in_month], *ax.get_ylim(), colors="black", linewidths=2.5)

    for label in ax.get_yticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")
    for label in ax.get_xticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")

    total_used = mapped_tickets + raw_tickets
    coverage = total_used / max(1, int(derived_payload.get("ticket_count", 0))) * 100
    mapped_ratio = mapped_tickets / max(1, total_used) * 100
    plt.title(f"Jira Tickets by Requesting Team - {month_name}", fontsize=14, fontweight="bold", pad=16)
    plt.suptitle(f"Coverage: {coverage:.1f}% | mapped share: {mapped_ratio:.1f}%", fontsize=10, y=1.02)
    plt.xlabel("Day of month", fontsize=11, fontweight="bold")
    plt.ylabel("Requesting team", fontsize=11, fontweight="bold")
    plt.tight_layout()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out = os.path.join(REPORTS_DIR, f"jira_requesting_team_heatmap_{year}_{month:02d}.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    return out


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Generate Jira ticket charts.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument(
        "--service-only",
        "--component-only",
        dest="service_only",
        action="store_true",
        help="Generate service heatmap only.",
    )
    parser.add_argument("--team-only", action="store_true", help="Generate requesting-team heatmap only.")
    args = parser.parse_args(argv)

    today = dt.date.today()
    year, month = parse_month(args.month) if args.month else (today.year, today.month)
    month_name = dt.date(year, month, 1).strftime("%B %Y")

    print(f"Generating Jira ticket charts for {month_name}...")
    date_anchor = load_heatmap_date_anchor()
    print(f"Date anchor: {date_anchor}")
    tickets = load_tickets(year, month, date_anchor=date_anchor)
    print(f"Loaded {len(tickets)} ticket(s)")

    derived_payload = derive_normalized_tickets(tickets, year, month, date_anchor=date_anchor)

    do_service = not args.team_only
    do_team = not args.service_only

    if do_service:
        print("\nService heatmap:")
        out = generate_service_heatmap(tickets, year, month, date_anchor=date_anchor)
        if out:
            print(f"  Saved: {out}")

    if do_team:
        print("\nRequesting team heatmap:")
        out = generate_team_heatmap(derived_payload, year, month)
        if out:
            print(f"  Saved: {out}")

    if not args.service_only and not args.team_only:
        print("\nFlow trend charts:")
        out = generate_flow_trend_chart(year, month, lookback_months=6)
        for path in (out or "").split(","):
            path = path.strip()
            if path:
                print(f"  Saved: {path}")

    print("\nDone.")


if __name__ == "__main__":
    main()