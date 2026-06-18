"""SigLab orchestration package.

Lazy ``__getattr__`` avoids a circular import: runner modules import
``siglab.workspace.builder`` at module level, while ``workspace.builder``
imports ``siglab.orchestration.trials``. Eagerly importing the runners
here would trigger that cycle before ``workspace.builder`` is ready.
"""

from typing import Any

# name -> submodule that defines it; resolved on first attribute access.
_LAZY: dict[str, str] = {
    "OptimizationResult": "siglab.orchestration.optimizer_runner",
    "OptimizerOutput": "siglab.orchestration.contracts",
    "OptunaOptimizerRunner": "siglab.orchestration.optimizer_runner",
    "PlannerOutput": "siglab.orchestration.contracts",
    "PlannerResult": "siglab.orchestration.planner_runner",
    "PreflightResult": "siglab.orchestration.contracts",
    "ReflectionRunner": "siglab.orchestration.reflector_runner",
    "ReflectorOutput": "siglab.orchestration.contracts",
    "ResearchPlannerRunner": "siglab.orchestration.planner_runner",
    "SpecWriterRunner": "siglab.orchestration.writer_runner",
    "WorkspaceHooks": "siglab.orchestration.hooks",
    "WriterOutput": "siglab.orchestration.contracts",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module 'siglab.orchestration' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value
