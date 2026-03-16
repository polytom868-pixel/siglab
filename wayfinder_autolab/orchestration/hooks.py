from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wayfinder_autolab.workspace.builder import WorkspaceBuilder, WorkspaceSession


@dataclass
class WorkspaceHooks:
    builder: WorkspaceBuilder
    session: WorkspaceSession

    def after_experiment(self, *, candidate_hash: str, iteration_number: int) -> str | None:
        card_ref = self.builder.record_experiment(
            session=self.session,
            candidate_hash=candidate_hash,
            iteration_number=iteration_number,
        )
        self.builder.refresh_frontier_files(self.session)
        return card_ref

    def after_reflection(self) -> None:
        self.builder.refresh_frontier_files(self.session)
