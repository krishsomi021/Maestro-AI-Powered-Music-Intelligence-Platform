"""
Agent proof-of-concept script.

Runs a single-turn conversation through the full agent stack against a live DB.
Streams events to stdout so you can verify the loop, prompt, tools, and LLM
are wired together correctly end-to-end.

Usage (from project root, with the stack running):

    docker compose exec api python scripts/agent_poc.py
    docker compose exec api python scripts/agent_poc.py --question "Who are my top 5 artists?"
    docker compose exec api python scripts/agent_poc.py --model claude-sonnet-4-6

Requirements: ANTHROPIC_API_KEY must be set in .env / environment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

# Ensure the app package is importable when run from the project root.
sys.path.insert(0, ".")


async def main(question: str, model: str | None) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.agent.loop import AgentLoop, TextChunkEvent, ToolStartEvent, ToolEndEvent, DoneEvent, ErrorEvent
    from app.agent.prompts import PromptBuilder
    from app.llm.factory import get_llm_provider
    from app.tools.registry import build_default_registry

    resolved_model = model or settings.agent_model
    print(f"[poc] model={resolved_model}  question={question!r}\n", flush=True)

    engine = create_async_engine(settings.async_database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Build prompt
        t0 = time.monotonic()
        system_prompt = await PromptBuilder(settings).build_system_prompt(db)
        print(f"[poc] system prompt built ({len(system_prompt)} chars, {time.monotonic()-t0:.2f}s)\n", flush=True)

        # Build tool registry
        registry = build_default_registry(db=db, settings=settings)
        print(f"[poc] tools registered: {[s['name'] for s in registry.schemas]}\n", flush=True)

        # Build loop
        llm = get_llm_provider(model=resolved_model)
        loop = AgentLoop(
            llm=llm,
            tool_registry=registry,
            max_iterations=settings.agent_max_iterations,
        )

        messages = [{"role": "user", "content": question}]

        print("─" * 60)
        t_start = time.monotonic()
        async for event in loop.run(messages, system=system_prompt, session_id="poc"):
            if isinstance(event, TextChunkEvent):
                print(event.text, end="", flush=True)

            elif isinstance(event, ToolStartEvent):
                print(
                    f"\n[tool_start] iter={event.iteration} "
                    f"tool={event.tool_name} input={event.tool_input}",
                    flush=True,
                )

            elif isinstance(event, ToolEndEvent):
                status = "ok" if event.success else f"ERROR: {event.error}"
                chart = f" chart={event.chart_spec['chart_type']!r}" if event.chart_spec else ""
                print(
                    f"[tool_end]   iter={event.iteration} "
                    f"tool={event.tool_name} {status}{chart} latency={event.latency_ms}ms",
                    flush=True,
                )

            elif isinstance(event, DoneEvent):
                elapsed = time.monotonic() - t_start
                print(
                    f"\n{'─'*60}\n[done] {event.total_iterations} iteration(s), {elapsed:.2f}s total",
                    flush=True,
                )

            elif isinstance(event, ErrorEvent):
                print(f"\n[error] {event.message}", flush=True)
                sys.exit(1)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent proof-of-concept")
    parser.add_argument(
        "--question",
        default="How many tracks have I listened to in total?",
        help="Question to ask the agent",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the LLM model (e.g. claude-sonnet-4-6)",
    )
    args = parser.parse_args()
    asyncio.run(main(question=args.question, model=args.model))
