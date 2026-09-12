# Fusion Agent — Core Engineering Decisions & Rationale

This document outlines the architectural trade-offs, deliberate design choices, and alternatives considered during the development of Fusion Agent. It is designed for deep-dive technical interview discussions and system architecture reviews.

---

## 1. Why Fusion Owns Repository Writes Instead of Providers
### Decision:
Model providers are strictly confined to generating structured textual edit specifications (`FILE: ... CONTENT: ...` or structured JSON blocks). Fusion's internal `WorkspaceEditor` applies all filesystem modifications, validates path boundaries, and checks syntax before disk commitment.

### Rationale:
- **Blast Radius Containment**: Autonomous models granted raw shell access or arbitrary write permissions frequently overwrite `.git`, modify package configurations, or touch files outside the project scope.
- **Atomic Operations**: By owning the write pipeline, Fusion can validate syntax trees (ASTs) before file write, verify that target files are within scope contracts, and rollback cleanly if errors occur.
- **Provider Portability**: Decoupling code generation from filesystem operations allows Fusion to treat any model (Codex, Antigravity, local Gemma, mock) as a stateless intelligence engine rather than requiring custom filesystem plugins per provider.

---

## 2. Why SQLite is Sufficient for Durable Orchestration State
### Decision:
Fusion uses local SQLite (`.fusion/fusion.db`) via WAL mode (`journal_mode=WAL`) and Python's standard `sqlite3` module with an embedded migration engine for all task checkpoints, deliberation logs, token metrics, and promotion history.

### Rationale:
- **Zero External Infrastructure**: Developers and CI runners should not require Docker, PostgreSQL, Redis, or cloud daemon setup to execute a coding task.
- **Single-Writer Concurrency**: Fusion orchestration is inherently single-worktree-per-task. SQLite easily handles thousands of reads and transactional writes per second with sub-millisecond latency.
- **Durable Checkpointing**: SQLite's atomic transactions guarantee that if a power outage or task cancellation occurs during a multi-turn repair loop, the state of completed stages is persisted and can be resumed with `fusion resume <task-id>` without losing progress.
- **Portability**: The entire state of a project's agent history is stored in a single file inside `.fusion/`, which can be inspected or archived effortlessly.

---

## 3. Why a Vector Database Was Omitted Without Measured Need
### Decision:
Repository context is gathered deterministically using symbol call graphs, import dependencies, AST definitions, and deterministic git status rather than embedding code chunks into a vector database.

### Rationale:
- **Relevance over Similarity**: In codebases, cosine similarity between vector embeddings frequently retrieves superficial matches (e.g. comments, similar variable names) while missing the exact semantic call path (caller, callee, type definitions, interface contracts).
- **Latency & Indexing Cost**: Embedding a 50,000-line codebase requires significant upfront computation, ongoing re-indexing on every git checkout, and heavy external dependencies (e.g. Chroma, FAISS, PyTorch).
- **Evidence-Based Context Reduction**: In Milestone 11 evaluations, Fusion's 3-tier deterministic snapshotting reduced median input token usage by **81.9% vs Codex** (66,980 vs 370,525 tokens) without a vector database. We avoid introducing architectural complexity until empirical evidence demonstrates retrieval failure.

---

## 4. Why Dynamic Roles Were Chosen Over Fixed Architect/Coder/Reviewer
### Decision:
Instead of hardcoding a static multi-agent hierarchy (e.g., Model A is always Architect, Model B is always Coder, Model C is always Reviewer), Fusion uses dynamic role assignment driven by task complexity assessment and optimization mode.

### Rationale:
- **Model Asymmetry**: Frontier models have different cost/latency trade-offs. For example, `gemini-3.8-flash-high` provides rapid, thorough critiques and verification checks, while `gpt-5.6-sol` excels at nuanced code synthesis.
- **Dynamic Role Reversal**: If a primary implementer generates a patch that fails automated verification twice, Fusion dynamically swaps the roles: the reviewer becomes the implementer with fresh context, and the previous implementer critiques the new approach.
- **Avoid Over-Orchestration**: Trivial single-line bugfixes do not warrant a 3-agent deliberation committee. Dynamic routing assigns low-complexity tasks to single-agent autonomous edit pipelines, avoiding unnecessary latency and token costs.

---

## 5. Why Checkpoints and Git Worktrees Are Separate Concepts
### Decision:
Fusion maintains Git worktrees for file isolation, but maintains execution checkpoints in SQLite.

### Rationale:
- **File State vs. Cognitive State**: A Git branch tracks the filesystem snapshot (tree, commit, diff). However, a coding agent's cognitive state includes conversation history, model prompts, critiques, token budgets, stage durations, and rejected proposals.
- **Clean Discardability**: If a user rejects a proposed patch at the human promotion gate, the Git worktree and branch are pruned instantly (`git worktree remove --force; git branch -D`). However, the record of the deliberation, verification failure, and human rejection remains stored in SQLite for post-mortem analysis and learning.

---

## 6. Why Git Worktrees Are Not Described as OS Sandboxes
### Decision:
Documentation and CLI diagnostics explicitly state that Git worktree isolation is **repository/policy isolation**, not an OS-level security sandbox. The native execution model is **host-trusted and non-adversarial**.

### Rationale:
- **Engineering Precision**: Ephemeral Git worktrees prevent uncommitted working tree destruction, merge conflicts, and dirty branch contamination. However, when Fusion runs automated verifiers (e.g. `pytest`) or provider CLIs, those subprocesses execute with the host user's system privileges.
- **No False Security Guarantees**: Describing worktree isolation as a "sandbox" falsely implies protection against malicious arbitrary code execution (e.g. an agent executing `os.system("rm -rf /")`). If adversarial execution protection is needed, containerized environments (Docker/Podman/gVisor) must be explicitly configured.

---

## 7. Why Human Approval Remains Mandatory
### Decision:
No command-line flag exists to auto-promote changes to the user's base repository without interactive confirmation (`[y/N]`). The `--no-promote` flag exists to explicitly discard or bypass interactive input for CI, but there is no `--yes` flag to bypass the human gate.

### Rationale:
- **Prevent Unsupervised Merges**: LLMs can hallucinate plausible-looking solutions that satisfy unit tests while introducing subtle security regressions, performance degradations, or architectural misalignments.
- **Fail-Safe Default**: If an agent produces a patch and verification passes, the final promotion step requires deliberate human affirmation. If the user hits Enter (default) or types `n`, the candidate worktree is cleanly dismantled and discarded without modifying the repository.

---

## 8. Why Milestone 11 Preserved Failures Rather Than Tuning Against the Suite
### Decision:
During the Milestone 11 comparative evaluation (63 runs across 7 tasks × 3 repetitions), benchmark results were frozen upon completion. Observed failures (e.g. multi-step sequential interface drift) were preserved in historical tables rather than retuning prompts or routing rules to game the score.

### Rationale:
- **Scientific Integrity**: Tuning prompts or routing thresholds against held-out benchmark tasks invalidates the benchmark as an out-of-sample evaluation.
- **Authentic Engineering Insights**: Freezing the results uncovered genuine operational insights:
  - Fusion achieved **61.9% (13/21)** correctness vs **71.4% (15/21)** for standalone baselines.
  - Fusion demonstrated dramatic context savings (**66,980 vs 370,525 tokens**).
  - The evaluation proved that sequential multi-step planning is susceptible to interface drift, establishing a concrete baseline and architectural target for future engineering cycles.
