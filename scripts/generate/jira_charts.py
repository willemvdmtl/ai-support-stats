#!/usr/bin/env python3
"""Generate Jira ticket charts for a given month."""

import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from common.palette import build_colormap
from common.setup_utils import read_json

REPORTS_DIR = "reports"
CONSOLIDATED_DIR = "cache/jira"
TEAM_NORMALIZATION_FILE = "config/jira-team-normalization.json"
ORG_STRUCTURE_FILE = "config/jira-org-structure.json"
OVERRIDES_FILE = "config/jira-team-overrides.json"
SERVICE_NORMALIZATION_FILE = "config/jira-service-normalization.json"
SERVICE_OVERRIDES_FILE = "config/jira-service-overrides.json"
JIRA_CONFIG_FILE = "config/jira-minimal.json"
DEFAULT_BASIC_GROUP_FIELDS: List[Tuple[str, str]] = [
    ("customfield_16011", "Service Name"),
]


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"issues-{year}-{month:02d}.json")


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


def load_tickets(year: int, month: int) -> List[Dict]:
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
    return [
        ticket
        for ticket in selected
        if str(ticket_fields(ticket).get("created") or "").startswith(target_month)
    ]


def ticket_fields(ticket: Dict) -> Dict:
    fields = ticket.get("fields")
    return fields if isinstance(fields, dict) else {}


def ticket_created_day(ticket: Dict) -> Optional[int]:
    created = ticket_fields(ticket).get("created", "")
    if not created:
        return None
    try:
        return dt.datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S").day
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
    key = raw.strip().lower()
    if not key:
        return raw

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
        return raw.strip()

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

    return raw.strip()


def derive_normalized_tickets(tickets: List[Dict], year: int, month: int) -> Dict:
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


def generate_service_heatmap(tickets: List[Dict], year: int, month: int) -> str:
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
        day = ticket_created_day(ticket)
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

    sns.heatmap(
        df,
        cmap=ListedColormap(["#d0d0d0"]),
        cbar=False,
        linewidths=0.5,
        linecolor="lightgray",
        annot=False,
        mask=~(weekend_mask & ~df.isna()),
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

    plt.title(f"Jira Tickets by {dominant_source} - {month_name}", fontsize=14, fontweight="bold", pad=16)
    plt.xlabel("Day of month", fontsize=11, fontweight="bold")
    plt.ylabel(dominant_source, fontsize=11, fontweight="bold")
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
        created_at = ticket.get("created_at", "")
        if not created_at:
            continue
        try:
            day = dt.datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S").day
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

    sns.heatmap(
        df,
        cmap=ListedColormap(["#d0d0d0"]),
        cbar=False,
        linewidths=0.5,
        linecolor="lightgray",
        annot=False,
        mask=~(weekend_mask & ~df.isna()),
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
    tickets = load_tickets(year, month)
    print(f"Loaded {len(tickets)} ticket(s)")

    derived_payload = derive_normalized_tickets(tickets, year, month)

    do_service = not args.team_only
    do_team = not args.service_only

    if do_service:
        print("\nService heatmap:")
        out = generate_service_heatmap(tickets, year, month)
        if out:
            print(f"  Saved: {out}")

    if do_team:
        print("\nRequesting team heatmap:")
        out = generate_team_heatmap(derived_payload, year, month)
        if out:
            print(f"  Saved: {out}")

    print("\nDone.")


if __name__ == "__main__":
    main()