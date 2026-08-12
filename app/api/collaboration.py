"""Governed collaboration primitives for shared analyst and BI delivery."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import IntegrityError

from app.core.security import SecurityError, authorize
from app.models.all import ReviewComment, ReviewDecision, ReviewThread, WorkspaceAsset, WorkspaceCollection, WorkspaceCollectionItem


router = APIRouter(prefix="/api/v1/workspaces", tags=["collaboration"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


def _thread_payload(thread: ReviewThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "tenant_id": thread.tenant_id,
        "workspace_id": thread.workspace_id,
        "asset_id": thread.asset_id,
        "title": thread.title,
        "status": thread.status,
        "created_by": thread.created_by,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "comments": [{"id": item.id, "author": item.author, "body": item.body, "created_at": item.created_at} for item in thread.comments],
        "decisions": [{"id": item.id, "decision": item.decision, "approver": item.approver, "evidence": item.evidence, "created_at": item.created_at} for item in thread.decisions],
    }


@router.post("/{workspace_id}/reviews", status_code=201)
def create_review(workspace_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "write", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        asset_id = payload.get("asset_id")
        if asset_id:
            asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.id == str(asset_id), WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id).first()
            if asset is None:
                raise SecurityError("ASSET_NOT_FOUND", "The review asset was not found in this tenant workspace.", status_code=404)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise SecurityError("REVIEW_TITLE_REQUIRED", "A review title is required.", status_code=422)
        thread = ReviewThread(tenant_id=actor.tenant_id, workspace_id=workspace_id, asset_id=str(asset_id) if asset_id else None, title=title, status="OPEN", created_by=actor.principal_id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
        return _thread_payload(thread)
    finally:
        db.close()


@router.get("/{workspace_id}/reviews")
def list_reviews(workspace_id: str, request: Request, status: str | None = None):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        query = db.query(ReviewThread).filter(ReviewThread.tenant_id == actor.tenant_id, ReviewThread.workspace_id == workspace_id)
        if status:
            query = query.filter(ReviewThread.status == str(status).upper())
        rows = query.order_by(ReviewThread.updated_at.desc()).limit(500).all()
        return {"tenant_id": actor.tenant_id, "workspace_id": workspace_id, "count": len(rows), "reviews": [_thread_payload(row) for row in rows]}
    finally:
        db.close()


def _get_thread(db, actor, workspace_id: str, review_id: str) -> ReviewThread:
    thread = db.query(ReviewThread).filter(ReviewThread.id == review_id, ReviewThread.tenant_id == actor.tenant_id, ReviewThread.workspace_id == workspace_id).first()
    if thread is None:
        raise SecurityError("REVIEW_NOT_FOUND", "The review thread was not found.", status_code=404)
    return thread


@router.post("/{workspace_id}/reviews/{review_id}/comments", status_code=201)
def add_review_comment(workspace_id: str, review_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "write", workspace_id=workspace_id)
    body = str(payload.get("body", "")).strip()
    if not body:
        raise SecurityError("COMMENT_REQUIRED", "Comment body is required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        thread = _get_thread(db, actor, workspace_id, review_id)
        if thread.status == "RESOLVED":
            raise SecurityError("REVIEW_RESOLVED", "Resolved reviews cannot receive new comments.", status_code=409)
        comment = ReviewComment(tenant_id=actor.tenant_id, thread_id=thread.id, author=actor.principal_id, body=body)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return {"id": comment.id, "review_id": thread.id, "author": comment.author, "body": comment.body, "created_at": comment.created_at}
    finally:
        db.close()


@router.post("/{workspace_id}/reviews/{review_id}/decision")
def decide_review(workspace_id: str, review_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "deploy", workspace_id=workspace_id)
    decision = str(payload.get("decision", "")).upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise SecurityError("INVALID_REVIEW_DECISION", "decision must be APPROVE or REJECT.", status_code=422)
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise SecurityError("DECISION_EVIDENCE_REQUIRED", "An evidence object is required for every review decision.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        thread = _get_thread(db, actor, workspace_id, review_id)
        record = ReviewDecision(tenant_id=actor.tenant_id, thread_id=thread.id, decision=decision, approver=actor.principal_id, evidence=evidence)
        thread.status = "RESOLVED" if decision == "APPROVE" else "OPEN"
        db.add(record)
        db.commit()
        db.refresh(record)
        return {"review_id": thread.id, "status": thread.status, "decision_id": record.id, "decision": decision, "approver": record.approver, "evidence": record.evidence}
    finally:
        db.close()


def _collection_payload(collection: WorkspaceCollection) -> dict[str, Any]:
    return {
        "id": collection.id,
        "tenant_id": collection.tenant_id,
        "workspace_id": collection.workspace_id,
        "name": collection.name,
        "description": collection.description,
        "owner": collection.owner,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
        "asset_ids": [item.asset_id for item in collection.items],
    }


@router.post("/{workspace_id}/collections", status_code=201)
def create_collection(workspace_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "write", workspace_id=workspace_id)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise SecurityError("COLLECTION_NAME_REQUIRED", "A collection name is required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        collection = WorkspaceCollection(tenant_id=actor.tenant_id, workspace_id=workspace_id, name=name, description=payload.get("description"), owner=actor.principal_id)
        db.add(collection)
        db.commit()
        db.refresh(collection)
        return _collection_payload(collection)
    except IntegrityError as exc:
        db.rollback()
        raise SecurityError("COLLECTION_CONFLICT", "A collection with this name already exists.", status_code=409) from exc
    finally:
        db.close()


@router.get("/{workspace_id}/collections")
def list_collections(workspace_id: str, request: Request):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(WorkspaceCollection).filter(WorkspaceCollection.tenant_id == actor.tenant_id, WorkspaceCollection.workspace_id == workspace_id).order_by(WorkspaceCollection.name.asc()).all()
        return {"workspace_id": workspace_id, "collections": [_collection_payload(row) for row in rows]}
    finally:
        db.close()


@router.post("/{workspace_id}/collections/{collection_id}/items", status_code=201)
def add_collection_item(workspace_id: str, collection_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "write", workspace_id=workspace_id)
    asset_id = str(payload.get("asset_id", "")).strip()
    if not asset_id:
        raise SecurityError("ASSET_REQUIRED", "asset_id is required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        collection = db.query(WorkspaceCollection).filter(WorkspaceCollection.id == collection_id, WorkspaceCollection.tenant_id == actor.tenant_id, WorkspaceCollection.workspace_id == workspace_id).first()
        asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.id == asset_id, WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id).first()
        if collection is None or asset is None:
            raise SecurityError("COLLECTION_OR_ASSET_NOT_FOUND", "The collection or asset was not found in this tenant workspace.", status_code=404)
        item = WorkspaceCollectionItem(collection_id=collection.id, asset_id=asset.id, added_by=actor.principal_id)
        db.add(item)
        db.commit()
        return {"collection_id": collection.id, "asset_id": asset.id, "added_by": actor.principal_id}
    except IntegrityError as exc:
        db.rollback()
        raise SecurityError("COLLECTION_ITEM_CONFLICT", "This asset is already in the collection.", status_code=409) from exc
    finally:
        db.close()
