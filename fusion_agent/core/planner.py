"""Planning engine for generating, validating, and managing bounded ExecutionPlans."""

import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fusion_agent.core.budget import TaskBudgetController
from fusion_agent.models.assessment import ReviewRisk
from fusion_agent.models.context import CodeContext
from fusion_agent.models.plan import ExecutionPlan, PlanStep, StepStatus
from fusion_agent.models.task import Complexity, Task
from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.normalizer import StructuredOutputNormalizer


class PlanValidationError(ValueError):
    """Raised when an execution plan fails structural, bounding, or DAG constraints."""
    pass


class PlanEngine:
    """Orchestrates structured plan generation, dependency validation, and bounded critique."""

    def __init__(self, max_steps: int = 5, max_amendments: int = 1):
        self.max_steps = max_steps
        self.max_amendments = max_amendments

    @staticmethod
    def validate_plan_dag(steps: List[PlanStep]) -> Tuple[bool, Optional[str]]:
        """Verify that step dependencies form a valid Directed Acyclic Graph (DAG)."""
        step_ids = {s.id for s in steps}
        if len(step_ids) != len(steps):
            return False, "Duplicate step IDs detected in plan."

        # Check that dependencies only refer to defined steps
        for step in steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    return False, f"Step '{step.id}' references undefined dependency '{dep}'."
                if dep == step.id:
                    return False, f"Step '{step.id}' cannot depend on itself."

        # Topological cycle check
        visited: Set[str] = set()
        visiting: Set[str] = set()
        step_map = {s.id: s for s in steps}

        def dfs(node_id: str) -> bool:
            visiting.add(node_id)
            for neighbor_id in step_map[node_id].dependencies:
                if neighbor_id in visiting:
                    return True  # Cycle detected
                if neighbor_id not in visited:
                    if dfs(neighbor_id):
                        return True
            visiting.remove(node_id)
            visited.add(node_id)
            return False

        for step in steps:
            if step.id not in visited:
                if dfs(step.id):
                    return False, "Dependency cycle detected among plan steps."

        return True, None

    @classmethod
    def parse_plan_json(cls, raw_content: str, max_steps: int = 5) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Extract and parse structured JSON plan from raw model response."""
        text = raw_content.strip()
        data = None

        # 1. Direct JSON parse
        try:
            data = json.loads(text)
        except Exception:
            pass

        # 2. Markdown fenced block parse
        if not data or not isinstance(data, dict):
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                except Exception:
                    pass

        # 3. Outer bracket extraction
        if not data or not isinstance(data, dict):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except Exception:
                    pass

        if not data or not isinstance(data, dict):
            return None, "Response could not be parsed as valid JSON."

        steps_raw = data.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            return None, "JSON missing non-empty 'steps' array."

        if len(steps_raw) > max_steps:
            return None, f"Plan contains {len(steps_raw)} steps, exceeding hard limit of {max_steps}."

        return data, None

    def build_planning_prompt(self, task_text: str, max_steps: int) -> str:
        """Formulate concise structured prompt for the planning provider."""
        return (
            f"Task: {task_text.strip()}\n\n"
            "Decompose this engineering task into a bounded, sequential execution plan.\n"
            "RESPONSE CONTRACT:\n"
            "Respond ONLY with a JSON object adhering to this schema:\n"
            "{\n"
            '  "title": "Short descriptive title of the task",\n'
            '  "summary": "Brief 1-2 sentence architectural summary",\n'
            '  "steps": [\n'
            "    {\n"
            '      "id": "step-1",\n'
            '      "objective": "Clear concrete coding action for this step",\n'
            '      "rationale": "Why this step is needed",\n'
            '      "expected_files": ["relative/path/to/file.ext"],\n'
            '      "expected_symbols": ["ClassName", "function_name"],\n'
            '      "dependencies": [],\n'
            '      "verification_expectations": "tests/test_file.py",\n'
            '      "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",\n'
            '      "estimated_complexity": "LOW|MEDIUM|HIGH"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            f"RULES:\n"
            f"1. Maximum {max_steps} steps. Keep each step small, atomic, and verifiable.\n"
            "2. Dependencies must reference earlier step IDs only.\n"
            "3. Do not include extraneous markdown commentary outside the JSON block.\n"
            "4. verification_expectations must be data (e.g. an existing test file 'tests/test_foo.py'), never shell commands. Leave empty if unit tests will be created in a later step."
        )

    def generate_plan(
        self,
        task: Task,
        context: Optional[CodeContext],
        planner: AgentProvider,
        reviewer: Optional[AgentProvider] = None,
        budget: Optional[TaskBudgetController] = None,
        emit: Optional[Callable] = None,
    ) -> ExecutionPlan:
        """Prompt planner to create a validated ExecutionPlan, with optional critique."""
        if emit is None:
            emit = lambda event, data: None

        task_text = task.description if task.description == task.title or task.description.startswith(task.title) else f"{task.title}\n{task.description}"
        prompt = self.build_planning_prompt(task_text, self.max_steps)

        emit("status", {"message": f"{planner.name} is decomposing task into a bounded execution plan..."})
        t_start = time.perf_counter()
        resp = planner.invoke(prompt, context=context)
        t_dur = (time.perf_counter() - t_start) * 1000.0

        if budget:
            budget.record_call(
                provider_name=planner.name,
                duration_ms=resp.duration_ms or t_dur,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                fusion_context_tokens=resp.fusion_context_tokens,
                reasoning_tokens=resp.reasoning_tokens,
                visible_output_tokens=resp.visible_output_tokens,
                cached_tokens=resp.cached_tokens,
                raw_usage=resp.metadata.get("usage"),
                stage="Plan Generation",
            )

        data, err = self.parse_plan_json(resp.content, self.max_steps)

        # Retry once if invalid and budget allows
        if (not data or err) and budget and budget.can_call_provider()[0]:
            emit("status", {"message": f"Plan parsing error ({err}). Requesting structured correction..."})
            correction_prompt = f"Previous response had validation error: {err}\nPlease re-output strictly valid JSON following the schema contract."
            t_start = time.perf_counter()
            resp = planner.invoke(correction_prompt, context=context)
            t_dur = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=planner.name,
                duration_ms=resp.duration_ms or t_dur,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                fusion_context_tokens=resp.fusion_context_tokens,
                stage="Plan Correction",
            )
            data, err = self.parse_plan_json(resp.content, self.max_steps)

        if not data or err:
            raise PlanValidationError(f"Failed to generate valid ExecutionPlan: {err}")

        # Build steps
        steps: List[PlanStep] = []
        for raw_s in data.get("steps", []):
            s_id = str(raw_s.get("id") or f"step-{len(steps)+1}")
            obj = str(raw_s.get("objective") or "Implement task component")
            rat = str(raw_s.get("rationale") or "")
            exp_files = [str(f) for f in raw_s.get("expected_files", []) if f]
            exp_syms = [str(s) for s in raw_s.get("expected_symbols", []) if s]
            deps = [str(d) for d in raw_s.get("dependencies", []) if d]
            verif = raw_s.get("verification_expectations")

            raw_risk = str(raw_s.get("risk_level", "LOW")).upper()
            risk = ReviewRisk.LOW
            if raw_risk in ReviewRisk.__members__:
                risk = ReviewRisk(raw_risk)

            raw_comp = str(raw_s.get("estimated_complexity", "LOW")).upper()
            comp = Complexity.LOW
            if raw_comp in Complexity.__members__:
                comp = Complexity(raw_comp)

            step = PlanStep(
                id=s_id,
                objective=obj,
                rationale=rat,
                expected_files=exp_files,
                expected_symbols=exp_syms,
                dependencies=deps,
                verification_expectations=str(verif) if verif else None,
                risk_level=risk,
                estimated_complexity=comp,
                status=StepStatus.PENDING,
            )
            steps.append(step)

        # Validate DAG
        dag_ok, dag_err = self.validate_plan_dag(steps)
        if not dag_ok:
            raise PlanValidationError(f"Invalid plan dependency structure: {dag_err}")

        plan = ExecutionPlan(
            plan_id=str(uuid.uuid4())[:8],
            task_id=task.id,
            title=data.get("title") or task.title,
            summary=data.get("summary") or "Checkpointed multi-step execution plan.",
            steps=steps,
            status="PREPARED",
        )

        # Optional Plan Critique for High Risk Plans
        has_high_risk = any(s.risk_level in (ReviewRisk.HIGH, ReviewRisk.CRITICAL) for s in steps)
        if reviewer and has_high_risk and budget and budget.can_call_provider()[0]:
            emit("status", {"message": f"{reviewer.name} is reviewing high-risk execution plan..."})
            plan_json_summary = json.dumps(plan.to_dict(), indent=2)
            critique_prompt = (
                f"### PROPOSED EXECUTION PLAN\n{plan_json_summary}\n\n"
                "Evaluate this plan for feasibility, missing dependencies, safety risks, or gaps. "
                "If acceptable, reply [APPROVED]. If major adjustments are needed, reply [NEEDS_REVISION] with specific critique."
            )
            t_start = time.perf_counter()
            crit_resp = reviewer.invoke(critique_prompt, context=context)
            t_dur = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=reviewer.name,
                duration_ms=crit_resp.duration_ms or t_dur,
                input_tokens=crit_resp.input_tokens,
                output_tokens=crit_resp.output_tokens,
                fusion_context_tokens=crit_resp.fusion_context_tokens,
                stage="Plan Critique",
            )
            emit("status", {"message": f"Plan review completed by {reviewer.name}."})

        emit("status", {"message": f"Generated validated execution plan with {len(plan.steps)} steps."})
        return plan

    @staticmethod
    def validate_verification_expectation(
        expectation: str,
        worktree_path: Optional[Any] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verify that a verification expectation is safe data and not a malicious command."""
        val = expectation.strip()
        if not val:
            return True, None

        # 1. Reject shell operators and control characters
        forbidden = set(";&|><$`\n\r")
        found_forbidden = [c for c in forbidden if c in val]
        if found_forbidden:
            return False, f"Verification expectation contains forbidden characters: {found_forbidden}"

        # 2. Reject path traversal
        if ".." in val:
            return False, "Path traversal ('..') is not permitted in verification expectations."

        # 3. Reject absolute paths
        if val.startswith("/") or val.startswith("\\") or (len(val) > 1 and val[1] == ":"):
            return False, "Absolute paths are not permitted in verification expectations."

        # 4. If worktree_path is supplied and expectation specifies a file, verify presence
        if worktree_path is not None:
            # Extract test file path before any pytest filter (e.g. tests/test_foo.py::test_bar or -k)
            test_file_cand = val.split("::")[0].split()[0]
            if test_file_cand.endswith(".py"):
                candidate_path = (worktree_path / test_file_cand).resolve()
                if not candidate_path.is_file():
                    return False, f"Specified test target '{test_file_cand}' does not exist in worktree."

        return True, None

    def apply_plan_amendment(
        self,
        plan: ExecutionPlan,
        amendment_dict: Dict[str, Any],
    ) -> ExecutionPlan:
        """Apply a bounded amendment to an active ExecutionPlan with strict safety invariants."""
        if plan.amendments_count >= self.max_amendments:
            raise PlanValidationError(
                f"Maximum plan amendments ({self.max_amendments}) exceeded; plan cannot be modified further."
            )

        action = amendment_dict.get("action", "").upper()
        target_step_id = amendment_dict.get("step_id")
        reason = amendment_dict.get("reason", "Dynamic discovery during execution")

        if not action:
            raise PlanValidationError("Amendment missing 'action' field.")

        # 1. Invariant: Cannot modify COMPLETED steps
        if target_step_id:
            target_step = plan.get_step(target_step_id)
            if target_step and target_step.status == StepStatus.COMPLETED:
                raise PlanValidationError(
                    f"Amendment rejected: step '{target_step_id}' is already COMPLETED and cannot be modified."
                )

        if action == "ADD_FILES":
            if not target_step_id:
                raise PlanValidationError("ADD_FILES amendment requires 'step_id'.")
            target_step = plan.get_step(target_step_id)
            if not target_step:
                raise PlanValidationError(f"Step '{target_step_id}' not found in plan.")

            new_files = [str(f) for f in amendment_dict.get("files", []) if f]
            for nf in new_files:
                if nf not in target_step.expected_files:
                    target_step.expected_files.append(nf)

        elif action == "INSERT_STEP":
            if len(plan.steps) >= self.max_steps:
                raise PlanValidationError(
                    f"Cannot insert step: plan would exceed maximum bound of {self.max_steps} steps."
                )

            step_data = amendment_dict.get("step", {})
            new_id = step_data.get("id") or f"step-{len(plan.steps)+1}"
            if plan.get_step(new_id):
                raise PlanValidationError(f"Cannot insert step with existing ID '{new_id}'.")

            deps = [str(d) for d in step_data.get("dependencies", []) if d]
            # Ensure dependencies only refer to existing steps
            for dep in deps:
                if not plan.get_step(dep):
                    raise PlanValidationError(f"Inserted step references non-existent dependency '{dep}'.")

            new_step = PlanStep(
                id=new_id,
                objective=step_data.get("objective", "Supplementary implementation step"),
                rationale=reason,
                expected_files=[str(f) for f in step_data.get("expected_files", []) if f],
                expected_symbols=[str(s) for s in step_data.get("expected_symbols", []) if s],
                dependencies=deps,
                verification_expectations=step_data.get("verification_expectations"),
                risk_level=ReviewRisk(str(step_data.get("risk_level", "LOW")).upper()) if str(step_data.get("risk_level", "LOW")).upper() in ReviewRisk.__members__ else ReviewRisk.LOW,
                estimated_complexity=Complexity(str(step_data.get("estimated_complexity", "LOW")).upper()) if str(step_data.get("estimated_complexity", "LOW")).upper() in Complexity.__members__ else Complexity.LOW,
                status=StepStatus.PENDING,
            )

            # Insert before next pending step or append
            plan.steps.append(new_step)

            # Re-validate DAG
            dag_ok, dag_err = self.validate_plan_dag(plan.steps)
            if not dag_ok:
                raise PlanValidationError(f"Amendment resulted in invalid DAG: {dag_err}")

        else:
            raise PlanValidationError(f"Unsupported amendment action '{action}'.")

        plan.amendments_count += 1
        return plan

