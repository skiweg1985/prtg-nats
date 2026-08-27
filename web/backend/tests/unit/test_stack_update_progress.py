"""What the operator sees while an update runs.

The first live run produced a log that was accurate and useless: eight rows
all saying "output from the updater", each hiding its content behind a
disclosure control, and every one of them filed under "moving the checkout" -
including the ones written while the build was running.

Both had the same cause. After handing over to the updater the handler stopped
calling step(), so the step list froze at whatever was current, and the batch
of output was logged under one fixed message that said nothing about what was
in it. The step list still went green at the end, which made it worse: it
claimed a sequence nobody had recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.workers.handlers.stack_update import STEPS, _report


@dataclass
class Recorder:
    """Just enough JobContext to see what _report does."""

    steps: list[str] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)

    async def step(self, name: str) -> None:
        self.steps.append(name)

    async def log(self, code: str, **kwargs: Any) -> None:
        self.logs.append({"code": code, **kwargs})


async def test_the_phase_markers_advance_the_step_list() -> None:
    """The steps follow the updater instead of freezing at the handover."""
    context: Any = Recorder()

    await _report(
        context,
        [
            "::phase build",
            "Building the images...",
            "#12 exporting layers",
            "::phase recreate",
            "Recreating the stack...",
        ],
    )

    assert context.steps == ["build", "recreate"]


async def test_a_marker_never_becomes_visible_output() -> None:
    """It is a contract between two programs, not something to read."""
    context: Any = Recorder()

    await _report(context, ["::phase build", "Building the images..."])

    assert "::phase" not in context.logs[0]["raw"]
    assert "::phase" not in context.logs[0]["params"]["line"]


async def test_the_visible_line_is_the_output_not_a_label() -> None:
    """The most recent thing the tool said, rather than "output from the
    updater" eight times over. The whole batch stays as technical detail."""
    context: Any = Recorder()

    await _report(context, ["#12 exporting layers", "Build finished."])

    entry = context.logs[0]
    assert entry["params"]["line"] == "Build finished."
    assert "#12 exporting layers" in entry["raw"]


async def test_a_batch_of_only_markers_logs_nothing() -> None:
    """Otherwise every phase change would add an empty row to the log."""
    context: Any = Recorder()

    await _report(context, ["::phase build"])

    assert context.steps == ["build"]
    assert context.logs == []


async def test_an_unknown_phase_is_ignored_rather_than_set() -> None:
    """The updater and the step list live in different files.

    A phase this job never declared cannot be rendered, so a typo in one of
    them should leave the step list alone rather than move it somewhere the
    interface has no name for.
    """
    context: Any = Recorder()

    await _report(context, ["::phase teleport", "still working"])

    assert context.steps == []
    assert "teleport" not in STEPS
