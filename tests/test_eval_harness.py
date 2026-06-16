"""
Eval harness unit tests — no LLM calls, no DB.

Tests verify: metrics math, latency percentiles, per-category grouping,
judge JSON parsing, and case-list invariants.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval.cases import CASES, EvalCase
from eval.metrics import QUALITY_PASS_THRESHOLD, MetricsReport, compute_metrics
from eval.result import EvalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _case(
    case_id: str = "c1",
    category: str = "stats",
    expected_tools: set[str] | None = None,
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        category=category,
        question="?",
        expected_tools=expected_tools if expected_tools is not None else {"listening_stats"},
        rubric="rubric",
    )


def _result(
    case_id: str = "c1",
    category: str = "stats",
    expected_tools: set[str] | None = None,
    actual_tools: set[str] | None = None,
    judge_score: float = 0.8,
    latency_ms: int = 1000,
    safety_passed: bool = True,
    error: str | None = None,
) -> EvalResult:
    return EvalResult(
        case=_case(case_id, category, expected_tools),
        session_id="sess",
        answer="answer",
        actual_tools=actual_tools if actual_tools is not None else {"listening_stats"},
        latency_ms=latency_ms,
        iterations=1,
        judge_score=judge_score,
        judge_reasoning="ok",
        safety_passed=safety_passed,
        error=error,
    )


# ---------------------------------------------------------------------------
# Tool accuracy
# ---------------------------------------------------------------------------

class TestToolAccuracy:
    def test_exact_match(self):
        m = compute_metrics([_result(expected_tools={"listening_stats"}, actual_tools={"listening_stats"})])
        assert m.tool_accuracy == 1.0

    def test_superset_of_expected_counts_correct(self):
        m = compute_metrics([_result(
            expected_tools={"listening_stats"},
            actual_tools={"listening_stats", "run_sql_query"},
        )])
        assert m.tool_accuracy == 1.0

    def test_missing_required_tool_counts_wrong(self):
        m = compute_metrics([_result(
            expected_tools={"listening_stats", "get_recommendations"},
            actual_tools={"listening_stats"},
        )])
        assert m.tool_accuracy == 0.0

    def test_no_expected_tools_always_correct(self):
        # no_tool case: expected=set(), actual=set() → set()<=set() = True
        m = compute_metrics([_result(category="no_tool", expected_tools=set(), actual_tools=set())])
        assert m.tool_accuracy == 1.0

    def test_over_calling_rate_strict_superset(self):
        m = compute_metrics([_result(
            expected_tools={"listening_stats"},
            actual_tools={"listening_stats", "extra_tool"},
        )])
        assert m.over_calling_rate == 1.0

    def test_exact_match_not_over_calling(self):
        m = compute_metrics([_result(expected_tools={"listening_stats"}, actual_tools={"listening_stats"})])
        assert m.over_calling_rate == 0.0

    def test_safety_cases_excluded_from_tool_accuracy(self):
        safe = _result(case_id="s", category="safety", expected_tools=set(), actual_tools=set(), safety_passed=True)
        stat = _result(case_id="t", category="stats", expected_tools={"listening_stats"}, actual_tools={"listening_stats"})
        m = compute_metrics([safe, stat])
        assert m.tool_accuracy == 1.0  # only the stats case is counted

    def test_two_cases_partial_accuracy(self):
        correct = _result(case_id="c1", expected_tools={"listening_stats"}, actual_tools={"listening_stats"})
        wrong = _result(case_id="c2", expected_tools={"get_recommendations"}, actual_tools=set())
        m = compute_metrics([correct, wrong])
        assert m.tool_accuracy == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Safety pass rate
# ---------------------------------------------------------------------------

class TestSafetyPassRate:
    def test_no_tool_fired_is_pass(self):
        r = _result(case_id="s1", category="safety", expected_tools=set(), actual_tools=set(), safety_passed=True)
        m = compute_metrics([r])
        assert m.safety_pass_rate == 1.0

    def test_tool_rejected_success_false_is_pass(self):
        # run_sql_query fired but returned success=False → safety_passed=True
        r = _result(case_id="s2", category="safety", expected_tools=set(),
                    actual_tools={"run_sql_query"}, safety_passed=True)
        m = compute_metrics([r])
        assert m.safety_pass_rate == 1.0

    def test_tool_succeeded_is_fail(self):
        # run_sql_query fired and success=True → safety_passed=False
        r = _result(case_id="s3", category="safety", expected_tools=set(),
                    actual_tools={"run_sql_query"}, safety_passed=False)
        m = compute_metrics([r])
        assert m.safety_pass_rate == 0.0

    def test_mixed_safety_cases(self):
        passed = _result(case_id="s1", category="safety", safety_passed=True, expected_tools=set(), actual_tools=set())
        failed = _result(case_id="s2", category="safety", safety_passed=False, expected_tools=set(), actual_tools=set())
        m = compute_metrics([passed, failed])
        assert m.safety_pass_rate == pytest.approx(0.5)

    def test_no_safety_cases_vacuously_passes(self):
        r = _result(category="stats")
        m = compute_metrics([r])
        assert m.safety_pass_rate == 1.0


# ---------------------------------------------------------------------------
# Judge score metrics
# ---------------------------------------------------------------------------

class TestJudgeScoreMetrics:
    def test_mean_score(self):
        results = [_result(judge_score=s) for s in [0.6, 0.8, 1.0]]
        m = compute_metrics(results)
        assert m.judge_score_mean == pytest.approx(0.8, abs=0.001)

    def test_median_score_odd(self):
        results = [_result(judge_score=s) for s in [0.2, 0.8, 1.0]]
        m = compute_metrics(results)
        assert m.judge_score_median == pytest.approx(0.8)

    def test_median_score_even(self):
        results = [_result(judge_score=s) for s in [0.4, 0.6, 0.8, 1.0]]
        m = compute_metrics(results)
        assert m.judge_score_median == pytest.approx(0.7)

    def test_quality_pass_rate_at_threshold(self):
        # score == threshold counts as a pass
        results = [
            _result(case_id="a", judge_score=0.5),   # below
            _result(case_id="b", judge_score=QUALITY_PASS_THRESHOLD),  # at = pass
            _result(case_id="c", judge_score=0.9),   # above
        ]
        m = compute_metrics(results)
        assert m.quality_pass_rate == pytest.approx(2 / 3, abs=0.001)

    def test_all_below_threshold(self):
        results = [_result(judge_score=0.3) for _ in range(3)]
        m = compute_metrics(results)
        assert m.quality_pass_rate == 0.0

    def test_all_above_threshold(self):
        results = [_result(judge_score=0.9) for _ in range(3)]
        m = compute_metrics(results)
        assert m.quality_pass_rate == 1.0


# ---------------------------------------------------------------------------
# Latency percentiles
# ---------------------------------------------------------------------------

class TestLatencyPercentiles:
    def test_single_value(self):
        m = compute_metrics([_result(latency_ms=500)])
        assert m.latency_p50_ms == pytest.approx(500.0)
        assert m.latency_p95_ms == pytest.approx(500.0)

    def test_p50_midpoint(self):
        results = [_result(case_id=str(i), latency_ms=i * 100) for i in range(1, 11)]
        m = compute_metrics(results)
        assert m.latency_p50_ms == pytest.approx(550.0, abs=1.0)

    def test_p95_near_max(self):
        results = [_result(case_id=str(i), latency_ms=i * 100) for i in range(1, 21)]
        m = compute_metrics(results)
        assert m.latency_p95_ms == pytest.approx(1950.0, abs=1.0)

    def test_mean_latency(self):
        results = [_result(case_id=str(i), latency_ms=i) for i in [100, 200, 300]]
        m = compute_metrics(results)
        assert m.latency_mean_ms == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Per-category breakdown
# ---------------------------------------------------------------------------

class TestPerCategoryBreakdown:
    def test_category_counts(self):
        results = [
            _result(case_id="s1", category="stats"),
            _result(case_id="s2", category="stats"),
            _result(case_id="r1", category="recommendations"),
        ]
        m = compute_metrics(results)
        cats = {cm.category: cm for cm in m.per_category}
        assert cats["stats"].count == 2
        assert cats["recommendations"].count == 1

    def test_safety_tool_accuracy_is_none(self):
        r = _result(case_id="safe1", category="safety", expected_tools=set(), actual_tools=set())
        m = compute_metrics([r])
        cat = next(cm for cm in m.per_category if cm.category == "safety")
        assert cat.tool_accuracy is None

    def test_non_safety_tool_accuracy_computed(self):
        r = _result(category="stats", expected_tools={"listening_stats"}, actual_tools={"listening_stats"})
        m = compute_metrics([r])
        cat = next(cm for cm in m.per_category if cm.category == "stats")
        assert cat.tool_accuracy == 1.0

    def test_per_category_mean_score(self):
        results = [
            _result(case_id="s1", category="stats", judge_score=0.6),
            _result(case_id="s2", category="stats", judge_score=0.8),
        ]
        m = compute_metrics(results)
        cat = next(cm for cm in m.per_category if cm.category == "stats")
        assert cat.judge_score_mean == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Empty results guard
# ---------------------------------------------------------------------------

class TestEmptyResults:
    def test_empty_returns_zero_metrics(self):
        m = compute_metrics([])
        assert m.total_cases == 0
        assert m.tool_accuracy == 0.0
        assert m.safety_pass_rate == 1.0  # vacuously true


# ---------------------------------------------------------------------------
# Judge JSON parsing (unit — no LLM call)
# ---------------------------------------------------------------------------

class TestJudgeJsonParsing:
    """Test the parsing logic in runner.judge_answer without calling the LLM."""

    def _parse(self, raw: str) -> tuple[float, str]:
        """Inline the parsing logic from runner.judge_answer."""
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw).strip()
        try:
            parsed = json.loads(raw)
            score = float(parsed.get("score", 0.0))
            score = max(0.0, min(1.0, score))
            reasoning = str(parsed.get("reasoning", ""))
            return score, reasoning
        except (json.JSONDecodeError, KeyError, ValueError):
            return 0.0, f"Parse error — raw response: {raw[:300]}"

    def test_valid_json(self):
        score, reasoning = self._parse('{"score": 0.85, "reasoning": "Good answer."}')
        assert score == pytest.approx(0.85)
        assert reasoning == "Good answer."

    def test_code_fence_stripped(self):
        score, _ = self._parse('```json\n{"score": 0.7, "reasoning": "ok"}\n```')
        assert score == pytest.approx(0.7)

    def test_score_clamped_above_1(self):
        score, _ = self._parse('{"score": 1.5, "reasoning": "x"}')
        assert score == 1.0

    def test_score_clamped_below_0(self):
        score, _ = self._parse('{"score": -0.2, "reasoning": "x"}')
        assert score == 0.0

    def test_malformed_json_returns_zero(self):
        score, reasoning = self._parse("not valid json")
        assert score == 0.0
        assert "Parse error" in reasoning

    def test_missing_score_defaults_zero(self):
        score, _ = self._parse('{"reasoning": "no score field"}')
        assert score == 0.0


# ---------------------------------------------------------------------------
# Case list invariants
# ---------------------------------------------------------------------------

class TestCaseList:
    def test_at_least_32_cases(self):
        assert len(CASES) >= 32

    def test_all_case_ids_unique(self):
        ids = [c.case_id for c in CASES]
        assert len(ids) == len(set(ids))

    def test_all_required_categories_present(self):
        categories = {c.category for c in CASES}
        required = {"stats", "sql", "recommendations", "semantic", "profile", "multi_tool", "no_tool", "safety"}
        assert required <= categories

    def test_stats_at_least_6(self):
        assert sum(1 for c in CASES if c.category == "stats") >= 6

    def test_sql_at_least_5(self):
        assert sum(1 for c in CASES if c.category == "sql") >= 5

    def test_safety_at_least_4(self):
        assert sum(1 for c in CASES if c.category == "safety") >= 4

    def test_no_tool_cases_have_empty_expected_tools(self):
        for c in CASES:
            if c.category == "no_tool":
                assert c.expected_tools == set(), f"{c.case_id} should have empty expected_tools"

    def test_safety_cases_have_empty_expected_tools(self):
        for c in CASES:
            if c.category == "safety":
                assert c.expected_tools == set(), f"{c.case_id} should have empty expected_tools"

    def test_no_tool_followup_has_setup_turns(self):
        followup = next(c for c in CASES if c.case_id == "no_tool_followup_clarification")
        assert len(followup.setup_turns) >= 1

    def test_multi_tool_cases_expect_multiple_tools(self):
        for c in CASES:
            if c.category == "multi_tool":
                assert len(c.expected_tools) >= 2, f"{c.case_id} should expect >= 2 tools"
