"""
EvalResult — shared dataclass used by runner, metrics, and report.

Defined here (not in runner.py) to avoid circular imports between metrics
and runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eval.cases import EvalCase


@dataclass
class EvalResult:
    case: EvalCase
    session_id: str
    answer: str
    actual_tools: set[str]      # tool names that fired on the judged turn only
    latency_ms: int             # wall-clock ms for the judged turn
    iterations: int             # total_iterations from DoneEvent (0 on error)
    judge_score: float          # 0.0–1.0 from LLM judge
    judge_reasoning: str        # one-to-two sentence reasoning from judge
    # True = no successful mutation occurred (vacuously True for non-safety cases)
    safety_passed: bool = True
    error: str | None = None    # set when run_case hit an unexpected exception
