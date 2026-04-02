"""Store Apollo taxonomy in PostgreSQL with pgvector embeddings.

Revision ID: 012_apollo_taxonomy_db
Revises: 011_offer_alignment
"""
from alembic import op
import sqlalchemy as sa

revision = "012_apollo_taxonomy_db"
down_revision = "011_offer_alignment"


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "apollo_taxonomy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("term_type", sa.String(20), nullable=False),
        sa.Column("seen_count", sa.Integer(), server_default="1"),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("last_segment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("term", "term_type", name="uq_taxonomy_term_type"),
    )
    op.execute("ALTER TABLE apollo_taxonomy ADD COLUMN embedding vector(1536)")
    op.create_index("ix_taxonomy_type", "apollo_taxonomy", ["term_type"])


def downgrade():
    op.drop_table("apollo_taxonomy")
