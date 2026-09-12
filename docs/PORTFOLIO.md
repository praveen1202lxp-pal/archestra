# Fusion Agent — Portfolio & Technical Interview Guide

This guide is designed for engineering hiring managers, technical interviewers, and resume reviewers. It summarizes the core problems, architectural decisions, empirical evaluations, and key talking points behind Fusion Agent.

---

## 1. The 30-Second Elevator Pitch

> *"Most AI coding tools wrap a single frontier LLM and give it raw terminal access. The model ends up ingesting massive token context, mutating test suites to make failing builds artificially pass, and creating unrecoverable merge conflicts. I built **Fusion Agent**, a provider-agnostic coding-agent control plane. Instead of letting models write directly to user codebases, Fusion orchestrates multiple specialized models (like OpenAI Codex and Google Antigravity/Gemini) through ephemeral Git worktrees, 3-tier bounded context snapshots, automated verification suites, cross-model peer review, and a mandatory human approval gate. In empirical evaluations, this control plane reduced median input tokens by **81.9% compared to Codex** and **76.4% compared to Antigravity** while eliminating test-suite mutation."*

---

## 2. The 2-Minute Technical Walkthrough

> *"Fusion Agent treats foundation models as stateless reasoning engines while centralizing all state, repository intelligence, and execution safety inside a deterministic Python runtime:*
> 
> 1. ***Task Assessment & Dynamic Routing***: When a user runs `fusion run "<task>"`, the Task Router assesses the instruction against a risk matrix (complexity, file scope, risk level) and chooses an orchestration strategy—from single-agent autonomous editing for simple bug fixes to multi-agent deliberation with peer review for complex refactoring.
> 
> 2. ***Deterministic Context Extraction***: Rather than ingesting the whole repository, the Repository Indexer extracts a 3-tier contextual snapshot (AST definitions, symbol call graphs, and dependency imports). This reduced median input tokens to **66,980** compared to **370,525** for Codex alone.
> 
> 3. ***Worktree Isolation & Structured Editing***: Edits execute inside ephemeral Git worktrees (`git worktree add`). The primary model generates structured edit specifications, which Fusion's `WorkspaceEditor` validates against AST syntax trees and scope boundaries before writing to disk.
> 
> 4. ***Automated Verification & Adversarial Peer Review***: Fusion immediately executes the repository's test suite inside the worktree. Upon test pass, a secondary model reviews the unified diff as an adversarial peer. If defects or regressions are detected, the implementer enters a bounded repair loop (capped at 2 rounds) to prevent runaway token spend.
> 
> 5. ***Durable SQLite Recovery***: Every stage, critique, token count, and git commit SHA is journaled in an embedded SQLite database using WAL mode. If a process is killed mid-task, `fusion resume <task-id>` resumes execution from the exact checkpoint without re-running completed stages.
> 
> 6. ***Mandatory Human Promotion Gate***: Verified changes are never automatically merged. Fusion renders the verified unified diff to the terminal, and only an affirmative human confirmation (`[y/N]`) promotes the isolated branch into the user's base repository."*

---

## 3. Key Architecture Talking Points

- **Provider-Agnostic Abstraction**: Pluggable provider interface (`ProviderInterface`) wrapping local CLI tools (`codex`, `agy`), API endpoints, and hermetic mock providers for testing.
- **Scope Contract Oracle**: Mathematical bounding of allowed file edits derived from prompt intents. Rejects model attempts to modify pre-existing unit tests or generate unrequested peripheral files.
- **Fail-Safe Promotion Boundary**: The promotion engine requires tests to pass, peer review to approve, a non-empty unified diff, and interactive human affirmation.
- **Strict Precedence Configuration**: Layered configuration hierarchy: `CLI Flags > Project Config (.fusion/config.json) > User Config (~/.fusion/config.json) > Environment Variables > Built-in Defaults`.
- **Zero Secret Leakage**: Integrated `SecretFilter` and `to_safe_dict()` serialization automatically mask API keys, tokens, and passwords in terminal logs, database tables, and exported configs.

---

## 4. Empirical Benchmark Findings (Milestone 11)

In a frozen, 63-run held-out comparative evaluation across 7 software engineering tasks (3 repetitions each):

