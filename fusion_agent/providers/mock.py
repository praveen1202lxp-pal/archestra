"""Mock provider for unit testing, integration tests, and local demonstrations."""

import time
from typing import Any, Callable, Dict, List, Optional

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.base import (
    AgentProvider,
    AgentResponse,
    ContextSnapshot,
    HealthCheckResult,
    ReviewResponse,
)
from fusion_agent.providers.capabilities import ProviderCapabilities


class MockProvider(AgentProvider):
    """A fully configurable mock provider for hermetic testing."""

    def __init__(
        self,
        name: str = "mock_provider",
        capabilities: Optional[ProviderCapabilities] = None,
        config: Optional[Dict[str, Any]] = None,
        default_response: str = "Mock response: Analysis and solution provided.",
        default_review_status: ReviewStatus = ReviewStatus.APPROVED,
        default_review_comments: str = "Looks good. Architecture and logic are sound.",
        should_fail_health: bool = False,
        should_fail_invoke: bool = False,
    ):
        super().__init__(name=name, config=config)
        self.capabilities = capabilities or ProviderCapabilities(
            reasoning=True,
            structured_output=True,
            context_window=128_000,
            estimated_cost_per_1k_input=0.001,
            estimated_cost_per_1k_output=0.002,
        )
        self.default_response = default_response
        self.default_review_status = default_review_status
        self.default_review_comments = default_review_comments
        self.should_fail_health = should_fail_health
        self.should_fail_invoke = should_fail_invoke
        
        # Test inspection hooks
        self.invocations: List[Dict[str, Any]] = []
        self.reviews: List[Dict[str, Any]] = []
        self.response_generator: Optional[Callable[[str, Optional[ContextSnapshot]], str]] = None
        self.review_generator: Optional[Callable[[str, str, Optional[ContextSnapshot]], ReviewResponse]] = None

    def initialize(self) -> bool:
        self._is_initialized = True
        return True

    def health_check(self) -> HealthCheckResult:
        if self.should_fail_health:
            return HealthCheckResult(
                healthy=False,
                message="MockProvider simulated health failure.",
                latency_ms=1.0,
            )
        return HealthCheckResult(
            healthy=True,
            message="MockProvider online and ready.",
            latency_ms=1.0,
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    def invoke(
        self,
        prompt: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> AgentResponse:
        start_time = time.perf_counter()
        
        if self.should_fail_invoke:
            raise RuntimeError(f"MockProvider '{self.name}' simulated invocation failure.")

        content = (
            self.response_generator(prompt, context)
            if self.response_generator
            else f"[{self.name}] {self.default_response}"
        )
        
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        ctx_tokens = 0
        if context:
            if hasattr(context, "to_prompt_context"):
                ctx_tokens = len(context.to_prompt_context().split())
            else:
                ctx_tokens = len(str(context).split())

        response = AgentResponse(
            content=content,
            input_tokens=len(prompt.split()) + ctx_tokens,
            output_tokens=len(content.split()),
            duration_ms=duration_ms,
            metadata={"mock_name": self.name, "cwd": cwd},
            fusion_context_tokens=ctx_tokens,
        )
        self.invocations.append({
            "prompt": prompt,
            "context": context,
            "response": response,
            "cwd": cwd,
            "kwargs": kwargs,
        })
        return response

    def review(
        self,
        content: str,
        criteria: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> ReviewResponse:
        start_time = time.perf_counter()
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if self.review_generator:
            response = self.review_generator(content, criteria, context)
        else:
            response = ReviewResponse(
                status=self.default_review_status,
                comments=f"[{self.name} Review] {self.default_review_comments}",
                suggested_fixes=[],
                input_tokens=len(content.split()) + len(criteria.split()),
                output_tokens=len(self.default_review_comments.split()),
                duration_ms=duration_ms,
            )
        self.reviews.append({
            "content": content,
            "criteria": criteria,
            "context": context,
            "cwd": cwd,
            "response": response,
        })
        return response

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        self._is_initialized = False
