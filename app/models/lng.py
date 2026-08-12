from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LNGCompany(Base):
    __tablename__ = "lng_companies"

    id = Column(String, primary_key=True, default=_uuid)
    legal_name = Column(String, nullable=False, index=True)
    normalized_name = Column(String, nullable=False, index=True)
    company_type = Column(String, nullable=False, index=True)
    country = Column(String, nullable=True, index=True)
    website = Column(String, nullable=True)
    lei = Column(String, nullable=True, index=True)
    registration_number = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="UNVERIFIED", index=True)
    kyc_status = Column(String, nullable=False, default="NOT_STARTED", index=True)
    sanctions_screening_status = Column(String, nullable=False, default="INSUFFICIENT DATA", index=True)
    source_url = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    retrieved_at = Column(DateTime, nullable=True)
    last_verified = Column(DateTime, nullable=True)
    confidence = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class LNGSpecification(Base):
    __tablename__ = "lng_specifications"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=True)
    origin = Column(String, nullable=True)
    quality_basis = Column(String, nullable=True)
    measurement_standard = Column(String, nullable=True)
    hhv_mmbtu_per_mt = Column(Float, nullable=True)
    lhv_mmbtu_per_mt = Column(Float, nullable=True)
    density_mt_per_m3 = Column(Float, nullable=True)
    wobbe_index = Column(Float, nullable=True)
    methane_pct = Column(Float, nullable=True)
    ethane_pct = Column(Float, nullable=True)
    propane_pct = Column(Float, nullable=True)
    butane_pct = Column(Float, nullable=True)
    nitrogen_pct = Column(Float, nullable=True)
    carbon_dioxide_pct = Column(Float, nullable=True)
    sulphur_specification = Column(String, nullable=True)
    water_specification = Column(String, nullable=True)
    certificate_reference = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="UNVERIFIED")
    last_verified = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)


class LNGTerminal(Base):
    __tablename__ = "lng_terminals"

    id = Column(String, primary_key=True, default=_uuid)
    terminal_name = Column(String, nullable=False, index=True)
    country = Column(String, nullable=False, index=True)
    port = Column(String, nullable=True)
    terminal_type = Column(String, nullable=False, index=True)
    operator_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=True)
    status = Column(String, nullable=False, default="UNKNOWN", index=True)
    capacity_mtpa = Column(Float, nullable=True)
    capacity_bcm_per_year = Column(Float, nullable=True)
    storage_capacity_m3 = Column(Float, nullable=True)
    berths = Column(Integer, nullable=True)
    sendout_capacity = Column(Float, nullable=True)
    third_party_access = Column(Boolean, nullable=True)
    ship_size_constraints = Column(JSON, nullable=True)
    quality_requirements = Column(JSON, nullable=True)
    tariff_data = Column(JSON, nullable=True)
    slot_booking_process = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="UNVERIFIED")
    last_verified = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)

    operator = relationship("LNGCompany")


class LNGLiquefactionProject(Base):
    __tablename__ = "lng_liquefaction_projects"

    id = Column(String, primary_key=True, default=_uuid)
    project_name = Column(String, nullable=False, index=True)
    country = Column(String, nullable=False, index=True)
    operator_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=True)
    loading_terminal_id = Column(String, ForeignKey("lng_terminals.id"), nullable=True)
    project_status = Column(String, nullable=False, index=True)
    liquefaction_capacity_mtpa = Column(Float, nullable=True)
    number_of_trains = Column(Integer, nullable=True)
    train_capacity_mtpa = Column(Float, nullable=True)
    commissioning_date = Column(Date, nullable=True)
    expected_start_date = Column(Date, nullable=True)
    feedgas_source = Column(String, nullable=True)
    marketing_entities = Column(JSON, nullable=True)
    contracted_capacity_mtpa = Column(Float, nullable=True)
    uncontracted_capacity_mtpa = Column(Float, nullable=True)
    marketing_basis = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="UNVERIFIED")
    last_verified = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)

    operator = relationship("LNGCompany")
    loading_terminal = relationship("LNGTerminal")


class LNGBuyerRFQ(Base):
    __tablename__ = "lng_buyer_rfqs"

    id = Column(String, primary_key=True, default=_uuid)
    buyer_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=False, index=True)
    transaction_type = Column(String, nullable=False, index=True)
    delivery_basis = Column(String, nullable=False)
    destination_terminal_id = Column(String, ForeignKey("lng_terminals.id"), nullable=True)
    country = Column(String, nullable=True, index=True)
    delivery_window_start = Column(Date, nullable=True, index=True)
    delivery_window_end = Column(Date, nullable=True, index=True)
    number_of_cargoes = Column(Integer, nullable=True)
    cargo_volume_mt = Column(Float, nullable=True)
    energy_mmbtu = Column(Float, nullable=True)
    lng_specification_id = Column(String, ForeignKey("lng_specifications.id"), nullable=True)
    origin_preferences = Column(JSON, nullable=True)
    origin_restrictions = Column(JSON, nullable=True)
    pricing_basis = Column(String, nullable=True)
    benchmark = Column(String, nullable=True)
    target_price = Column(Float, nullable=True)
    currency = Column(String, nullable=False, default="USD")
    payment_terms = Column(Text, nullable=True)
    credit_requirements = Column(Text, nullable=True)
    vessel_requirements = Column(JSON, nullable=True)
    terminal_requirements = Column(JSON, nullable=True)
    offer_deadline = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="NEW", index=True)
    source_url = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="UNVERIFIED")
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)

    buyer = relationship("LNGCompany")
    destination_terminal = relationship("LNGTerminal")
    specification = relationship("LNGSpecification")


