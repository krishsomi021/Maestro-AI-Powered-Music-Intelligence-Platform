"""
Eval report — renders a results table to stdout and writes a markdown file.

Safety rows use safety_passed for the ✓/✗ column instead of tool subset check.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from eval.metrics import QUALITY_PASS_THRESHOLD

if TYPE_CHECKING:
    from eval.metrics import MetricsReport
    from eval.result import EvalResult


def render(
    results: list[EvalResult],
    metrics: MetricsReport,
    run_name: str,
    generator_model: str,
    output_path: str,
) -> None:
    from app.config import settings as app_settings

    md = _build_markdown(results, metrics, run_name, generator_model, app_settings.agent_judge_model)
    print(md)
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    print(f"\nResults written to {output_path}")


def _build_markdown(
    results: list[EvalResult],
    metrics: MetricsReport,
    run_name: str,
    generator_model: str,
    judge_model: str,
) -> str:
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    # --- summary block ------------------------------------------------------
    lines += [
        f"# Eval Run: {run_name}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Timestamp | {now} |",
        f"| Generator model | `{generator_model}` |",
        f"| Judge model | `{judge_model}` |",
        f"| Total cases | {metrics.total_cases} |",
        f"| Tool accuracy | {metrics.tool_accuracy:.1%} |",
        f"| Over-calling rate | {metrics.over_calling_rate:.1%} |",
        f"| Safety pass rate | {metrics.safety_pass_rate:.1%} |",
        f"| Mean judge score | {metrics.judge_score_mean:.3f} |",
        f"| Median judge score | {metrics.judge_score_median:.3f} |",
        f"| Quality pass rate (≥{QUALITY_PASS_THRESHOLD}) | {metrics.quality_pass_rate:.1%} |",
        f"| Latency mean | {metrics.latency_mean_ms:.0f} ms |",
        f"| Latency p50 | {metrics.latency_p50_ms:.0f} ms |",
        f"| Latency p95 | {metrics.latency_p95_ms:.0f} ms |",
        "",
    ]

    # --- per-case table -----------------------------------------------------
    lines += [
        "## Per-Case Results",
        "",
        "| case_id | category | expected tools | actual tools | ✓/✗ | judge score | latency ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        expected_str = ", ".join(sorted(r.case.expected_tools)) or "—"
        actual_str = ", ".join(sorted(r.actual_tools)) or "—"

        if r.case.category == "safety":
            ok = "✓" if r.safety_passed else "✗"
        else:
            ok = "✓" if r.case.expected_tools <= r.actual_tools else "✗"

        error_flag = " ⚠" if r.error else ""
        lines.append(
            f"| {r.case.case_id} | {r.case.category} | {expected_str} | {actual_str}"
            f" | {ok} | {r.judge_score:.3f} | {r.latency_ms}{error_flag} |"
        )
    lines.append("")

    # --- per-category summary -----------------------------------------------
    lines += [
        "## Per-Category Summary",
        "",
        "| category | N | tool accuracy | mean judge score |",
        "|---|---|---|---|",
    ]
    for cm in metrics.per_category:
        tool_str = f"{cm.tool_accuracy:.1%}" if cm.tool_accuracy is not None else "N/A"
        lines.append(
            f"| {cm.category} | {cm.count} | {tool_str} | {cm.judge_score_mean:.3f} |"
        )
    lines.append("")

    return "\n".join(lines)
