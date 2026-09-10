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


class BenchmarkReporter:
    """Generates comparative statistical reports from benchmark run records."""

    def __init__(self, storage: Optional[BenchmarkStorage] = None):
        self.storage = storage or BenchmarkStorage()

    def generate_report_markdown(self, records: Optional[List[BenchmarkRunRecord]] = None) -> str:
        """Generate comprehensive markdown evaluation report."""
        runs = records if records is not None else self.storage.get_runs()
        if not runs:
            return "# Benchmark Report\n\nNo benchmark runs found in storage."

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

            in_tokens = [r.native_input_tokens for r in s_runs if r.native_input_tokens]
            med_in_tokens = statistics.median(in_tokens) if in_tokens else 0

            md.append(
                f"| **{sut.value.upper()}** | {total} | {passes} | {partials} | {fails} | "
                f"**{pass_pct:.1f}%** | {med_duration:.2f}s | {int(med_in_tokens):,} |"
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

            in_t = [r.native_input_tokens for r in s_runs]
            out_t = [r.native_output_tokens for r in s_runs]
            med_in = statistics.median(in_t) if in_t else 0
            med_out = statistics.median(out_t) if out_t else 0

            ctx_vals = [r.fusion_controlled_context_tokens for r in s_runs if r.fusion_controlled_context_tokens is not None]
            med_ctx = f"{int(statistics.median(ctx_vals)):,}" if ctx_vals else "N/A"

            residuals = [r.provider_managed_overhead_residual for r in s_runs if r.provider_managed_overhead_residual is not None]
            med_res = f"{int(statistics.median(residuals)):,}" if residuals else "N/A"

            md.append(f"| **{sut.value}** | {int(med_in):,} | {int(med_out):,} | {med_ctx} | {med_res} |")

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
        return "\n".join(md)
