#!/usr/bin/env python3
"""Generate GitHub PR charts for a given month."""

import argparse
import calendar
import datetime as dt
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from common.palette import PALETTE, build_colormap
from common.setup_utils import read_json

REPORTS_DIR = "reports"
CONSOLIDATED_DIR = "cache/github"
INTERNAL_TEAM_FILE = "config/github-internal-team.json"


def consolidated_file(year: int, month: int) -> str:
    return os.path.join(CONSOLIDATED_DIR, f"prs-{year}-{month:02d}.json")


def load_prs(year: int, month: int) -> List[Dict]:
    path = consolidated_file(year, month)
    if not os.path.exists(path):
        print(f"ERROR: No consolidated data found at {path}")
        print(f"Run first:  python3 scripts/fetch-data.py github --month {year}-{month:02d}")
        sys.exit(1)
    data = read_json(path)
    return data.get("pull_requests", [])


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


def pr_created_day(pr: Dict) -> Optional[int]:
    raw = pr.get("created_at", "")
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").day
    except ValueError:
        return None


def repo_short(pr: Dict) -> str:
    full = pr.get("repository_url", "")
    if "/repos/" in full:
        name = full.split("/repos/", 1)[1]
        return name.split("/", 1)[1] if "/" in name else name
    html = pr.get("html_url", "")
    parts = html.replace("https://github.com/", "").split("/pull/")
    if len(parts) == 2:
        return parts[0].split("/", 1)[1] if "/" in parts[0] else parts[0]
    return "unknown"


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