| Metric | Fusion Agent | OpenAI Codex Alone (`gpt-5.6-sol`) | Google Antigravity Alone (`gemini-3.8-flash`) |
| :--- | :---: | :---: | :---: |
| **Functional Correctness** | **61.9%** (13/21) | **71.4%** (15/21) | **71.4%** (15/21) |
| **Median Input Tokens** | **66,980** | **370,525** | **284,316** |
| **Input Token Reduction** | *Baseline* | **-81.9%** | **-76.4%** |
| **Strict Scope Oracle** | **57.1%** (12/21) | **38.1%** (8/21)* | **47.6%** (10/21)* |
| **Median Task Duration** | **70.4s** | **248.9s** | **244.3s** |

*\*Caveat: Standalone systems were partly affected by uncommunicated protected-path policies that were not explicitly stated in task-visible prompts.*

### Honest Engineering Takeaways:
- **Where Standalone Models Won**: Standalone models demonstrated higher single-step code synthesis accuracy on complex multi-service refactoring tasks.
- **Where Fusion Won**: Massive context reduction (81.9% savings vs Codex), governance over scope boundaries, prevention of test-suite corruption, and automated peer review/repair cycles.
- **Identified Weakness**: Multi-step sequential planning in Fusion remains susceptible to interface drift between plan steps.

---

## 5. Five Technical Interview Questions & Answers

### Q1: Why didn't you let the LLM directly execute git commands and write files?
> **Answer**: *"Allowing autonomous LLMs raw terminal write access breaks the blast radius. Models frequently rewrite `.git`, overwrite configuration files, or modify test assertions to make failing builds artificially pass. By forcing the LLM to output structured file specifications while Fusion manages ephemeral Git worktrees, AST syntax validation, and automated verification, we enforce a strict security and policy boundary. The model proposes; Fusion validates and applies."*

### Q2: Why use SQLite instead of Postgres or Redis for agent persistence?
> **Answer**: *"Fusion is a developer tool designed to run locally on developer workstations and CI runners. Introducing Postgres or Redis adds external daemon dependencies and operational overhead. SQLite with Write-Ahead Logging (WAL) provides sub-millisecond ACID transactions, handles single-writer orchestration effortlessly, and stores the entire task history, token ledger, and checkpoints in a single file inside `.fusion/fusion.db`."*

### Q3: Why did Fusion achieve lower raw correctness (61.9%) than standalone models (71.4%)?
> **Answer**: *"This was one of our most valuable empirical findings. Standalone models operated in a single unconstrained turn, taking massive code dumps and writing monolithic solutions. Fusion decomposed complex tasks into multi-step execution plans. While this drastically reduced token usage and improved scope discipline, it introduced **interface drift**—where Step 2 generated function signatures slightly misaligned with Step 1's definitions. Identifying this trade-off gave us a concrete architectural target for future iterations rather than hiding failures."*

### Q4: How do you prevent infinite repair loops or runaway model billing?
> **Answer**: *"Every execution plan operates under strict token and turn budgets enforced by a `BudgetController`. The repair loop is hard-capped at 2 rounds. Each round bounds reviewer feedback to 2,000 characters to prevent prompt bloat. If verification still fails after 2 repair attempts, Fusion aborts the task, dismantles the ephemeral worktree, and records the failure in SQLite rather than wasting tokens in an infinite loop."*

### Q5: What is the difference between Git worktree isolation and an OS sandbox?
> **Answer**: *"We are very deliberate about this terminology. Git worktree isolation provides **repository and policy isolation**—it guarantees that uncommitted user files and the active branch are untouched. However, when Fusion runs automated test suites (like `pytest`), those commands run natively with the host user's privileges. It is a **host-trusted, non-adversarial execution model**. If a workflow requires untrusted, adversarial code execution, container isolation (Docker/gVisor) must be layered on top."*

---

## 6. Three Strong Resume Bullets

- **Architected a provider-agnostic multi-model AI coding-agent orchestrator** in Python that dynamically routes tasks across OpenAI Codex and Google Antigravity, implementing ephemeral Git worktree isolation, automated test verification, and cross-model peer review.
- **Engineered a 3-tier deterministic repository context engine** (AST definitions, symbol call graphs, dependency imports) that reduced median input token consumption by **81.9% vs. standalone Codex** (66,980 vs. 370,525 tokens) across a frozen 63-run held-out evaluation suite.
- **Implemented a durable checkpoint and recovery system** using SQLite WAL mode and atomic state transitions, enabling zero-loss task resumption and enforcing a mandatory interactive human promotion gate with unified diff inspection.
