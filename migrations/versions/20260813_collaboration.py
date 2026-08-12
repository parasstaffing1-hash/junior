"""Add tenant-scoped review and shared-collection collaboration."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_collaboration"
down_revision: Union[str, Sequence[str], None] = "20260813_model_tenants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "review_threads" not in tables:
        op.create_table(
            "review_threads",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("asset_id", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["asset_id"], ["workspace_assets.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("workspace_id", "workspace_id"), ("asset_id", "asset_id"), ("status", "status")):
            op.create_index(f"ix_review_threads_{name}", "review_threads", [column])
    if "review_comments" not in tables:
        op.create_table(
            "review_comments",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("author", sa.String(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["thread_id"], ["review_threads.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("thread_id", "thread_id")):
            op.create_index(f"ix_review_comments_{name}", "review_comments", [column])
    if "review_decisions" not in tables:
        op.create_table(
            "review_decisions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("approver", sa.String(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["thread_id"], ["review_threads.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("thread_id", "thread_id")):
            op.create_index(f"ix_review_decisions_{name}", "review_decisions", [column])
    if "workspace_collections" not in tables:
        op.create_table(
            "workspace_collections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("owner", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "workspace_id", "name", name="uq_workspace_collection_name"),
        )
        for name, column in (("tenant_id", "tenant_id"), ("workspace_id", "workspace_id")):
            op.create_index(f"ix_workspace_collections_{name}", "workspace_collections", [column])
    if "workspace_collection_items" not in tables:
        op.create_table(
            "workspace_collection_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("collection_id", sa.String(), nullable=False),
            sa.Column("asset_id", sa.String(), nullable=False),
            sa.Column("added_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["collection_id"], ["workspace_collections.id"]),
            sa.ForeignKeyConstraint(["asset_id"], ["workspace_assets.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("collection_id", "asset_id", name="uq_workspace_collection_asset"),
        )
        op.create_index("ix_workspace_collection_items_collection_id", "workspace_collection_items", ["collection_id"])
        op.create_index("ix_workspace_collection_items_asset_id", "workspace_collection_items", ["asset_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "workspace_collection_items" in inspector.get_table_names():
        op.drop_index("ix_workspace_collection_items_asset_id", table_name="workspace_collection_items")
        op.drop_index("ix_workspace_collection_items_collection_id", table_name="workspace_collection_items")
        op.drop_table("workspace_collection_items")
    if "workspace_collections" in inspector.get_table_names():
        op.drop_index("ix_workspace_collections_workspace_id", table_name="workspace_collections")
        op.drop_index("ix_workspace_collections_tenant_id", table_name="workspace_collections")
        op.drop_table("workspace_collections")
    for table, indexes in (("review_decisions", ("thread_id", "tenant_id")), ("review_comments", ("thread_id", "tenant_id")), ("review_threads", ("status", "asset_id", "workspace_id", "tenant_id"))):
        if table in inspector.get_table_names():
            for name in indexes:
                op.drop_index(f"ix_{table}_{name}", table_name=table)
            op.drop_table(table)
