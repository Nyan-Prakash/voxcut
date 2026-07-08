"""Project CRUD (spec §11.3)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Project, utcnow

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    context_brief: dict = {}
    settings: dict = {"aspect": "16:9", "cut_density": "normal"}


class ProjectOut(BaseModel):
    id: str
    name: str
    status: str
    duration_s: float
    edl_version: int
    context_brief: dict
    settings: dict
    voiceover_path: str | None

    @classmethod
    def of(cls, p: Project) -> "ProjectOut":
        return cls(
            id=p.id, name=p.name, status=p.status, duration_s=p.duration_s,
            edl_version=p.edl_version,
            context_brief=json.loads(p.context_brief or "{}"),
            settings=json.loads(p.settings or "{}"),
            voiceover_path=p.voiceover_path,
        )


@router.post("", response_model=ProjectOut)
def create_project(body: ProjectCreate, db: Session = Depends(get_session)) -> ProjectOut:
    p = Project(
        name=body.name,
        context_brief=json.dumps(body.context_brief),
        settings=json.dumps(body.settings),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return ProjectOut.of(p)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_session)) -> list[ProjectOut]:
    rows = db.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [ProjectOut.of(p) for p in rows]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_session)) -> ProjectOut:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return ProjectOut.of(p)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, body: dict, db: Session = Depends(get_session)) -> ProjectOut:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    if "name" in body:
        p.name = body["name"]
    if "context_brief" in body:
        p.context_brief = json.dumps(body["context_brief"])
    if "settings" in body:
        p.settings = json.dumps(body["settings"])
    p.updated_at = utcnow()
    db.add(p)
    db.commit()
    db.refresh(p)
    return ProjectOut.of(p)
