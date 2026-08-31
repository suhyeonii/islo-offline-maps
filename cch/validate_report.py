#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed on invalid CCH prototype output")
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-query-p95-ms", type=float, default=1000.0)
    parser.add_argument("--min-arc-pairs", type=int, default=50)
    parser.add_argument("--require-random-reachable", type=int, default=0)
    parser.add_argument("--min-largest-component-ratio", type=float, default=0.0)
    args = parser.parse_args()

    report = json.loads(args.report.read_text())
    failures: list[str] = []
    if report["activeNodes"] <= 0 or report["inputArcs"] <= 0 or report["cchArcs"] <= 0:
        failures.append("empty topology")
    if report["reachableArcPairs"] < args.min_arc_pairs:
        failures.append(
            f"reachableArcPairs={report['reachableArcPairs']} < {args.min_arc_pairs}"
        )
    if report["reachableRandomQueries"] < args.require_random_reachable:
        failures.append(
            "reachableRandomQueries="
            f"{report['reachableRandomQueries']} < {args.require_random_reachable}"
        )
    if report["largestWeakComponentRatio"] < args.min_largest_component_ratio:
        failures.append(
            "largestWeakComponentRatio="
            f"{report['largestWeakComponentRatio']:.6f} < "
            f"{args.min_largest_component_ratio:.6f}"
        )
    if report["queryP95Ms"] > args.max_query_p95_ms:
        failures.append(
            f"queryP95Ms={report['queryP95Ms']:.3f} > {args.max_query_p95_ms:.3f}"
        )

    if failures:
        print("CCH validation FAILED: " + "; ".join(failures))
        return 1
    print(
        "CCH validation passed: "
        f"nodes={report['activeNodes']} arcs={report['inputArcs']} "
        f"cchArcs={report['cchArcs']} queryP95Ms={report['queryP95Ms']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
