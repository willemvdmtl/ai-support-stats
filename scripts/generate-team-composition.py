#!/usr/bin/env python3
"""
Generate monthly team-composition snapshots from Jira assignee data.

Usage:
  # Process a single month (shows diff vs previous month):
  python3 scripts/generate-team-composition.py --month 2024-05

  # Process all manifest months (skips already-written files):
  python3 scripts/generate-team-composition.py --all

  # Force re-write even if the output file already exists:
  python3 scripts/generate-team-composition.py --month 2024-05 --force
  python3 scripts/generate-team-composition.py --all --force

  # Build the consolidated first/last-seen timeline from saved snapshots:
  python3 scripts/generate-team-composition.py --timeline
"""

import argparse
import datetime as dt
import json
import sys
from typing import Optional

from generate.team_composition import (
    build_assignment_timeline,
    build_timeline,
    build_membership_summary,
    build_monthly_roster_from_summary,
    diff,
    extract_assignees,
    load_saved,
    load_corrections,
    manifest_months,
    write_month,
    write_assignment_timeline_excel,
    write_team_split_excel,
    write_timeline,
    write_timeline_svg,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate monthly team-composition snapshots from Jira assignees."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--month", metavar="YYYY-MM", help="Process a single month")
    group.add_argument(
        "--all", action="store_true", help="Process all months in the Jira manifest"
    )
    group.add_argument(
        "--timeline",
        action="store_true",
        help="Build consolidated first/last-seen timeline from saved snapshots",
    )
    group.add_argument(
        "--visual",
        action="store_true",
        help="Build an SVG timeline from assignment changelog data",
    )
    group.add_argument(
        "--summary",
        action="store_true",
        help="Build a month-by-month membership summary using inactivity rules",
    )
    group.add_argument(
        "--excel",
        action="store_true",
        help="Build Excel workbook from assignment changelog timeline",
    )
    group.add_argument(
        "--team-split",
        action="store_true",
        help="Build separate Excel file with fixed vs floating headcount by month",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-write output file even if it already exists",
    )
    return parser.parse_args(argv)


def validate_month(raw: str) -> str:
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
        return f"{parsed.year}-{parsed.month:02d}"
    except ValueError:
        print(f"ERROR: Invalid --month value '{raw}'. Use YYYY-MM")
        sys.exit(1)


def process_month(month: str, prev_month: Optional[str], force: bool) -> bool:
    """
    Extract assignees for a month, print a diff, and write the output file.
    Returns True if the file was written (new or forced), False if skipped.
    """
    from generate.team_composition import OUTPUT_DIR
    import os

    output_path = os.path.join(OUTPUT_DIR, f"assignees-{month}.json")
    if os.path.exists(output_path) and not force:
        print(f"  {month}  skipped (already exists — use --force to overwrite)")
        return False

    assignees = extract_assignees(month)
    if assignees is None:
        print(f"  {month}  skipped (no cache file found)")
        return False

    prev = load_saved(prev_month) if prev_month else None
    new_members, gone_members = diff(prev, assignees)

    write_month(month, assignees)

    tag = "(forced)" if force and os.path.exists(output_path) else ""
    print(f"  {month}  {len(assignees)} assignees  {tag}")
    if new_members:
        for p in new_members:
            print(f"    + {p['displayName']} ({p['emailAddress']})")
    if gone_members:
        for p in gone_members:
            print(f"    - {p['displayName']} ({p['emailAddress']})")

    return True


