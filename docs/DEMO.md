# Fusion Agent — Demo Video Recording Script (2–3 Minutes)

This recording guide provides a turn-by-turn script, visual cues, terminal commands, and spoken narration for a live developer demonstration of Fusion Agent.

---

## Technical Setup Before Recording
1. **Working Directory**: Start inside the safe demo project:
   ```bash
   cd examples/demo_calculator
   ```
2. **Terminal Size**: 120 columns × 36 rows, modern font (e.g. Fira Code / JetBrains Mono, 14pt).
3. **Environment**: Virtual environment active (`.venv\Scripts\activate`), clean Git working tree.
4. **Target Duration**: 2 minutes 30 seconds.

---

## Timeline & Scene Breakdown

### Scene 1: Introduction & What Fusion Is (0:00 – 0:20)
- **Visual**: Terminal prompt in `examples/demo_calculator`, root `README.md` or architecture diagram on adjacent monitor/split screen.
- **Action**: Run `fusion --version`
  ```bash
  fusion --version
  ```
- **Terminal Displays**:
  ```text
  Fusion Agent v0.12.0
  ```
- **Narration (Spoken)**:
  > *"When developers use coding models like Codex or Gemini directly, the model receives unbounded context and tries to rewrite files or test suites without boundaries. Fusion Agent is a provider-agnostic coding-agent control plane. Rather than acting as a chat interface, Fusion owns repository intelligence, task routing, isolated workspace editing, verification, peer review, and human approval."*

---

### Scene 2: Environment Diagnostics with `fusion doctor` (0:20 – 0:40)
- **Visual**: Terminal executing system health checks.
- **Action**: Run `fusion doctor`
  ```bash
  fusion doctor
  ```
- **Terminal Displays**:
  ```text
  Fusion Doctor

    ✓ Fusion Version         Fusion Agent v0.12.0
    ✓ Python Runtime         Python 3.12.10 (CPython)
    ✓ Git Tooling            git version 2.45.1 at .../git.EXE
    ✓ Repository State       Git repository detected (clean)
    ✓ Storage & SQLite       State directory writable (.fusion/fusion.db)
    ✓ Configuration          Valid config at .fusion/config.json (mode: BALANCED)
    ✓ Codex CLI              Codex CLI (0.153.4) online and ready
    ✓ Antigravity CLI        Antigravity CLI (1.2.2) online and ready

  Summary: 8 passed, 0 warnings, 0 errors
  ✓ System is ready to run Fusion Agent.
  ```
- **Narration (Spoken)**:
  > *"Before executing tasks, `fusion doctor` performs non-destructive diagnostics. It verifies Git tooling, Python runtime, state writability, and discovers local provider CLIs without invoking expensive generative model calls or exposing authentication credentials."*

---

### Scene 3: Submitting a Task (0:40 – 1:15)
- **Visual**: Submitting a practical feature addition to the calculator project.
- **Action**: Inspect current code, then run `fusion run`:
  ```bash
  cat src/calculator.py
  fusion run "Add power(base, exponent) function to src/calculator.py and add unit test coverage in tests/test_calculator.py"
  ```
- **Terminal Displays (Real-time progress milestones)**:
  ```text
  Fusion Agent
  Task: Add power(base, exponent) function to src/calculator.py...

    * Analyzing task complexity and routing...
    -> Strategy: AUTONOMOUS_EDIT | Implementer: codex | Reviewer: antigravity
      Assessment: CODE_MODIFICATION | Complexity: LOW | Scope: SINGLE_FILE | Risk: LOW
    * Pre-flight check: verifying repository working tree is clean...
    * Allocated isolated worktree on task branch 'fusion/task-8f3b21'.
    * Indexing repository and selecting relevant files deterministically...
    * Constructed bounded CodeContext (2 files: src/calculator.py, tests/test_calculator.py, ~420 tokens).
  ```
