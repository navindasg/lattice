#!/usr/bin/env python3
"""Scoring agent for the miniapi benchmark.

Runs each module's test suite independently, computes per-module
pass/fail, and outputs a structured scorecard.

Usage:
    uv run python score.py
    uv run python score.py --json
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass


MODULES = [
    {"name": "users",    "ticket": "01", "test_file": "tests/test_users.py",    "expected_tests": 4},
    {"name": "projects", "ticket": "02", "test_file": "tests/test_projects.py", "expected_tests": 4},
    {"name": "tasks",    "ticket": "03", "test_file": "tests/test_tasks.py",    "expected_tests": 5},
    {"name": "tags",     "ticket": "04", "test_file": "tests/test_tags.py",     "expected_tests": 4},
    {"name": "search",   "ticket": "05", "test_file": "tests/test_search.py",   "expected_tests": 3},
    {"name": "stats",    "ticket": "06", "test_file": "tests/test_stats.py",    "expected_tests": 3},
]

MAX_SCORE = len(MODULES) + 1  # 6 modules + 1 bonus


@dataclass
class ModuleResult:
    name: str
    ticket: str
    passed: int
    failed: int
    errors: int
    total: int
    score: int  # 1 if all pass, 0 otherwise
    duration_s: float
    output: str


def run_module_tests(module: dict) -> ModuleResult:
    """Run pytest for a single module and parse results."""
    start = time.monotonic()
    result = subprocess.run(
        ["uv", "run", "pytest", module["test_file"], "-v", "--tb=short"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    duration = time.monotonic() - start
    output = result.stdout + result.stderr

    # Parse pytest output for pass/fail counts
    passed = output.count(" PASSED")
    failed = output.count(" FAILED")
    errors = output.count(" ERROR")
    total = passed + failed + errors

    return ModuleResult(
        name=module["name"],
        ticket=module["ticket"],
        passed=passed,
        failed=failed,
        errors=errors,
        total=total,
        score=1 if (failed == 0 and errors == 0 and passed > 0) else 0,
        duration_s=round(duration, 2),
        output=output,
    )


def run_health_check() -> bool:
    """Check if the app starts and /health returns ok."""
    result = subprocess.run(
        [
            "uv", "run", "python", "-c",
            "from httpx import AsyncClient, ASGITransport; "
            "from miniapi.app import app; "
            "import asyncio; "
            "async def check(): "
            "    t = ASGITransport(app=app); "
            "    async with AsyncClient(transport=t, base_url='http://test') as c: "
            "        r = await c.get('/health'); "
            "        assert r.status_code == 200; "
            "        print('HEALTH_OK'); "
            "asyncio.run(check())"
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return "HEALTH_OK" in result.stdout


def main() -> None:
    as_json = "--json" in sys.argv
    start_time = time.monotonic()

    results: list[ModuleResult] = []
    for module in MODULES:
        try:
            result = run_module_tests(module)
        except subprocess.TimeoutExpired:
            result = ModuleResult(
                name=module["name"],
                ticket=module["ticket"],
                passed=0, failed=0, errors=1,
                total=module["expected_tests"],
                score=0,
                duration_s=60.0,
                output="TIMEOUT: tests did not complete within 60s",
            )
        results.append(result)

    # Bonus: all modules pass + health check
    all_pass = all(r.score == 1 for r in results)
    health_ok = run_health_check() if all_pass else False
    bonus = 1 if (all_pass and health_ok) else 0

    total_score = sum(r.score for r in results) + bonus
    total_duration = round(time.monotonic() - start_time, 2)

    if as_json:
        output = {
            "score": total_score,
            "max_score": MAX_SCORE,
            "pct": round(total_score / MAX_SCORE * 100, 1),
            "bonus": bonus,
            "duration_s": total_duration,
            "modules": [
                {
                    "name": r.name,
                    "ticket": r.ticket,
                    "score": r.score,
                    "passed": r.passed,
                    "failed": r.failed,
                    "errors": r.errors,
                    "duration_s": r.duration_s,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 60)
        print("  miniapi Benchmark Scorecard")
        print("=" * 60)
        print()
        print(f"  {'Module':<12} {'Ticket':<8} {'Pass':<6} {'Fail':<6} {'Score':<6} {'Time':<8}")
        print(f"  {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*8}")
        for r in results:
            icon = "\u2705" if r.score == 1 else "\u274c"
            print(
                f"  {r.name:<12} {r.ticket:<8} {r.passed:<6} "
                f"{r.failed:<6} {icon:<6} {r.duration_s:<8.2f}s"
            )
        print()
        bonus_icon = "\u2705" if bonus else "\u274c"
        print(f"  {'BONUS':<12} {'all+hc':<8} {'':6} {'':6} {bonus_icon:<6}")
        print()
        print(f"  Total Score: {total_score} / {MAX_SCORE} ({total_score/MAX_SCORE*100:.0f}%)")
        print(f"  Duration:    {total_duration}s")
        print("=" * 60)

        # Print failures detail
        failures = [r for r in results if r.score == 0]
        if failures:
            print()
            print("Failed modules:")
            for r in failures:
                print(f"\n--- {r.name} (ticket {r.ticket}) ---")
                # Show last 20 lines of output
                lines = r.output.strip().split("\n")
                for line in lines[-20:]:
                    print(f"  {line}")


if __name__ == "__main__":
    main()
