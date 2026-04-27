#!/usr/bin/env python3
"""Generate Jira ticket charts for a given month."""

import argparse
import calendar
import datetime as dt
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from common.palette import build_colormap
from common.setup_utils import read_json, write_json

REPORTS_DIR = "reports"
CONSOLIDATED_DIR = "cache/jira"
DERIVED_DIR = "cache/jira/derived"
TEAM_NORMALIZATION_FILE = "config/jira-team-normalization.json"
ORG_STRUCTURE_FILE = "config/jira-org-structure.json"
OVERRIDES_FILE = "config/jira-team-overrides.json"
DEFAULT_BASIC_GROUP_FIELDS: List[Tuple[str, str]] = [
    ("customfield_16011", "Service Name"),
]


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"tickets-{year}-{month:02d}.json")


def derived_file(year: int, month: int) -> str:
    return os.path.join(DERIVED_DIR, f"tickets-{year}-{month:02d}.normalized.json")


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
        return parsed.year, parsed.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def load_tickets(year: int, month: int) -> List[Dict]:
    path = consolidated_file(year, month)
    if not os.path.exists(path):
        print(f"ERROR: No consolidated Jira data found at {path}")
        print(f"Run first:  python3 scripts/fetch-data.py jira --month {year}-{month:02d}")
        sys.exit(1)
    data = read_json(path)
    return data.get("tickets", [])


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

    return ["Unassigned"], "Unassigned"


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


def _norm_key(s: str) -> str:
    """Normalize a string for fuzzy matching: lowercase, collapse separators to spaces."""
    s = s.lower().strip()
    s = s.replace('&', 'and').replace('-', ' ').replace('_', ' ')
    s = _NORM_NONALNUM_RE.sub('', s)
    return re.sub(r'\s+', ' ', s).strip()


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


def _org_team_map(org_config: Dict) -> Dict[str, Dict[str, str]]:
    teams = org_config.get("teams") or {}
    mapped: Dict[str, Dict[str, str]] = {}
    if not isinstance(teams, dict):
        return mapped
    for canonical, details in teams.items():
        canonical_name = str(canonical).strip()
        if not canonical_name:
            continue
        if isinstance(details, str):
            mapped[canonical_name] = {"display_name": canonical_name, "area": details.strip()}
        elif isinstance(details, dict):
            mapped[canonical_name] = {
                "display_name": str(details.get("display_name") or canonical_name).strip(),
                "area": str(details.get("area") or "").strip(),
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
        effective_team = canonical_team or (deduped_values[0] if deduped_values else "")
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
                "requesting_team": canonical_team,
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

    os.makedirs(DERIVED_DIR, exist_ok=True)
    write_json(derived_file(year, month), payload)
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
            component_day[component][day] += 1
            component_total[component] += 1

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
    print(f"Wrote derived ticket file: {derived_file(year, month)}")

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