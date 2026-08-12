"""Add LNG commercial domain foundation.

Revision ID: 20260812_lng_foundation
Revises: 20260811_workspace_assets
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260812_lng_foundation"
down_revision: Union[str, Sequence[str], None] = "20260811_workspace_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lng_companies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("legal_name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("company_type", sa.String(), nullable=False),
        sa.Column("country", sa.String()),
        sa.Column("website", sa.String()),
        sa.Column("lei", sa.String()),
        sa.Column("registration_number", sa.String()),
        sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("kyc_status", sa.String(), nullable=False),
        sa.Column("sanctions_screening_status", sa.String(), nullable=False),
        sa.Column("source_url", sa.String()),
        sa.Column("source_type", sa.String()),
        sa.Column("retrieved_at", sa.DateTime()),
        sa.Column("last_verified", sa.DateTime()),
        sa.Column("confidence", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    for column in ("legal_name", "normalized_name", "company_type", "country", "lei", "verification_status", "kyc_status", "sanctions_screening_status"):
        op.create_index(f"ix_lng_companies_{column}", "lng_companies", [column])

    op.create_table(
        "lng_specifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String()), sa.Column("origin", sa.String()),
        sa.Column("quality_basis", sa.String()), sa.Column("measurement_standard", sa.String()),
        sa.Column("hhv_mmbtu_per_mt", sa.Float()), sa.Column("lhv_mmbtu_per_mt", sa.Float()),
        sa.Column("density_mt_per_m3", sa.Float()), sa.Column("wobbe_index", sa.Float()),
        sa.Column("methane_pct", sa.Float()), sa.Column("ethane_pct", sa.Float()),
        sa.Column("propane_pct", sa.Float()), sa.Column("butane_pct", sa.Float()),
        sa.Column("nitrogen_pct", sa.Float()), sa.Column("carbon_dioxide_pct", sa.Float()),
        sa.Column("sulphur_specification", sa.String()), sa.Column("water_specification", sa.String()),
        sa.Column("certificate_reference", sa.String()), sa.Column("source_url", sa.String()),
        sa.Column("verification_status", sa.String(), nullable=False), sa.Column("last_verified", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "lng_terminals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("terminal_name", sa.String(), nullable=False), sa.Column("country", sa.String(), nullable=False),
        sa.Column("port", sa.String()), sa.Column("terminal_type", sa.String(), nullable=False),
        sa.Column("operator_company_id", sa.String(), sa.ForeignKey("lng_companies.id")),
        sa.Column("status", sa.String(), nullable=False), sa.Column("capacity_mtpa", sa.Float()),
        sa.Column("capacity_bcm_per_year", sa.Float()), sa.Column("storage_capacity_m3", sa.Float()),
        sa.Column("berths", sa.Integer()), sa.Column("sendout_capacity", sa.Float()),
        sa.Column("third_party_access", sa.Boolean()), sa.Column("ship_size_constraints", sa.JSON()),
        sa.Column("quality_requirements", sa.JSON()), sa.Column("tariff_data", sa.JSON()),
        sa.Column("slot_booking_process", sa.Text()), sa.Column("source_url", sa.String()),
        sa.Column("source_type", sa.String()), sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("last_verified", sa.DateTime()), sa.Column("created_at", sa.DateTime()),
    )
    for column in ("terminal_name", "country", "terminal_type", "status"):
        op.create_index(f"ix_lng_terminals_{column}", "lng_terminals", [column])

    op.create_table(
        "lng_liquefaction_projects",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("project_name", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=False), sa.Column("operator_company_id", sa.String(), sa.ForeignKey("lng_companies.id")),
        sa.Column("loading_terminal_id", sa.String(), sa.ForeignKey("lng_terminals.id")),
        sa.Column("project_status", sa.String(), nullable=False), sa.Column("liquefaction_capacity_mtpa", sa.Float()),
        sa.Column("number_of_trains", sa.Integer()), sa.Column("train_capacity_mtpa", sa.Float()),
        sa.Column("commissioning_date", sa.Date()), sa.Column("expected_start_date", sa.Date()),
        sa.Column("feedgas_source", sa.String()), sa.Column("marketing_entities", sa.JSON()),
        sa.Column("contracted_capacity_mtpa", sa.Float()), sa.Column("uncontracted_capacity_mtpa", sa.Float()),
        sa.Column("marketing_basis", sa.String()), sa.Column("source_url", sa.String()),
        sa.Column("source_type", sa.String()), sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("last_verified", sa.DateTime()), sa.Column("created_at", sa.DateTime()),
    )
    for column in ("project_name", "country", "project_status"):
        op.create_index(f"ix_lng_liquefaction_projects_{column}", "lng_liquefaction_projects", [column])

    op.create_table(
        "lng_buyer_rfqs",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("buyer_company_id", sa.String(), sa.ForeignKey("lng_companies.id"), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=False), sa.Column("delivery_basis", sa.String(), nullable=False),
        sa.Column("destination_terminal_id", sa.String(), sa.ForeignKey("lng_terminals.id")), sa.Column("country", sa.String()),
        sa.Column("delivery_window_start", sa.Date()), sa.Column("delivery_window_end", sa.Date()),
        sa.Column("number_of_cargoes", sa.Integer()), sa.Column("cargo_volume_mt", sa.Float()), sa.Column("energy_mmbtu", sa.Float()),
        sa.Column("lng_specification_id", sa.String(), sa.ForeignKey("lng_specifications.id")),
        sa.Column("origin_preferences", sa.JSON()), sa.Column("origin_restrictions", sa.JSON()),
        sa.Column("pricing_basis", sa.String()), sa.Column("benchmark", sa.String()), sa.Column("target_price", sa.Float()),
        sa.Column("currency", sa.String(), nullable=False), sa.Column("payment_terms", sa.Text()), sa.Column("credit_requirements", sa.Text()),
        sa.Column("vessel_requirements", sa.JSON()), sa.Column("terminal_requirements", sa.JSON()), sa.Column("offer_deadline", sa.DateTime()),
        sa.Column("status", sa.String(), nullable=False), sa.Column("source_url", sa.String()),
        sa.Column("verification_status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime()), sa.Column("updated_at", sa.DateTime()),
    )
    for column in ("buyer_company_id", "transaction_type", "country", "delivery_window_start", "delivery_window_end", "status"):
        op.create_index(f"ix_lng_buyer_rfqs_{column}", "lng_buyer_rfqs", [column])

    op.create_table(
        "lng_seller_offers",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("seller_company_id", sa.String(), sa.ForeignKey("lng_companies.id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("lng_liquefaction_projects.id")),
        sa.Column("loading_terminal_id", sa.String(), sa.ForeignKey("lng_terminals.id")), sa.Column("origin", sa.String()),
        sa.Column("delivery_basis", sa.String(), nullable=False), sa.Column("available_window_start", sa.Date()), sa.Column("available_window_end", sa.Date()),
        sa.Column("cargo_volume_mt", sa.Float()), sa.Column("energy_mmbtu", sa.Float()),
        sa.Column("lng_specification_id", sa.String(), sa.ForeignKey("lng_specifications.id")),
        sa.Column("pricing_formula", sa.JSON()), sa.Column("benchmark", sa.String()), sa.Column("premium_discount", sa.Float()),
        sa.Column("fixed_price", sa.Float()), sa.Column("currency", sa.String(), nullable=False), sa.Column("payment_terms", sa.Text()),
        sa.Column("credit_requirements", sa.Text()), sa.Column("destination_restrictions", sa.JSON()), sa.Column("vessel_requirements", sa.JSON()),
        sa.Column("offer_date", sa.DateTime()), sa.Column("valid_until", sa.DateTime()), sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_url", sa.String()), sa.Column("verification_status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime()),
    )
    for column in ("seller_company_id", "origin", "available_window_start", "available_window_end", "status"):
        op.create_index(f"ix_lng_seller_offers_{column}", "lng_seller_offers", [column])

    op.create_table(
        "lng_cargoes",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("seller_company_id", sa.String(), sa.ForeignKey("lng_companies.id"), nullable=False),
        sa.Column("seller_offer_id", sa.String(), sa.ForeignKey("lng_seller_offers.id")), sa.Column("project_id", sa.String(), sa.ForeignKey("lng_liquefaction_projects.id")),
        sa.Column("loading_terminal_id", sa.String(), sa.ForeignKey("lng_terminals.id")), sa.Column("origin", sa.String()),
        sa.Column("loading_window_start", sa.Date()), sa.Column("loading_window_end", sa.Date()),
        sa.Column("expected_delivery_window_start", sa.Date()), sa.Column("expected_delivery_window_end", sa.Date()),
        sa.Column("volume_mt", sa.Float()), sa.Column("energy_mmbtu", sa.Float()),
        sa.Column("lng_specification_id", sa.String(), sa.ForeignKey("lng_specifications.id")), sa.Column("delivery_basis", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False), sa.Column("vessel_status", sa.String()),
        sa.Column("destination_terminal_id", sa.String(), sa.ForeignKey("lng_terminals.id")),
        sa.Column("buyer_company_id", sa.String(), sa.ForeignKey("lng_companies.id")),
        sa.Column("compliance_status", sa.String(), nullable=False), sa.Column("source_url", sa.String()),
        sa.Column("verification_status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime()), sa.Column("updated_at", sa.DateTime()),
    )
    for column in ("seller_company_id", "origin", "loading_window_start", "loading_window_end", "status", "compliance_status"):
        op.create_index(f"ix_lng_cargoes_{column}", "lng_cargoes", [column])

    op.create_table(
        "lng_matches",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("rfq_id", sa.String(), sa.ForeignKey("lng_buyer_rfqs.id"), nullable=False),
        sa.Column("cargo_id", sa.String(), sa.ForeignKey("lng_cargoes.id"), nullable=False), sa.Column("score", sa.Float(), nullable=False),
        sa.Column("overall_status", sa.String(), nullable=False), sa.Column("actionable", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False), sa.Column("critical_unknowns", sa.JSON()), sa.Column("created_at", sa.DateTime()),
    )
    for column in ("rfq_id", "cargo_id", "overall_status"):
        op.create_index(f"ix_lng_matches_{column}", "lng_matches", [column])


def downgrade() -> None:
    for table in ("lng_matches", "lng_cargoes", "lng_seller_offers", "lng_buyer_rfqs", "lng_liquefaction_projects", "lng_terminals", "lng_specifications", "lng_companies"):
        op.drop_table(table)
