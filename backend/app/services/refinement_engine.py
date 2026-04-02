"""Self-Refinement Engine — THE KEY DIFFERENTIATOR.

Autonomous prompt improvement loop:
1. GPT-4o-mini analyzes companies with current prompt
2. Stratified sample: 20 targets + 10 borderline + 10 rejected
3. GPT-4o independently verifies (skeptical verifier)
4. Calculate accuracy = (TP + TN) / total
5. If >= target: DONE
6. Extract FP/FN patterns
7. Gemini 2.5 Pro improves prompt
8. Re-analyze → repeat (max 8 iterations)

Cost per run (~100 companies, ~5 iters): ~$6
"""
import logging
import random
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.refinement import RefinementRun, RefinementIteration
from app.models.gathering import GatheringRun, AnalysisResult, AnalysisRun

logger = logging.getLogger(__name__)


class RefinementEngine:
    """Autonomous self-refinement loop for ICP analysis."""

    def __init__(self, openai_key: Optional[str] = None, gemini_key: Optional[str] = None):
        self._openai_key = openai_key
        self._gemini_key = gemini_key

    async def run_refinement(
        self,
        session: AsyncSession,
        gathering_run_id: int,
        project_id: int,
        initial_prompt: str,
        target_accuracy: float = 0.9,
        max_iterations: int = 8,
    ) -> RefinementRun:
        """Execute the full refinement loop."""

        # Create refinement run record
        ref_run = RefinementRun(
            gathering_run_id=gathering_run_id,
            project_id=project_id,
            target_accuracy=target_accuracy,
            max_iterations=max_iterations,
            status="running",
        )
        session.add(ref_run)
        await session.flush()

        current_prompt = initial_prompt

        for iteration in range(1, max_iterations + 1):
            ref_run.current_iteration = iteration
            logger.info(f"Refinement iteration {iteration}/{max_iterations}")

            # Step 1: Get analysis results from latest analysis run
            analysis_results = await self._get_analysis_results(session, gathering_run_id)
            if not analysis_results:
                logger.warning("No analysis results to refine")
                ref_run.status = "failed"
                break

            # Step 2: Stratified sampling
            sample = self._stratified_sample(analysis_results)

            # Step 3: Independent verification (GPT-4o)
            verification = await self._verify_sample(sample, current_prompt)

            # Step 4: Calculate accuracy
            accuracy = self._calculate_accuracy(verification)

            # Step 5: Record iteration
            iter_record = RefinementIteration(
                refinement_run_id=ref_run.id,
                iteration_number=iteration,
                accuracy=accuracy,
                true_positives=verification.get("tp", 0),
                true_negatives=verification.get("tn", 0),
                false_positives=verification.get("fp", 0),
                false_negatives=verification.get("fn", 0),
                sample_size=len(sample),
                false_positive_patterns=verification.get("fp_patterns", []),
                false_negative_patterns=verification.get("fn_patterns", []),
            )
            session.add(iter_record)
            ref_run.final_accuracy = accuracy

            logger.info(f"Iteration {iteration}: accuracy={accuracy:.1%}")

            # Step 6: Check convergence
            if accuracy >= target_accuracy:
                ref_run.status = "converged"
                ref_run.completed_at = datetime.utcnow()
                logger.info(f"Converged at iteration {iteration} with {accuracy:.1%} accuracy")
                break

            # Step 7: Improve prompt (Gemini 2.5 Pro)
            improved_prompt = await self._improve_prompt(
                current_prompt, accuracy, verification.get("fp_patterns", []),
                verification.get("fn_patterns", []),
            )
            if improved_prompt:
                current_prompt = improved_prompt
                iter_record.prompt_adjustments = f"Prompt improved: {len(improved_prompt)} chars"

        else:
            ref_run.status = "max_iterations"
            ref_run.completed_at = datetime.utcnow()

        return ref_run

    async def _get_analysis_results(self, session: AsyncSession, gathering_run_id: int) -> List[Dict]:
        """Get the latest analysis results for this gathering run."""
        run = await session.get(GatheringRun, gathering_run_id)
        if not run:
            return []
        # Get latest analysis run for this project
        result = await session.execute(
            select(AnalysisRun).where(AnalysisRun.project_id == run.project_id)
            .order_by(AnalysisRun.created_at.desc()).limit(1)
        )
        analysis_run = result.scalar_one_or_none()
        if not analysis_run:
            return []
        results = await session.execute(
            select(AnalysisResult).where(AnalysisResult.analysis_run_id == analysis_run.id)
        )
        return [
            {
                "id": r.id,
                "is_target": r.is_target,
                "confidence": r.confidence or 0,
                "reasoning": r.reasoning,
                "segment": r.segment,
            }
            for r in results.scalars().all()
        ]

    def _stratified_sample(self, results: List[Dict], n_targets: int = 20, n_borderline: int = 10, n_rejected: int = 10) -> List[Dict]:
        """Stratified sampling: targets + borderline + rejected."""
        targets = [r for r in results if r["is_target"]]
        borderline = [r for r in results if 0.4 <= r["confidence"] <= 0.6]
        rejected = [r for r in results if not r["is_target"]]

        sample = []
        sample.extend(random.sample(targets, min(n_targets, len(targets))))
        sample.extend(random.sample(borderline, min(n_borderline, len(borderline))))
        sample.extend(random.sample(rejected, min(n_rejected, len(rejected))))
        return sample

    async def _verify_sample(self, sample: List[Dict], prompt: str) -> Dict[str, Any]:
        """Independent verification using GPT-4o (skeptical verifier)."""
        # TODO: Implement actual GPT-4o verification calls
        # For now, simulate verification results
        tp = sum(1 for s in sample if s["is_target"] and s["confidence"] > 0.7)
        tn = sum(1 for s in sample if not s["is_target"] and s["confidence"] < 0.4)
        fp = sum(1 for s in sample if s["is_target"] and s["confidence"] <= 0.7)
        fn = sum(1 for s in sample if not s["is_target"] and s["confidence"] >= 0.4)

        return {
            "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "fp_patterns": ["Marketing agencies serving SaaS clients"] if fp > 0 else [],
            "fn_patterns": ["SaaS product described indirectly"] if fn > 0 else [],
        }

    def _calculate_accuracy(self, verification: Dict) -> float:
        total = verification["tp"] + verification["tn"] + verification["fp"] + verification["fn"]
        if total == 0:
            return 0.0
        return (verification["tp"] + verification["tn"]) / total

    async def _improve_prompt(self, current_prompt: str, accuracy: float,
                               fp_patterns: List[str], fn_patterns: List[str]) -> Optional[str]:
        """Use Gemini 2.5 Pro to improve the analysis prompt."""
        # TODO: Implement actual Gemini call
        # For now, return improved prompt with pattern exclusions
        additions = []
        if fp_patterns:
            additions.append(f"\n\nEXCLUDE these false positive patterns:\n" + "\n".join(f"- {p}" for p in fp_patterns))
        if fn_patterns:
            additions.append(f"\n\nINCLUDE these missed patterns:\n" + "\n".join(f"- {p}" for p in fn_patterns))

        if additions:
            return current_prompt + "".join(additions)
        return None
