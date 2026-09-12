"""Reproducible script to generate Milestone 11 evaluation SVG charts.

Data Source: Frozen Milestone 11 Phase C Evaluation Matrix (63 total runs).
No external libraries required; uses pure standard library Python.
"""

from pathlib import Path


def generate_correctness_chart(output_path: Path):
    """Generate SVG bar chart for Functional Correctness (M11 Phase C)."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 320" width="100%" height="100%" style="background-color: #0d1117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
  <style>
    .title { fill: #f0f6fc; font-size: 16px; font-weight: 600; }
    .subtitle { fill: #8b949e; font-size: 12px; }
    .label { fill: #c9d1d9; font-size: 13px; font-weight: 500; }
    .val { fill: #ffffff; font-size: 13px; font-weight: 600; }
    .note { fill: #8b949e; font-size: 11px; font-style: italic; }
    .grid { stroke: #21262d; stroke-width: 1; }
  </style>

  <!-- Title & Subtitle -->
  <text x="30" y="38" class="title">Functional Correctness Rate (Milestone 11 Held-Out Evaluation)</text>
  <text x="30" y="58" class="subtitle">Evaluated across 21 tasks (7 engineering tasks × 3 repetitions). Full task success.</text>

  <!-- Grid lines -->
  <line x1="210" y1="80" x2="620" y2="80" class="grid" />
  <line x1="210" y1="135" x2="620" y2="135" class="grid" />
  <line x1="210" y1="190" x2="620" y2="190" class="grid" />
  <line x1="210" y1="245" x2="620" y2="245" class="grid" />

  <!-- Scale ticks (0%, 25%, 50%, 75%, 100%) -->
  <text x="210" y="262" fill="#8b949e" font-size="11" text-anchor="middle">0%</text>
  <text x="312.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">25%</text>
  <text x="415" y="262" fill="#8b949e" font-size="11" text-anchor="middle">50%</text>
  <text x="517.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">75%</text>
  <text x="620" y="262" fill="#8b949e" font-size="11" text-anchor="middle">100%</text>

  <!-- Bar 1: Fusion Agent -->
  <text x="200" y="105" class="label" text-anchor="end">Fusion Agent</text>
  <rect x="210" y="90" width="253.8" height="24" rx="4" fill="#388bfd" />
  <text x="472" y="107" class="val">61.9% (13/21)</text>

  <!-- Bar 2: OpenAI Codex Alone -->
  <text x="200" y="150" class="label" text-anchor="end">Codex Alone (gpt-5.6-sol)</text>
  <rect x="210" y="135" width="292.7" height="24" rx="4" fill="#238636" />
  <text x="511" y="152" class="val">71.4% (15/21)</text>

  <!-- Bar 3: Google Antigravity Alone -->
  <text x="200" y="195" class="label" text-anchor="end">Antigravity Alone (gemini-3.8)</text>
  <rect x="210" y="180" width="292.7" height="24" rx="4" fill="#a371f7" />
  <text x="511" y="197" class="val">71.4% (15/21)</text>

  <!-- Baseline Footer Note -->
  <text x="30" y="295" class="note">* Note: Standalone models achieved higher overall functional pass rates. Fusion sequential planning suffered interface drift.</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def generate_tokens_chart(output_path: Path):
    """Generate SVG bar chart for Median Input Token Usage (M11 Phase C)."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 320" width="100%" height="100%" style="background-color: #0d1117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
  <style>
    .title { fill: #f0f6fc; font-size: 16px; font-weight: 600; }
    .subtitle { fill: #8b949e; font-size: 12px; }
    .label { fill: #c9d1d9; font-size: 13px; font-weight: 500; }
    .val { fill: #ffffff; font-size: 13px; font-weight: 600; }
    .savings { fill: #58a6ff; font-size: 12px; font-weight: 600; }
    .note { fill: #8b949e; font-size: 11px; font-style: italic; }
    .grid { stroke: #21262d; stroke-width: 1; }
  </style>

  <!-- Title & Subtitle -->
  <text x="30" y="38" class="title">Median Input Context Consumption (Lower is Better)</text>
  <text x="30" y="58" class="subtitle">Context window ingestion per task run (in tokens). Bounded 3-tier snapshotting vs full workspace.</text>

  <!-- Grid lines -->
  <line x1="210" y1="80" x2="620" y2="80" class="grid" />
  <line x1="210" y1="135" x2="620" y2="135" class="grid" />
  <line x1="210" y1="190" x2="620" y2="190" class="grid" />
  <line x1="210" y1="245" x2="620" y2="245" class="grid" />

  <!-- Scale ticks (0k, 100k, 200k, 300k, 400k) -->
  <text x="210" y="262" fill="#8b949e" font-size="11" text-anchor="middle">0k</text>
  <text x="312.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">100k</text>
  <text x="415" y="262" fill="#8b949e" font-size="11" text-anchor="middle">200k</text>
  <text x="517.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">300k</text>
  <text x="620" y="262" fill="#8b949e" font-size="11" text-anchor="middle">400k</text>

  <!-- Bar 1: Fusion Agent -->
  <text x="200" y="105" class="label" text-anchor="end">Fusion Agent</text>
  <rect x="210" y="90" width="68.7" height="24" rx="4" fill="#2ea043" />
  <text x="286" y="107" class="val">66,980 tokens</text>

  <!-- Bar 2: Google Antigravity Alone -->
  <text x="200" y="150" class="label" text-anchor="end">Antigravity Alone</text>
  <rect x="210" y="135" width="291.4" height="24" rx="4" fill="#a371f7" />
  <text x="510" y="152" class="val">284,316 tokens</text>
  <text x="510" y="167" class="savings">(-76.4% in Fusion)</text>

  <!-- Bar 3: OpenAI Codex Alone -->
  <text x="200" y="200" class="label" text-anchor="end">Codex Alone</text>
  <rect x="210" y="185" width="379.8" height="24" rx="4" fill="#f85149" />
  <text x="600" y="202" class="val" text-anchor="end">370,525 tokens</text>
  <text x="600" y="217" class="savings" text-anchor="end">(-81.9% in Fusion)</text>

  <!-- Footer Note -->
  <text x="30" y="295" class="note">* Result: Fusion extracted only relevant symbols &amp; dependencies, reducing median input tokens by 81.9% vs Codex.</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def generate_scope_chart(output_path: Path):
    """Generate SVG bar chart for Strict Scope Compliance (M11 Phase C)."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 320" width="100%" height="100%" style="background-color: #0d1117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;">
  <style>
    .title { fill: #f0f6fc; font-size: 16px; font-weight: 600; }
    .subtitle { fill: #8b949e; font-size: 12px; }
    .label { fill: #c9d1d9; font-size: 13px; font-weight: 500; }
    .val { fill: #ffffff; font-size: 13px; font-weight: 600; }
    .note { fill: #e3b341; font-size: 11px; }
    .grid { stroke: #21262d; stroke-width: 1; }
  </style>

  <!-- Title & Subtitle -->
  <text x="30" y="38" class="title">Strict Scope Oracle Pass Rate (No Test Mutation / Scope Creep)</text>
  <text x="30" y="58" class="subtitle">Evaluated whether agents obeyed boundary contracts and avoided rewriting existing tests.</text>

  <!-- Grid lines -->
  <line x1="210" y1="80" x2="620" y2="80" class="grid" />
  <line x1="210" y1="135" x2="620" y2="135" class="grid" />
  <line x1="210" y1="190" x2="620" y2="190" class="grid" />
  <line x1="210" y1="245" x2="620" y2="245" class="grid" />

  <!-- Scale ticks (0%, 20%, 40%, 60%, 80%) -->
  <text x="210" y="262" fill="#8b949e" font-size="11" text-anchor="middle">0%</text>
  <text x="312.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">20%</text>
  <text x="415" y="262" fill="#8b949e" font-size="11" text-anchor="middle">40%</text>
  <text x="517.5" y="262" fill="#8b949e" font-size="11" text-anchor="middle">60%</text>
  <text x="620" y="262" fill="#8b949e" font-size="11" text-anchor="middle">80%</text>

  <!-- Bar 1: Fusion Agent -->
  <text x="200" y="105" class="label" text-anchor="end">Fusion Agent</text>
  <rect x="210" y="90" width="292.7" height="24" rx="4" fill="#388bfd" />
  <text x="511" y="107" class="val">57.1% (12/21)</text>

  <!-- Bar 2: Google Antigravity Alone -->
  <text x="200" y="150" class="label" text-anchor="end">Antigravity Alone</text>
  <rect x="210" y="135" width="244.0" height="24" rx="4" fill="#a371f7" />
  <text x="462" y="152" class="val">47.6% (10/21)</text>

  <!-- Bar 3: OpenAI Codex Alone -->
  <text x="200" y="195" class="label" text-anchor="end">Codex Alone</text>
  <rect x="210" y="180" width="195.2" height="24" rx="4" fill="#d29922" />
  <text x="413" y="197" class="val">38.1% (8/21)</text>

  <!-- Important Caveat Note -->
  <text x="30" y="292" class="note">⚠ Caveat: Raw scope differences were partly affected by uncommunicated protected-path policies</text>
  <text x="30" y="307" class="note">   for standalone systems, which had no external contract forbidding test modifications.</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def main():
    assets_dir = Path(__file__).resolve().parent
    assets_dir.mkdir(parents=True, exist_ok=True)

    print("Generating Milestone 11 evaluation SVG charts...")
    generate_correctness_chart(assets_dir / "benchmark_correctness.svg")
    generate_tokens_chart(assets_dir / "benchmark_tokens.svg")
    generate_scope_chart(assets_dir / "benchmark_scope.svg")
    print(f"Generated 3 SVG charts in {assets_dir}")


if __name__ == "__main__":
    main()
