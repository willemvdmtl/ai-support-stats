#!/usr/bin/env python3
"""Remove generated outputs to restore a clean first-run state."""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from typing import Dict, List, Tuple

CACHE_TARGETS: List[str] = [
    "cache/github",
    "cache/jira",
]

REPORT_TARGETS: List[str] = [
    "reports",
]

# Only auto-generated config files are cleaned.
# Hand-maintained files (jira-team-normalization.json, jira-team-overrides.json)
# are intentionally excluded.
CONFIG_TARGETS: List[str] = [
    "config/capabilities.json",
    "config/owned-repositories.json",
    "config/github-minimal.json",
    "config/github-internal-team.json",
    "config/jira-minimal.json",
    "config/jira-org-structure.json",
    "config/jira-service-normalization.json",
    "data/config",
]


def collect_paths(targets: List[str]) -> List[Tuple[str, bool]]:
    return [(t, os.path.exists(t)) for t in targets]


def _archive_timestamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _copy_path(src: str, dest_root: str) -> None:
    dest = os.path.join(dest_root, src)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return
    shutil.copy2(src, dest)


def archive_paths(paths: List[str], archive_root: str, label: str) -> str:
    timestamp = _archive_timestamp()
    archive_dir = os.path.join(archive_root, label, timestamp)
    os.makedirs(archive_dir, exist_ok=True)

    present = [path for path in paths if os.path.exists(path)]
    for path in present:
        _copy_path(path, archive_dir)

    metadata: Dict[str, object] = {
        "archived_at": dt.datetime.utcnow().isoformat() + "Z",
        "targets": present,
    }
    metadata_file = os.path.join(archive_dir, "metadata.json")
    with open(metadata_file, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")

    return archive_dir


def remove_path(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.isfile(path):
        os.remove(path)


def remove_parent_if_empty(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)


def run_clean(targets: List[str], yes: bool, archive: bool, archive_root: str, archive_label: str) -> None:
    paths = collect_paths(targets)
    present = [(p, e) for p, e in paths if e]

    if not present:
        print("Nothing to clean.")
        return

    print("Will remove:")
    for path, _ in present:
        print(f"  {path}/")

    if archive:
        archive_targets = [path for path, _ in present]
        archive_dir = archive_paths(archive_targets, archive_root=archive_root, label=archive_label)
        print(f"\nArchived {len(archive_targets)} target(s) to: {archive_dir}")

    if not yes:
        answer = input("\nProceed? [y/N]: ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    for path, _ in present:
        remove_path(path)
        remove_parent_if_empty(path)
        print(f"  removed  {path}/")

    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove generated outputs.")
    parser.add_argument("--cache", action="store_true", help="Remove cache/ outputs")
    parser.add_argument("--config", action="store_true", help="Remove config/ outputs")
    parser.add_argument("--reports", action="store_true", help="Remove reports/ outputs")
    parser.add_argument("--all", action="store_true", help="Remove all generated outputs")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--archive", action="store_true", help="Archive selected targets before removal")
    parser.add_argument("--archive-root", default="archives", help="Archive root directory (default: archives)")
    parser.add_argument("--archive-label", default="pre-clean", help="Archive label path under archive root")
    args = parser.parse_args()

    if not any([args.cache, args.config, args.reports, args.all]):
        parser.print_help()
        sys.exit(0)

    targets: List[str] = []
    if args.cache or args.all:
        targets.extend(CACHE_TARGETS)
    if args.config or args.all:
        targets.extend(CONFIG_TARGETS)
    if args.reports or args.all:
        targets.extend(REPORT_TARGETS)

    run_clean(
        targets,
        yes=args.yes,
        archive=args.archive,
        archive_root=args.archive_root,
        archive_label=args.archive_label,
    )


if __name__ == "__main__":
    main()
