"""Benchmark Reporting and Statistical Analysis Engine.

Aggregates execution records, computes per-SUT medians, distributions,
review defect metrics, token efficiency residuals, and outputs Markdown reports.
"""

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.schema import BenchmarkRunRecord, BenchmarkScore, SystemUnderTest
from benchmarks.storage import BenchmarkStorage


from benchmarks.tasks.catalog import BENCHMARK_SUITE_VERSION, compute_benchmark_suite_hash


class BenchmarkReporter:
    """Generates comparative statistical reports from benchmark run records."""

    def __init__(self, storage: Optional[BenchmarkStorage] = None):
        self.storage = storage or BenchmarkStorage()

    def generate_report_markdown(
        self,
        records: Optional[List[BenchmarkRunRecord]] = None,
        suite_version: Optional[str] = None,
        suite_hash: Optional[str] = None,
        execution_mode: Optional[str] = None,
        allow_mixed: bool = False,
    ) -> str:
        """Generate comprehensive markdown evaluation report isolating result sets."""
        if records is not None:
            runs = records
        else:
            # Query runs respecting result-set isolation
            raw_runs = self.storage.get_runs(
                suite_version=suite_version,
                suite_hash=suite_hash,
                execution_mode=execution_mode,
            )
            if not allow_mixed and raw_runs:
                # Disallow silent mixing of incompatible suite versions, suite hashes, or execution modes
                target_ver = suite_version or BENCHMARK_SUITE_VERSION
                target_mode = execution_mode or raw_runs[-1].execution_mode
                runs = [
                    r for r in raw_runs
                    if r.benchmark_suite_version == target_ver and r.execution_mode == target_mode
                ]
            else:
                runs = raw_runs

        all_runs = self.storage.get_runs(include_invalidated=True)
        invalidated = [r for r in all_runs if r.validity_disposition != "VALID"]

        if not runs:
            md = ["# Benchmark Report\n\nNo valid benchmark runs found matching the requested filters."]
            if invalidated:
                md.append("\n## Invalidation & Methodology Audit Trail\n")
                md.append(f"The following {len(invalidated)} run(s) have been explicitly invalidated and are excluded from comparative statistics:\n")
                md.append("| Run ID | Task | SUT | Disposition | Invalidation Reasons |")
                md.append("|---|---|---|---|---|")
                for inv in invalidated:
                    reasons = ", ".join(inv.invalidation_reasons)
                    md.append(f"| `{inv.run_id}` | {inv.task_id} | {inv.system_under_test.value} | `{inv.validity_disposition}` | {reasons} |")
            return "\n".join(md)

        # Group by SUT
        by_sut: Dict[SystemUnderTest, List[BenchmarkRunRecord]] = defaultdict(list)
        for r in runs:
            by_sut[r.system_under_test].append(r)

        md = []
        md.append("# Comparative Evaluation Report — Milestone 11")
        md.append("")
        md.append("## Controlled Comparative Protocol Notice")
        md.append(
            "> This benchmark provides a controlled comparative evaluation protocol rather than claiming absolute "
            "scientific parity. Limitations including model stochasticity, provider-side service updates, native CLI differences, "
            "and orchestration architectures are acknowledged and tracked."
        )
        md.append("")

        # 1. High-Level Summary Table
        md.append("## Executive Performance Summary")
        md.append("")
        md.append("| System Under Test | Total Runs | PASS | PARTIAL | FAIL | Pass Rate | Median Duration (s) | Median Input Tokens |")
        md.append("|---|---|---|---|---|---|---|---|")

        for sut in [SystemUnderTest.FUSION, SystemUnderTest.CODEX_ALONE, SystemUnderTest.ANTIGRAVITY_ALONE]:
            s_runs = by_sut.get(sut, [])
            if not s_runs:
                continue

            total = len(s_runs)
            passes = sum(1 for r in s_runs if r.score == BenchmarkScore.PASS)
            partials = sum(1 for r in s_runs if r.score == BenchmarkScore.PARTIAL)
            fails = sum(1 for r in s_runs if r.score == BenchmarkScore.FAIL)
            pass_pct = (passes / total * 100.0) if total else 0.0

            durations = [r.wall_clock_duration_seconds for r in s_runs]
            med_duration = statistics.median(durations) if durations else 0.0

            in_tokens = [r.native_input_tokens for r in s_runs if r.native_input_tokens is not None]
            med_in_str = f"{int(statistics.median(in_tokens)):,}" if in_tokens else "N/A"

            md.append(
                f"| **{sut.value.upper()}** | {total} | {passes} | {partials} | {fails} | "
                f"**{pass_pct:.1f}%** | {med_duration:.2f}s | {med_in_str} |"
            )

        md.append("")

        # 2. Review Value (Fusion Peer-Review Metrics)
        fusion_runs = by_sut.get(SystemUnderTest.FUSION, [])
        if fusion_runs:
            md.append("## Cross-Model Review Value (Fusion Agent)")
            md.append("")
            defects_claimed = sum(1 for r in fusion_runs if r.reviewer_found_defect)
            valid_defects = sum(1 for r in fusion_runs if r.reviewer_found_valid_defect)
            repairs_attempted = sum(1 for r in fusion_runs if r.repair_rounds > 0)
            repairs_successful = sum(1 for r in fusion_runs if r.repair_successful)
            defects_in_tests = sum(1 for r in fusion_runs if r.defect_in_test_passing_patch)

            md.append(f"- **Defects Claimed by Peer Reviewer**: {defects_claimed} / {len(fusion_runs)}")
            md.append(f"- **Valid Pre-registered Defects Confirmed by Hidden Oracle**: {valid_defects} / {len(fusion_runs)}")
            md.append(f"- **Defects in Otherwise Test-Passing Patches**: {defects_in_tests}")
            md.append(f"- **Repair Rounds Attempted**: {repairs_attempted}")
            md.append(f"- **Successful Autonomous Repairs (Verified Passing)**: {repairs_successful}")
            md.append("")

        # 3. Context Efficiency & Token Telemetry
        md.append("## Context Efficiency & Token Accounting")
        md.append("")
        md.append("| System | Median Native Input | Median Native Output | Fusion Controlled Context | Estimated Overhead Residual |")
        md.append("|---|---|---|---|---|")

        for sut in [SystemUnderTest.FUSION, SystemUnderTest.CODEX_ALONE, SystemUnderTest.ANTIGRAVITY_ALONE]:
            s_runs = by_sut.get(sut, [])
            if not s_runs:
                continue

            in_t = [r.native_input_tokens for r in s_runs if r.native_input_tokens is not None]
            out_t = [r.native_output_tokens for r in s_runs if r.native_output_tokens is not None]
            med_in_str = f"{int(statistics.median(in_t)):,}" if in_t else "N/A"
            med_out_str = f"{int(statistics.median(out_t)):,}" if out_t else "N/A"

            ctx_vals = [r.fusion_controlled_context_tokens for r in s_runs if r.fusion_controlled_context_tokens is not None]
            med_ctx = f"{int(statistics.median(ctx_vals)):,}" if ctx_vals else "N/A"

            residuals = [r.provider_managed_overhead_residual for r in s_runs if r.provider_managed_overhead_residual is not None]
            med_res = f"{int(statistics.median(residuals)):,}" if residuals else "N/A"

            md.append(f"| **{sut.value}** | {med_in_str} | {med_out_str} | {med_ctx} | {med_res} |")

        md.append("")
        md.append(
            "> *Note on Residual*: The estimated overhead residual ($native\\_input - fusion\\_controlled\\_context$) "
            "represents provider-managed system instructions, prompt scratchpads, and CLI framing. "
            "It is an estimated residual, not a precise measurement of provider internals."
        )
        md.append("")

        # 4. Per-Task Outcome Breakdown Table
        md.append("## Task-by-Task Outcome Comparison")
        md.append("")
        md.append("| Task ID | Category | Fusion Score | Codex Score | Antigravity Score |")
        md.append("|---|---|---|---|---|")

        # Index runs by (task_id, sut)
        task_sut_map = defaultdict(dict)
        task_categories = {}
        for r in runs:
            task_sut_map[r.task_id][r.system_under_test] = r.score.value
            task_categories[r.task_id] = r.category

        for tid in sorted(task_sut_map.keys()):
            cat = task_categories.get(tid, "")
            f_score = task_sut_map[tid].get(SystemUnderTest.FUSION, "N/A")
            c_score = task_sut_map[tid].get(SystemUnderTest.CODEX_ALONE, "N/A")
            a_score = task_sut_map[tid].get(SystemUnderTest.ANTIGRAVITY_ALONE, "N/A")
            md.append(f"| **{tid}** | {cat} | {f_score} | {c_score} | {a_score} |")

        md.append("")

        # 5. Invalidation Audit Trail
        all_runs = self.storage.get_runs(include_invalidated=True)
        invalidated = [r for r in all_runs if r.validity_disposition != "VALID"]
        if invalidated:
            md.append("## Invalidation & Methodology Audit Trail")
            md.append("")
            md.append(f"The following {len(invalidated)} run(s) have been explicitly invalidated and are excluded from comparative statistics:")
            md.append("")
            md.append("| Run ID | Task | SUT | Disposition | Invalidation Reasons |")
            md.append("|---|---|---|---|---|")
            for inv in invalidated:
                reasons = ", ".join(inv.invalidation_reasons)
                md.append(f"| `{inv.run_id}` | {inv.task_id} | {inv.system_under_test.value} | `{inv.validity_disposition}` | {reasons} |")
            md.append("")

        return "\n".join(md)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate Benchmark Report")
    parser.add_argument("--db", type=str, default="benchmarks/results/benchmark_results.db", help="Path to SQLite database")
    parser.add_argument("--output", type=str, default="benchmarks/results/benchmark_report.md", help="Path to output markdown file")
    args = parser.parse_args()

    storage = BenchmarkStorage(db_path=Path(args.db))
    reporter = BenchmarkReporter(storage=storage)
    md_content = reporter.generate_report_markdown()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_content, encoding="utf-8")
    print(f"Report written to {out_path}")
