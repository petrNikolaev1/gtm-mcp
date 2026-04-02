"""Add pipeline_iterations + processing_steps tables

Revision ID: 008_iterations
Revises: 007_constraints
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '008_iterations'
down_revision = '007_constraints'


def upgrade() -> None:
    op.create_table(
        'pipeline_iterations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('gathering_run_id', sa.Integer(), sa.ForeignKey('gathering_runs.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('iteration_number', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(255), nullable=True),
        sa.Column('trigger', sa.String(100), nullable=False),
        sa.Column('steps_snapshot', JSONB, nullable=False),
        sa.Column('change_detail', JSONB, nullable=True),
        sa.Column('filters_snapshot', JSONB, nullable=True),
        sa.Column('prompt_snapshot', sa.Text(), nullable=True),
        sa.Column('target_count', sa.Integer(), nullable=True),
        sa.Column('target_rate', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'processing_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('iteration_id', sa.Integer(), sa.ForeignKey('pipeline_iterations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('step_type', sa.String(30), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('config', JSONB, nullable=True),
        sa.Column('output_column', sa.String(100), nullable=True),
        sa.Column('is_essential', sa.Boolean(), server_default='false'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('processing_steps')
    op.drop_table('pipeline_iterations')
