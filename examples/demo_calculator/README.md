# Demo Calculator — Safe Walkthrough Fixture for Fusion Agent

This directory provides a minimal, self-contained Python project designed for testing Fusion Agent without risking valuable production codebases.

---

## Project Structure

```
demo_calculator/
├── src/
│   └── calculator.py       # Basic calculator implementation (add, subtract, multiply, divide)
├── tests/
│   └── test_calculator.py  # Automated tests with pytest
└── README.md
```

---

## Walkthrough: Testing Fusion Agent End-to-End

### Step 1: Pre-flight Verification
Run `fusion doctor` to confirm that Python, Git, and your coding CLI providers are discovered and authenticated:

```bash
fusion doctor
```

### Step 2: Initialize Fusion in Demo Project
Initialize project-level configuration and the shared brain:

```bash
cd examples/demo_calculator
fusion init --name "DemoCalculator"
```

This creates `.fusion/config.json`, initializes `.fusion/fusion.db`, and automatically updates `.gitignore`.

### Step 3: Check Configured Providers
Verify which AI providers are active and ready:

```bash
fusion providers
```

### Step 4: Run a Task
Submit an instruction to Fusion:

```bash
fusion run "Add a power(base, exponent) function with input validation to src/calculator.py and tests in tests/test_calculator.py"
```

### What Happens Behind the Scenes:
1. **Task Routing**: Fusion evaluates complexity and selects an orchestration strategy (e.g. `EXECUTE_AND_REVIEW` or `PROPOSE_CRITIQUE_REFINE`).
2. **Workspace Isolation**: A dedicated ephemeral Git worktree is provisioned on a task branch. Your working tree is never touched directly.
3. **Execution & Deliberation**: The primary implementer generates code changes, and the secondary model performs an automated peer review.
4. **Verification**: Fusion runs automated tests in the isolated worktree (`pytest`).
5. **Human Approval Gate**: Fusion presents the unified diff and verification results, and asks for confirmation:
   ```
   Apply verified changes? [y/N]:
   ```
6. **Promotion**: If approved (`y`), changes are cleanly merged into your working branch. If declined (`n`), the ephemeral worktree is discarded with zero side-effects.