- **Narration (Spoken)**:
  > *"We give Fusion a concrete task. Notice what happens immediately: Fusion assesses the task as a low-risk single-file modification. Instead of sending our entire project tree, Fusion's repository indexer constructs a bounded context of just 420 tokens. Edits do not touch our active branch—Fusion provisions an isolated Git worktree on an ephemeral branch."*

---

### Scene 4: Orchestration, Verification & Peer Review (1:15 – 1:45)
- **Visual**: Implementation patch generation, automated test execution, and secondary model review.
- **Terminal Displays**:
  ```text
    * OpenAI Codex CLI is formulating structured code modifications...
    * Applied 1 structured file patch to isolated worktree.
    * Executing automated verification suite inside isolated worktree (pytest)...
    * Verification passed (5 passed in 0.18s).
    * Google Antigravity CLI is conducting peer diff review (Round 1)...
    * Peer review approved: "Clean implementation using Python ** operator with proper type annotations and test coverage."
  ```
- **Narration (Spoken)**:
  > *"Codex generates the implementation patch in the worktree. Fusion immediately executes the test suite. Once tests pass, the secondary model—here Antigravity running Gemini—acts as an adversarial peer reviewer, inspecting the unified diff to ensure no unrequested changes or test mutations occurred."*

---

### Scene 5: Diff Inspection & Human Promotion Gate (1:45 – 2:10)
- **Visual**: Unified diff displayed in terminal with the interactive `[y/N]` prompt.
- **Terminal Displays**:
  ```text
  === ISOLATED REPOSITORY EDIT INSPECTION ===
  Task Branch:  fusion/task-8f3b21
  Base Commit:  48886800
  Verification: PASSED (exit code 0, 0.18s)
  Peer Review:  [APPROVED] by Google Antigravity CLI

  --- GENERATED UNIFIED DIFF ---
  diff --git a/src/calculator.py b/src/calculator.py
  --- a/src/calculator.py
  +++ b/src/calculator.py
  @@ -24,3 +24,7 @@ def divide(a: float, b: float) -> float:
       if b == 0:
           raise ValueError("Cannot divide by zero.")
       return a / b
  +
  +def power(base: float, exponent: float) -> float:
  +    """Calculate base raised to the power of exponent."""
  +    return float(base ** exponent)
  ------------------------------

  Apply verified changes? [y/N]:
  ```
- **Action**: Type `y` and hit Enter:
  ```text
  Apply verified changes? [y/N]: y

  [SUCCESS] Successfully promoted task-8f3b21 to master.
  ```
- **Narration (Spoken)**:
  > *"Here is Fusion's core safety boundary: the human approval gate. Even with passing tests and approved peer review, no code is ever merged automatically. Fusion presents the exact unified diff for human inspection. When we approve with 'y', the isolated branch is cleanly merged into master and the worktree is dismantled."*

---

### Scene 6: Durable State, Benchmark & Summary (2:10 – 2:30)
- **Visual**: Quick `fusion status`, followed by the Milestone 11 evaluation summary in `README.md`.
- **Action**: Run `fusion status`:
  ```bash
  fusion status
  ```
- **Terminal Displays**:
  ```text
  Project:      demo_calculator
  Mode:         BALANCED
  Recent Tasks in Memory:
    - [PROMOTED] Add power function to calculator (AUTONOMOUS_EDIT)
  ```
- **Narration (Spoken)**:
  > *"All decisions, provider token metrics, and checkpoints persist durably in SQLite. In our 63-run held-out evaluation across 7 tasks, Fusion reduced median input tokens by 81.9% compared to Codex and 76.4% compared to Antigravity, while enforcing strict change boundaries. That is Fusion Agent: autonomous multi-model collaboration with strict engineering control."*

---

## Troubleshooting & Edge-Case Demos
- **Demonstrating Rollback**: Reject the prompt with `N` or Enter to show clean worktree teardown.
- **Demonstrating Resumption**: Kill the process with `Ctrl+C` mid-task, then run `fusion resume <task-id>` to demonstrate zero-loss resumption from the last recorded SQLite checkpoint.