def main(argv=None) -> None:
    args = parse_args(argv)

    if args.month:
        month = validate_month(args.month)
        months = manifest_months()
        idx = months.index(month) if month in months else -1
        prev_month = months[idx - 1] if idx > 0 else None
        print(f"Team composition — {month}")
        process_month(month, prev_month, args.force)
        return

    if args.timeline:
        months = manifest_months()
        if not months:
            print("ERROR: No months found in Jira manifest.")
            sys.exit(1)
        timeline = build_timeline(months)
        path = write_timeline(months, timeline)
        print(f"Timeline — {len(timeline)} members ({months[0]} → {months[-1]})")
        last_month = months[-1]
        for m in timeline:
            status = "current" if m["last_seen"] == last_month else f"last seen {m['last_seen']}"
            print(f"  {m['displayName']:35s}  joined {m['first_seen']}  ({status})  [{len(m['months_active'])} months active]")
        print(f"\nWritten: {path}")
        return

    if args.visual:
        months = manifest_months()
        if not months:
            print("ERROR: No months found in Jira manifest.")
            sys.exit(1)
        corrections = load_corrections()
        excluded = set(corrections.get("exclude_names") or [])
        assignment_cfg = corrections.get("assignment_timeline") or {}
        min_events = int(assignment_cfg.get("min_events") or 5)
        member_timeline = build_assignment_timeline(
            months,
            exclude_names=excluded,
            min_events=min_events,
            corrections=corrections,
        )
        if not member_timeline:
            print("ERROR: No assignment changelog data found for visual generation.")
            sys.exit(1)
        excluded_hint = ", ".join(sorted(excluded)) if excluded else "none"
        summary = build_membership_summary(
            months + ["2026-07"],
            exclude_names=excluded,
            inactivity_threshold=3,
            corrections=corrections,
        )
        monthly_rows = build_monthly_roster_from_summary(summary)
        member_names = [member["displayName"] for member in member_timeline]
        timeline = []
        for name in member_names:
            active_months = [row["month"] for row in monthly_rows if name in row["roster"]]
            if not active_months:
                continue
            timeline.append(
                {
                    "displayName": name,
                    "first_seen": active_months[0],
                    "last_seen": active_months[-1],
                    "months_active": active_months,
                    "events": next((member["events"] for member in member_timeline if member["displayName"] == name), 0),
                }
            )
        path = write_timeline_svg(
            months,
            timeline,
            title="Assignment Timeline",
            subtitle=(
                f"Assignee-change events by month from {months[0]} to {months[-1]} "
                f"(excluded: {excluded_hint}; min {min_events} assignments)"
            ),
            monthly_rows=monthly_rows,
        )
        print(
            f"Visual timeline — {len(timeline)} members ({months[0]} → {months[-1]})"
        )
        print(f"Written: {path}")
        return

    if args.summary:
        months = manifest_months()
        if not months:
            print("ERROR: No months found in Jira manifest.")
            sys.exit(1)
        corrections = load_corrections()
        summary = build_membership_summary(
            months,
            exclude_names=set(corrections.get("exclude_names") or []),
            inactivity_threshold=3,
            corrections=corrections,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    if args.excel:
        months = manifest_months()
        if not months:
            print("ERROR: No months found in Jira manifest.")
            sys.exit(1)
        corrections = load_corrections()
        excluded = set(corrections.get("exclude_names") or [])
        assignment_cfg = corrections.get("assignment_timeline") or {}
        min_events = int(assignment_cfg.get("min_events") or 5)
        timeline = build_assignment_timeline(
            months,
            exclude_names=excluded,
            min_events=min_events,
            corrections=corrections,
        )
        if not timeline:
            print("ERROR: No assignment changelog data found for Excel generation.")
            sys.exit(1)
        summary = build_membership_summary(
            months + ["2026-07"],
            exclude_names=excluded,
            inactivity_threshold=3,
            corrections=corrections,
        )
        monthly_rows = build_monthly_roster_from_summary(summary)
        filtered_timeline = []
        for member in timeline:
            name = member["displayName"]
            active_months = [row["month"] for row in monthly_rows if name in row["roster"]]
            if not active_months:
                continue
            filtered_timeline.append(member)
        try:
            path = write_assignment_timeline_excel(months, filtered_timeline)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            print("Fix: install openpyxl (pip install openpyxl)")
            sys.exit(1)
        print(f"Excel timeline — {len(filtered_timeline)} members ({months[0]} → {months[-1]})")
        print(f"Written: {path}")
        return

    if args.team_split:
        months = manifest_months()
        if not months:
            print("ERROR: No months found in Jira manifest.")
            sys.exit(1)
        try:
            path = write_team_split_excel(months)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            print("Fix: install openpyxl (pip install openpyxl)")
            sys.exit(1)
        print(f"Fixed/Floating split — {months[0]} → {months[-1]}")
        print(f"Written: {path}")
        return

    # --all
    months = manifest_months()
    if not months:
        print("ERROR: No months found in Jira manifest.")
        sys.exit(1)

    print(f"Team composition — {len(months)} months ({months[0]} → {months[-1]})")
    written = 0
    for i, month in enumerate(months):
        prev_month = months[i - 1] if i > 0 else None
        if process_month(month, prev_month, args.force):
            written += 1

    print(f"\nDone. {written} file(s) written to reports/team-composition/")


if __name__ == "__main__":
    main(sys.argv[1:])
