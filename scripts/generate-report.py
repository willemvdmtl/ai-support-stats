#!/usr/bin/env python3
"""Generate a simple monthly markdown report with cycle time statn and chart embeds."""

import argparse
import datetime as dt
import os
import sys
from typing import Dict, List, Optional, Tuple

from common.setup_utils import read_json

REPORTS_DIR = "reports"
GITHUB_CACHE_DIR = "cache/github"
JIRA_CACHE_DIR = "cache/jira"


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
        return parsed.year, parsed.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def github_consolidated_file(year: int, month: int) -> str:
    return os.path.join(GITHUB_CACHE_DIR, f"prs-{year}-{month:02d}.json")


def jira_consolidated_file(year: int, month: int) -> str:
    return os.path.join(JIRA_CACHE_DIR, f"tickets-{year}-{month:02d}.json")


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


def jira_cycle_stats(year: int, month: int) -> Tuple[Optional[float], Optional[Dict[str, object]]]:
    durations_days, longest_wait = jira_cycle_stats_in_window(
        *month_window(year, month),
    )

    average_days = (sum(durations_days) / len(durations_days)) if durations_days else None
    return average_days, longest_wait


def month_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    target_month_start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        target_month_end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        target_month_end = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)

    return target_month_start, target_month_end


def rolling_2w_window(year: int, month: int) -> Tuple[dt.datetime, dt.datetime]:
    today_utc = dt.datetime.now(dt.timezone.utc)
    month_start, month_end = month_window(year, month)
    window_end = min(today_utc, month_end)
    window_start = window_end - dt.timedelta(days=14)
    return window_start, window_end


def all_jira_tickets() -> List[Dict]:
    if not os.path.exists(JIRA_CACHE_DIR):
        return []

    tickets_by_key: Dict[str, Dict] = {}
    for name in sorted(os.listdir(JIRA_CACHE_DIR)):
        if not name.startswith("tickets-") or not name.endswith(".json"):
            continue
        path = os.path.join(JIRA_CACHE_DIR, name)
        payload = read_json(path)
        for ticket in payload.get("tickets", []):
            key = str(ticket.get("key") or "")
            if not key:
                continue
            tickets_by_key[key] = ticket

    return list(tickets_by_key.values())


def jira_cycle_stats_in_window(
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> Tuple[List[float], Optional[Dict[str, object]]]:
    durations_days: List[float] = []
    longest_wait: Optional[Dict[str, object]] = None

    for ticket in all_jira_tickets():
        fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
        created = parse_jira_datetime(str(fields.get("created") or ""))
        if created is None:
            continue

        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if resolution is None:
            continue

        resolution_utc = resolution.astimezone(dt.timezone.utc)
        if not (window_start <= resolution_utc < window_end):
            continue

        delta = (resolution - created).total_seconds()
        if delta < 0:
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

    cycle_time, longest_wait = jira_cycle_stats(year, month)
    rolling_durations, _ = jira_cycle_stats_in_window(*rolling_2w_window(year, month))
    rolling_cycle_time = (sum(rolling_durations) / len(rolling_durations)) if rolling_durations else None

    if cycle_time is None:
        cycle_stat_line = "**Cycle Time (average):** unavailable (no Jira ticket data with resolution timestamps)"
    else:
        cycle_stat_line = f"**Cycle Time (average):** {cycle_time:.2f} days"

    if rolling_cycle_time is None:
        rolling_cycle_line = "**Cycle Time (2-week rolling):** unavailable"
    else:
        rolling_cycle_line = f"**Cycle Time (2-week rolling):** {rolling_cycle_time:.2f} days"

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
        cycle_stat_line,
        "",
        rolling_cycle_line,
        "",
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
    ]

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