class LNGSellerOffer(Base):
    __tablename__ = "lng_seller_offers"

    id = Column(String, primary_key=True, default=_uuid)
    seller_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("lng_liquefaction_projects.id"), nullable=True)
    loading_terminal_id = Column(String, ForeignKey("lng_terminals.id"), nullable=True)
    origin = Column(String, nullable=True, index=True)
    delivery_basis = Column(String, nullable=False)
    available_window_start = Column(Date, nullable=True, index=True)
    available_window_end = Column(Date, nullable=True, index=True)
    cargo_volume_mt = Column(Float, nullable=True)
    energy_mmbtu = Column(Float, nullable=True)
    lng_specification_id = Column(String, ForeignKey("lng_specifications.id"), nullable=True)
    pricing_formula = Column(JSON, nullable=True)
    benchmark = Column(String, nullable=True)
    premium_discount = Column(Float, nullable=True)
    fixed_price = Column(Float, nullable=True)
    currency = Column(String, nullable=False, default="USD")
    payment_terms = Column(Text, nullable=True)
    credit_requirements = Column(Text, nullable=True)
    destination_restrictions = Column(JSON, nullable=True)
    vessel_requirements = Column(JSON, nullable=True)
    offer_date = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="INDICATIVE", index=True)
    source_url = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="INDICATIVE")
    created_at = Column(DateTime, default=_utc_now)

    seller = relationship("LNGCompany")
    project = relationship("LNGLiquefactionProject")
    loading_terminal = relationship("LNGTerminal")
    specification = relationship("LNGSpecification")


class LNGCargo(Base):
    __tablename__ = "lng_cargoes"

    id = Column(String, primary_key=True, default=_uuid)
    seller_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=False, index=True)
    seller_offer_id = Column(String, ForeignKey("lng_seller_offers.id"), nullable=True)
    project_id = Column(String, ForeignKey("lng_liquefaction_projects.id"), nullable=True)
    loading_terminal_id = Column(String, ForeignKey("lng_terminals.id"), nullable=True)
    origin = Column(String, nullable=True, index=True)
    loading_window_start = Column(Date, nullable=True, index=True)
    loading_window_end = Column(Date, nullable=True, index=True)
    expected_delivery_window_start = Column(Date, nullable=True)
    expected_delivery_window_end = Column(Date, nullable=True)
    volume_mt = Column(Float, nullable=True)
    energy_mmbtu = Column(Float, nullable=True)
    lng_specification_id = Column(String, ForeignKey("lng_specifications.id"), nullable=True)
    delivery_basis = Column(String, nullable=False)
    status = Column(String, nullable=False, default="INDICATIVE", index=True)
    vessel_status = Column(String, nullable=True)
    destination_terminal_id = Column(String, ForeignKey("lng_terminals.id"), nullable=True)
    buyer_company_id = Column(String, ForeignKey("lng_companies.id"), nullable=True)
    compliance_status = Column(String, nullable=False, default="INSUFFICIENT DATA", index=True)
    source_url = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="INDICATIVE")
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)

    seller = relationship("LNGCompany", foreign_keys=[seller_company_id])
    buyer = relationship("LNGCompany", foreign_keys=[buyer_company_id])
    offer = relationship("LNGSellerOffer")
    project = relationship("LNGLiquefactionProject")
    loading_terminal = relationship("LNGTerminal", foreign_keys=[loading_terminal_id])
    destination_terminal = relationship("LNGTerminal", foreign_keys=[destination_terminal_id])
    specification = relationship("LNGSpecification")


class LNGMatch(Base):
    __tablename__ = "lng_matches"

    id = Column(String, primary_key=True, default=_uuid)
    rfq_id = Column(String, ForeignKey("lng_buyer_rfqs.id"), nullable=False, index=True)
    cargo_id = Column(String, ForeignKey("lng_cargoes.id"), nullable=False, index=True)
    score = Column(Float, nullable=False)
    overall_status = Column(String, nullable=False, index=True)
    actionable = Column(Boolean, nullable=False, default=False)
    checks = Column(JSON, nullable=False)
    critical_unknowns = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utc_now)

    rfq = relationship("LNGBuyerRFQ")
    cargo = relationship("LNGCargo")