def generate_heatmap(prs: List[Dict], year: int, month: int) -> str:
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

    repo_day: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    repo_total: Dict[str, int] = defaultdict(int)

    for pr in prs:
        day = pr_created_day(pr)
        if day is None or day > last_day:
            continue
        repo = repo_short(pr)
        repo_day[repo][day] += 1
        repo_total[repo] += 1

    if not repo_total:
        print(f"  No PRs found for {month_name}. Skipping heatmap.")
        return ""

    repos = sorted(repo_total, key=lambda r: repo_total[r], reverse=True)
    day_cols = [str(d) for d in range(1, days_in_month + 1)]

    data_rows = []
    for repo in repos:
        row = [
            repo_day[repo][d] if d <= last_day else np.nan
            for d in range(1, days_in_month + 1)
        ]
        row.append(repo_total[repo])
        data_rows.append(row)

    day_totals = [
        sum(repo_day[r][d] for r in repos) if d <= last_day else np.nan
        for d in range(1, days_in_month + 1)
    ]
    day_totals.append(sum(repo_total.values()))
    data_rows.append(day_totals)

    df = pd.DataFrame(
        data_rows,
        index=repos + ["Total"],
        columns=day_cols + ["Total"],
    )

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

    fig_height = max(4, (len(repos) + 1) * 0.5 + 1.5)
    fig, ax = plt.subplots(figsize=(17, fig_height))

    sns.heatmap(
        df,
        annot=annot,
        fmt="",
        cmap=build_colormap("sky_purple"),
        cbar_kws={"label": "PRs (daily cells)"},
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

    ax.hlines([len(repos)], *ax.get_xlim(), colors="black", linewidths=2.5)
    ax.vlines([days_in_month], *ax.get_ylim(), colors="black", linewidths=2.5)

    for label in ax.get_yticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")
    for label in ax.get_xticklabels():
        if label.get_text() == "Total":
            label.set_fontweight("bold")

    plt.title(f"GitHub Pull Requests by Repository - {month_name}", fontsize=14, fontweight="bold", pad=16)
    plt.xlabel("Day of month", fontsize=11, fontweight="bold")
    plt.ylabel("Repository", fontsize=11, fontweight="bold")
    plt.tight_layout()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out = os.path.join(REPORTS_DIR, f"github_pr_heatmap_{year}_{month:02d}.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    return out


def generate_split_bar(prs: List[Dict], year: int, month: int, internal_teams: Dict[str, List[str]]) -> str:
    import matplotlib.pyplot as plt

    month_name = dt.date(year, month, 1).strftime("%B %Y")

    if not internal_teams:
        print("  No internal users configured. Skipping internal/external chart.")
        print("  Run:  python3 scripts/setup.py --capabilities 2")
        return ""

    login_to_team: Dict[str, str] = {}
    for team_name, users in internal_teams.items():
        for user in users:
            key = user.lower()
            if key in login_to_team and login_to_team[key] != team_name:
                print(
                    "  WARNING: "
                    f"'{user}' appears in multiple teams; using first team '{login_to_team[key]}'."
                )
                continue
            login_to_team[key] = team_name

    split = classify_pr_split(prs, internal_teams)
    team_counts = split["team_counts"]
    internal_count = int(split["internal_count"])
    external_count = int(split["external_count"])
    unknown_count = int(split["unknown_count"])
    total = int(split["total"])
    if total == 0:
        print(f"  No PRs found for {month_name}. Skipping split chart.")
        return ""

    print(f"  Internal: {internal_count} ({internal_count / total * 100:.1f}%)")
    for team_name, count in sorted(team_counts.items(), key=lambda item: item[1], reverse=True):
        if count:
            print(f"    - {team_name}: {count} ({count / total * 100:.1f}%)")
    print(f"  External: {external_count} ({external_count / total * 100:.1f}%)")
    if unknown_count:
        print(f"  Unknown author: {unknown_count}")

    colours = [PALETTE["sky"], PALETTE["teal"], PALETTE["purple"], PALETTE["peach"]]
    segments = []
    for index, (team_name, count) in enumerate(sorted(team_counts.items(), key=lambda item: item[1], reverse=True)):
        segments.append((f"Internal ({team_name})", count, colours[index % len(colours)]))
    segments.append(("External", external_count, PALETTE["mint"]))
    segments.sort(key=lambda s: s[1], reverse=True)

    fig, ax = plt.subplots(figsize=(10, 2.8))
    left = 0
    for label, count, colour in segments:
        ax.barh(["PRs"], [count], left=[left], color=colour, label=label)
        if count > 0:
            text_colour = "white" if colour in {PALETTE["sky"], PALETTE["teal"], PALETTE["purple"]} else "black"
            ax.text(left + count / 2, 0, str(count), va="center", ha="center", color=text_colour, fontweight="bold")
        left += count

    if unknown_count:
        ax.barh(["PRs"], [unknown_count], left=[left], color="#cccccc", label="Unknown")
        ax.text(left + unknown_count / 2, 0, str(unknown_count), va="center", ha="center", color="black", fontweight="bold")

    ax.set_title(f"External vs Internal GitHub PRs - {month_name}", fontsize=13, fontweight="bold")
    ax.set_xlim(0, max(total, 1))
    ax.set_xticks([])
    ax.grid(False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.45), ncol=3, frameon=False)

    plt.tight_layout()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    out = os.path.join(REPORTS_DIR, f"github_pr_internal_external_{year}_{month:02d}.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    return out


def parse_month(raw: str) -> Tuple[int, int]:
    try:
        d = dt.datetime.strptime(raw, "%Y-%m")
        return d.year, d.month
    except ValueError:
        print(f"ERROR: Invalid month format '{raw}'. Use YYYY-MM")
        sys.exit(1)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Generate GitHub PR charts.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument("--heatmap-only", action="store_true", help="Generate repository heatmap only.")
    parser.add_argument("--split-only", action="store_true", help="Generate internal/external chart only.")
    args = parser.parse_args(argv)

    today = dt.date.today()
    year, month = parse_month(args.month) if args.month else (today.year, today.month)
    month_name = dt.date(year, month, 1).strftime("%B %Y")

    print(f"Generating GitHub PR charts for {month_name}...")
    prs = load_prs(year, month)
    print(f"Loaded {len(prs)} PR(s)")

    do_heatmap = not args.split_only
    do_split = not args.heatmap_only

    if do_heatmap:
        print("\nHeatmap:")
        out = generate_heatmap(prs, year, month)
        if out:
            print(f"  Saved: {out}")

    if do_split:
        print("\nInternal / external split:")
        internal_teams = load_internal_teams()
        out = generate_split_bar(prs, year, month, internal_teams)
        if out:
            print(f"  Saved: {out}")

    print("\nDone.")


if __name__ == "__main__":
    main()
