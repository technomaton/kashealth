#!/usr/bin/env python3
"""
EDPA CW Heuristics Evaluator — LOCKED FILE

DO NOT EDIT evaluation logic casually. This is the objective function for
auto-calibration. Separation of optimizer from objective prevents gaming.
If you need to change evaluation criteria, create a new version.

Usage:
    python evaluate_cw.py --ground-truth .edpa/data/ground_truth.yaml --heuristics .edpa/config/heuristics.yaml
    python evaluate_cw.py --ground-truth .edpa/data/ground_truth.yaml --heuristics .edpa/config/heuristics.yaml --verbose
    python evaluate_cw.py --ground-truth .edpa/data/ground_truth.yaml --heuristics .edpa/config/heuristics.yaml --per-role

Ground truth format (.edpa/data/ground_truth.yaml):
    records:
      - person: urbanek
        role: Arch
        item: S-200
        evidence_role: reviewer     # auto-detected evidence level
        confirmed_cw: 0.30          # team-confirmed CW from retro
        iteration: PI-2026-1.1
"""

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml")
    sys.exit(1)


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def get_auto_cw(evidence_role, person_role, heuristics):
    """Map evidence role to CW using current heuristics, with role_overrides."""
    # Check role_overrides first (v2.0+)
    overrides = heuristics.get("role_overrides", {})
    if person_role and person_role in overrides:
        role_cw = overrides[person_role].get(evidence_role)
        if role_cw is not None:
            return role_cw

    # Fallback to generic role_weights
    role_weights = heuristics.get("role_weights", {})
    return role_weights.get(evidence_role, 0.15)


def evaluate(ground_truth_path, heuristics_path, verbose=False, per_role=False):
    """Calculate Mean Absolute Deviation between auto CW and confirmed CW."""
    gt = load_yaml(ground_truth_path)
    heuristics = load_yaml(heuristics_path)

    records = gt.get("records", [])
    if len(records) < 20:
        print(f"ERROR: Insufficient ground truth. Need >= 20, got {len(records)}")
        sys.exit(1)

    total_deviation = 0.0
    count = 0
    role_stats = {}  # role → {total_dev, count, corrections}

    for record in records:
        person_role = record.get("role", "")
        evidence_role = record["evidence_role"]
        confirmed_cw = record["confirmed_cw"]

        auto_cw = get_auto_cw(evidence_role, person_role, heuristics)
        deviation = abs(auto_cw - confirmed_cw)
        total_deviation += deviation
        count += 1

        # Per-role tracking
        if person_role not in role_stats:
            role_stats[person_role] = {"total_dev": 0, "count": 0, "corrections": 0}
        role_stats[person_role]["total_dev"] += deviation
        role_stats[person_role]["count"] += 1
        if deviation > 0.01:
            role_stats[person_role]["corrections"] += 1

        if verbose:
            direction = "↑" if auto_cw < confirmed_cw else ("↓" if auto_cw > confirmed_cw else "=")
            print(f"  {record.get('person', '?'):<15} {person_role:<8} "
                  f"{evidence_role:<12} auto={auto_cw:.2f} confirmed={confirmed_cw:.2f} "
                  f"dev={deviation:.3f} {direction}")

    mad = total_deviation / count if count > 0 else 0.0

    # Output format expected by autocalib skill
    print(f"MAD={mad:.6f}")
    print(f"RECORDS={count}")
    print(f"TOTAL_DEVIATION={total_deviation:.6f}")

    # Verdict
    if mad < 0.02:
        verdict = "EXCELLENT"
    elif mad < 0.05:
        verdict = "GOOD"
    elif mad < 0.10:
        verdict = "ACCEPTABLE"
    else:
        verdict = "NEEDS_CALIBRATION"
    print(f"VERDICT={verdict}")

    # Per-role breakdown
    if per_role and role_stats:
        print(f"\n{'Role':<12} {'MAD':>8} {'Records':>8} {'Corrections':>12} {'Rate':>8}")
        print("-" * 52)
        for role in sorted(role_stats):
            s = role_stats[role]
            role_mad = s["total_dev"] / s["count"] if s["count"] > 0 else 0
            rate = s["corrections"] / s["count"] * 100 if s["count"] > 0 else 0
            flag = " ← needs tuning" if rate > 50 else ""
            print(f"{role:<12} {role_mad:>8.4f} {s['count']:>8} {s['corrections']:>12} {rate:>7.0f}%{flag}")

    return mad


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EDPA CW Heuristics Evaluator")
    parser.add_argument("--ground-truth", required=True, help="Path to ground_truth.yaml")
    parser.add_argument("--heuristics", required=True, help="Path to cw_heuristics.yaml")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-record details")
    parser.add_argument("--per-role", action="store_true", help="Show per-role MAD breakdown")
    args = parser.parse_args()

    evaluate(args.ground_truth, args.heuristics, verbose=args.verbose, per_role=args.per_role)
