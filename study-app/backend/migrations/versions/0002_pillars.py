"""Pillars: knowledge-graph tables (both dialects), vector storage + RLS (Postgres).

- concepts + concept_edges: the knowledge/skill graph — portable, created
  on SQLite too (the graph endpoints work in dev).
- document_chunks + pgvector extension + HNSW index: Postgres-only. The
  embeddings pipeline that fills it is a follow-up; the table starts empty.
- ENABLE ROW LEVEL SECURITY on every table when on Postgres/Supabase:
  Supabase exposes tables through its public PostgREST API with the anon
  key. No policies are created (deny-all for anon/authenticated); the app
  connects as the table owner, which bypasses RLS.

Revision ID: pillars
Revises: baseline
Create Date: 2026-08-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "pillars"
down_revision: Union[str, None] = "baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_TABLES = (
    "modules",
    "lessons",
    "documents",
    "content_items",
    "quiz_attempts",
    "agent_memory",
    "recommendation_events",
    "agent_events",
    "user_activities",
    "study_plans",
    "lecture_sessions",
    "concepts",
    "concept_edges",
    "document_chunks",
)


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    op.create_table(
        "concepts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_concepts_user_name"),
    )
    with op.batch_alter_table("concepts") as batch_op:
        batch_op.create_index(batch_op.f("ix_concepts_user_id"), ["user_id"], unique=False)

    op.create_table(
        "concept_edges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "target_id", "relation", name="uq_concept_edges_str"),
    )
    with op.batch_alter_table("concept_edges") as batch_op:
        batch_op.create_index(batch_op.f("ix_concept_edges_source_id"), ["source_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_target_id"), ["target_id"], unique=False)

    if is_pg:
        # pgvector + the retrieval table. sqlite dev/test skip this —
        # nothing queries document_chunks until the embeddings pipeline
        # lands, and it will only ever run against Postgres.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("document_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("embedding", Vector(384), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("document_chunks") as batch_op:
            batch_op.create_index(batch_op.f("ix_document_chunks_document_id"), ["document_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_document_chunks_user_id"), ["user_id"], unique=False)
        op.create_index(
            "ix_document_chunks_embedding_hnsw",
            "document_chunks",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

        # Deny-all at the Supabase API boundary; the app's direct
        # connection (table owner) is unaffected.
        for table in _ALL_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    if is_pg:
        for table in _ALL_TABLES:
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table("document_chunks")
    op.drop_table("concept_edges")
    op.drop_table("concepts")
