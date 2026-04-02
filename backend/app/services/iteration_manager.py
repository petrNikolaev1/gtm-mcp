"""Iteration Manager — tracks pipeline processing rule changes.

Each add/remove/modify of a processing step creates a new PipelineIteration
with a full snapshot of active steps. Historical iterations are always
viewable in the UI with their original columns.

Usage:
    mgr = IterationManager()
    iteration = await mgr.add_step(session, project_id, step_config)
    iteration = await mgr.remove_step(session, project_id, step_name)
    iterations = await mgr.list_iterations(session, project_id)
    columns = await mgr.get_iteration_columns(session, iteration_id)
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.processing_step import PipelineIteration, ProcessingStep
from app.services.step_executor import (
    detect_step_type, build_regex_config, build_filter_config,
    ESSENTIAL_STEP_NAMES, ESSENTIAL_COLUMNS,
)

logger = logging.getLogger(__name__)


class IterationManager:
    """Manages pipeline iterations and processing steps."""

    async def get_current_steps(
        self, session: AsyncSession, project_id: int
    ) -> List[Dict[str, Any]]:
        """Get the currently active processing steps for a project."""
        result = await session.execute(
            select(ProcessingStep)
            .where(ProcessingStep.project_id == project_id, ProcessingStep.is_active == True)
            .order_by(ProcessingStep.step_number)
        )
        steps = result.scalars().all()
        return [self._step_to_dict(s) for s in steps]

    async def get_latest_iteration(
        self, session: AsyncSession, project_id: int
    ) -> Optional[PipelineIteration]:
        """Get the most recent iteration for a project."""
        result = await session.execute(
            select(PipelineIteration)
            .where(PipelineIteration.project_id == project_id)
            .order_by(PipelineIteration.iteration_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _next_iteration_number(self, session: AsyncSession, project_id: int) -> int:
        result = await session.execute(
            select(func.coalesce(func.max(PipelineIteration.iteration_number), 0))
            .where(PipelineIteration.project_id == project_id)
        )
        return (result.scalar() or 0) + 1

    async def add_step(
        self,
        session: AsyncSession,
        project_id: int,
        name: str,
        description: str,
        output_column: Optional[str] = None,
        config: Optional[Dict] = None,
        step_type: Optional[str] = None,
        gathering_run_id: Optional[int] = None,
        created_by: str = "mcp",
    ) -> PipelineIteration:
        """Add a processing step and create a new iteration.

        If step_type is not provided, auto-detects from description.
        If config is not provided, auto-builds from description and step_type.
        """
        # Auto-detect step type
        if not step_type:
            step_type = detect_step_type(description)

        # Auto-build config
        if not config:
            if step_type == "regex":
                config = build_regex_config(description, name)
            elif step_type == "filter":
                config = build_filter_config(description)
            elif step_type == "ai":
                config = {"prompt": description, "model": "gpt-4o-mini"}
            elif step_type == "scrape":
                config = {"page_paths": ["/"], "max_pages": 1}
            else:
                config = {}

        # Default output column from name
        if not output_column and step_type != "filter":
            output_column = name.lower().replace(" ", "_").replace("-", "_")

        # Get current active steps
        current_steps = await self.get_current_steps(session, project_id)

        # Create new step
        step_number = len(current_steps) + 1
        iter_num = await self._next_iteration_number(session, project_id)

        iteration = PipelineIteration(
            project_id=project_id,
            gathering_run_id=gathering_run_id,
            iteration_number=iter_num,
            label=f"Added column: {output_column or name}",
            trigger="add_step",
            steps_snapshot=current_steps + [{
                "name": name,
                "step_number": step_number,
                "output_column": output_column,
                "step_type": step_type,
                "config": config,
                "is_essential": False,
            }],
            change_detail={
                "action": "add",
                "step_name": name,
                "output_column": output_column,
                "step_type": step_type,
            },
            status="pending",
            columns_count=len(current_steps) + 1,
            created_by=created_by,
        )
        session.add(iteration)
        await session.flush()

        step = ProcessingStep(
            iteration_id=iteration.id,
            project_id=project_id,
            step_number=step_number,
            name=name,
            output_column=output_column,
            step_type=step_type,
            config=config,
            is_essential=False,
        )
        session.add(step)
        await session.flush()

        logger.info(f"Added step '{name}' ({step_type}) → iteration {iter_num} for project {project_id}")
        return iteration

    async def remove_step(
        self,
        session: AsyncSession,
        project_id: int,
        step_name: str,
        created_by: str = "mcp",
    ) -> PipelineIteration:
        """Remove a processing step and create a new iteration.

        Essential steps cannot be removed.
        """
        # Find the step
        result = await session.execute(
            select(ProcessingStep).where(
                ProcessingStep.project_id == project_id,
                ProcessingStep.name == step_name,
                ProcessingStep.is_active == True,
            )
        )
        step = result.scalar_one_or_none()
        if not step:
            raise ValueError(f"Step '{step_name}' not found or already removed")

        if step.is_essential:
            raise ValueError(f"Cannot remove essential step '{step_name}'")

        # Soft-delete the step
        step.is_active = False

        # Get remaining active steps
        remaining = await self.get_current_steps(session, project_id)

        iter_num = await self._next_iteration_number(session, project_id)

        iteration = PipelineIteration(
            project_id=project_id,
            iteration_number=iter_num,
            label=f"Removed column: {step.output_column or step_name}",
            trigger="remove_step",
            steps_snapshot=remaining,
            change_detail={
                "action": "remove",
                "step_name": step_name,
                "output_column": step.output_column,
                "step_type": step.step_type,
            },
            status="completed",
            columns_count=len(remaining),
            created_by=created_by,
        )
        session.add(iteration)
        await session.flush()

        logger.info(f"Removed step '{step_name}' → iteration {iter_num} for project {project_id}")
        return iteration

    async def list_iterations(
        self, session: AsyncSession, project_id: int
    ) -> List[Dict[str, Any]]:
        """List all iterations for a project (for the iteration selector dropdown)."""
        result = await session.execute(
            select(PipelineIteration)
            .where(PipelineIteration.project_id == project_id)
            .order_by(PipelineIteration.iteration_number.desc())
        )
        iterations = result.scalars().all()
        return [
            {
                "id": it.id,
                "number": it.iteration_number,
                "label": it.label,
                "trigger": it.trigger,
                "columns_count": it.columns_count,
                "status": it.status,
                "columns": [s.get("output_column") for s in (it.steps_snapshot or []) if s.get("output_column")],
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in iterations
        ]

    async def get_iteration_columns(
        self, session: AsyncSession, iteration_id: int
    ) -> List[str]:
        """Get the custom columns that existed at a specific iteration."""
        iteration = await session.get(PipelineIteration, iteration_id)
        if not iteration:
            return []
        return [
            s.get("output_column")
            for s in (iteration.steps_snapshot or [])
            if s.get("output_column")
        ]

    def _step_to_dict(self, step: ProcessingStep) -> Dict[str, Any]:
        return {
            "id": step.id,
            "name": step.name,
            "step_number": step.step_number,
            "output_column": step.output_column,
            "step_type": step.step_type,
            "config": step.config,
            "is_essential": step.is_essential,
            "is_active": step.is_active,
        }
