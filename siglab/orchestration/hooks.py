from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from siglab.workspace.builder import WorkspaceBuilder, WorkspaceSession


@dataclass
class WorkspaceHooks:
    builder: WorkspaceBuilder
    session: WorkspaceSession

    def after_experiment(self, *, spec_hash: str, iteration_number: int) -> str | None:
        card_ref = self.builder.record_experiment(
            session=self.session,
            spec_hash=spec_hash,
            iteration_number=iteration_number,
        )
        self.builder.refresh_frontier_files(self.session)
        return card_ref

    def after_reflection(self) -> None:
        self.builder.refresh_frontier_files(self.session)

