#!/usr/bin/env python3
"""Generate a simple monthly markdown report with cycle time statn and chart embeds."""

import argparse
import datetime as dt
import os
import statistics
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


def jira_cycle_time_days(year: int, month: int) -> Optional[float]:
    path = jira_consolidated_file(year, month)
    if not os.path.exists(path):
        return None

    payload = read_json(path)
    tickets: List[Dict] = payload.get("tickets", [])
    if not tickets:
        return None

    durations_days: List[float] = []

    for ticket in tickets:
        fields = ticket.get("fields") if isinstance(ticket.get("fields"), dict) else {}
        created = parse_jira_datetime(str(fields.get("created") or ""))
        resolution = parse_jira_datetime(str(fields.get("resolutiondate") or ""))
        if created is None or resolution is None:
            continue

        delta = (resolution - created).total_seconds()
        if delta < 0:
            continue
        durations_days.append(delta / 86400.0)

    if not durations_days:
        return None

    # One stable headline metric: median cycle time for resolved tickets.
    median_days = statistics.median(durations_days)
    return median_days


def image_block(path: str, alt_text: str, report_dir: str) -> str:
    if os.path.exists(path):
        rel_path = os.path.relpath(path, start=report_dir or ".").replace(os.sep, "/")
        return f"![{alt_text}]({rel_path})"
    return f"_Not available: {path}_"


def build_report(year: int, month: int, report_dir: str) -> str:
    month_label = dt.date(year, month, 1).strftime("%B %Y")
    charts = chart_files(year, month)

    cycle_time = jira_cycle_time_days(year, month)

    if cycle_time is None:
        cycle_stat_line = "**Cycle Time (median):** unavailable (no Jira ticket data with resolution timestamps)"
    else:
        cycle_stat_line = (
            f"**Cycle Time (median):** {cycle_time:.2f} days"
        )

    lines = [
        f"# Support Stats - {month_label}",
        "",
        cycle_stat_line,
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
