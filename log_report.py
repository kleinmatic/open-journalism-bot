#!/usr/bin/env python3
"""Parse bot logs and display a report of recent checks."""

import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

# Two summary line formats the bot has used:
# Old: "Done. Checked X organizations, found Y new repos."
# New: "Done. Checked X orgs. New: Y, held back (empty): Z, rechecked: R, recovered: V, posted: P."
OLD_DONE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INFO: "
    r"Done\. Checked (\d+) organizations, found (\d+) new repos\.$"
)
NEW_DONE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INFO: "
    r"Done\. Checked (\d+) orgs\. "
    r"New: (\d+), held back \(empty\): (\d+), "
    r"rechecked: (\d+), recovered: (\d+), "
    r"posted: (\d+)\.$"
)
WARNING_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) WARNING: (.*)$"
)
ERROR_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ERROR: (.*)$"
)
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


CHECKING_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INFO: Checking \d+ organizations for"
)


def parse_log(log_path, cutoff):
    """Parse log file and return list of check results after cutoff."""
    checks = []
    # Track warnings/errors between "Done" lines
    current_warnings = []
    current_errors = []
    run_start = None

    for line in open(log_path):
        line = line.rstrip()

        # Skip lines before cutoff (quick check on timestamp)
        ts_match = TIMESTAMP_RE.match(line)
        if ts_match:
            try:
                ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                if ts < cutoff:
                    current_warnings.clear()
                    current_errors.clear()
                    run_start = None
                    continue
            except ValueError:
                continue

        # Track run start time
        cm = CHECKING_RE.match(line)
        if cm:
            run_start = datetime.strptime(cm.group(1), "%Y-%m-%d %H:%M:%S")

        # Collect warnings (skip noisy ones)
        wm = WARNING_RE.match(line)
        if wm:
            msg = wm.group(2)
            if "without auth" not in msg:
                current_warnings.append(msg)
            continue

        em = ERROR_RE.match(line)
        if em:
            current_errors.append(em.group(2))
            continue

        # Try new format first
        m = NEW_DONE_RE.match(line)
        if m:
            done_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            duration = (done_time - run_start).total_seconds() if run_start else None
            checks.append({
                "time": done_time,
                "orgs": int(m.group(2)),
                "new": int(m.group(3)),
                "empty": int(m.group(4)),
                "rechecked": int(m.group(5)),
                "recovered": int(m.group(6)),
                "posted": int(m.group(7)),
                "duration": duration,
                "warnings": current_warnings[:],
                "errors": current_errors[:],
            })
            current_warnings.clear()
            current_errors.clear()
            run_start = None
            continue

        # Try old format
        m = OLD_DONE_RE.match(line)
        if m:
            done_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            duration = (done_time - run_start).total_seconds() if run_start else None
            checks.append({
                "time": done_time,
                "orgs": int(m.group(2)),
                "new": int(m.group(3)),
                "empty": 0,
                "rechecked": 0,
                "recovered": 0,
                "posted": int(m.group(3)),  # old format posted everything found
                "duration": duration,
                "warnings": current_warnings[:],
                "errors": current_errors[:],
            })
            current_warnings.clear()
            current_errors.clear()
            run_start = None
            continue

    return checks


def print_report(checks, hours):
    print(f"\nBot Check Report — last {hours}h ({len(checks)} runs)")
    print("=" * 46)

    if not checks:
        print("No checks found in this time range.")
        return

    # Table header
    hdr = f"{'Time':>16} {'Dur':>5} {'Orgs':>4} {'New':>3} {'Empty':>5} {'Rechk':>5} {'Recov':>5} {'Posted':>6}"
    print(hdr)
    print("-" * len(hdr))

    total_new = 0
    total_posted = 0
    total_empty = 0
    total_rechecked = 0
    total_recovered = 0
    notes_out = []

    for i, c in enumerate(checks, 1):
        total_new += c["new"]
        total_posted += c["posted"]
        total_empty += c["empty"]
        total_rechecked += c["rechecked"]
        total_recovered += c["recovered"]

        # Highlight rows with activity
        marker = "*" if (c["new"] or c["posted"] or c["errors"]) else " "
        time_str = c["time"].strftime("%b %d %H:%M:%S")

        if c["duration"] is not None:
            mins, secs = divmod(int(c["duration"]), 60)
            dur_str = f"{mins}:{secs:02d}"
        else:
            dur_str = "-"

        print(
            f"{marker}{time_str:>16} {dur_str:>5} {c['orgs']:>4} {c['new']:>3} "
            f"{c['empty']:>5} {c['rechecked']:>5} {c['recovered']:>5} {c['posted']:>6}"
        )

        # Collect notes to print at the end
        if c["errors"]:
            for e in c["errors"]:
                notes_out.append(f"  {c['time'].strftime('%H:%M')} ERR: {e}")
        if c["warnings"]:
            for w in c["warnings"]:
                notes_out.append(f"  {c['time'].strftime('%H:%M')} WARN: {w}")

    print("-" * len(hdr))
    print(f"{'TOTAL':>16} {'':>5} {'':>4} {total_new:>3} {total_empty:>5} {total_rechecked:>5} {total_recovered:>5} {total_posted:>6}")

    if notes_out:
        print(f"\nNotes:")
        for n in notes_out:
            print(n)

    print()


def main():
    parser = argparse.ArgumentParser(description="Report on bot check history from logs")
    parser.add_argument(
        "--hours", type=int, default=24,
        help="How many hours back to report (default: 24)"
    )
    parser.add_argument(
        "--log", type=Path,
        default=Path(__file__).parent / "logs" / "bot.log",
        help="Path to bot.log",
    )
    args = parser.parse_args()

    if not args.log.exists():
        print(f"Log file not found: {args.log}")
        return

    cutoff = datetime.now() - timedelta(hours=args.hours)
    checks = parse_log(args.log, cutoff)
    print_report(checks, args.hours)


if __name__ == "__main__":
    main()
