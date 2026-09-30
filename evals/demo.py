"""Run the short, deterministic interview demonstration."""

import asyncio

from evals.run import run_suite


async def main() -> None:
    summary = await run_suite(
        {"edit-and-test", "resume-interrupted-call", "pause-for-approval"}
    )
    for task in summary["tasks"]:
        print(
            f"{task['id']}: status={task['status']} steps={task['steps']} "
            f"tool_calls={task['tool_calls']} "
            f"recovered_interrupted_calls={task['recovered_interrupted_calls']}"
        )
    print(
        "Demo summary: "
        f"{summary['success_rate']:.0%} success, "
        f"{summary['successful_recoveries']} recovery case(s), "
        f"{summary['human_review_pauses']} approval pause(s)."
    )


if __name__ == "__main__":
    asyncio.run(main())
