from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

import models


ARCHIVE_SCHEMA = "luminascript.project"
ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024


class ArchivedScene(BaseModel):
    scene_index: int = Field(ge=1, le=1000)
    outline: str = Field(min_length=1, max_length=50_000)
    content: str | None = Field(default=None, max_length=2_000_000)
    summary: str | None = Field(default=None, max_length=200_000)
    status: models.ProcessingStatus = models.ProcessingStatus.PENDING


class ArchivedProject(BaseModel):
    title: str = Field(default="导入项目", min_length=1, max_length=200)
    logline: str = Field(min_length=1, max_length=20_000)
    project_type: Literal["movie", "tv", "short", "short_video", "pending"] = "movie"
    genre: str | None = Field(default=None, max_length=100_000)
    global_context: dict[str, Any] = Field(default_factory=dict)
    global_summary: str | None = Field(default=None, max_length=500_000)
    next_step_cache: dict[str, Any] | None = None
    status: models.ProcessingStatus = models.ProcessingStatus.PENDING
    scenes: list[ArchivedScene] = Field(default_factory=list, max_length=1000)

    @field_validator("title", "logline")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("项目标题和故事梗概不能为空")
        return normalized

    @model_validator(mode="after")
    def ensure_unique_scene_indexes(self):
        indexes = [scene.scene_index for scene in self.scenes]
        if len(indexes) != len(set(indexes)):
            raise ValueError("导入文件包含重复的场次序号")
        return self


class ProjectArchive(BaseModel):
    schema_name: Literal[ARCHIVE_SCHEMA] = Field(alias="schema")
    version: Literal[ARCHIVE_VERSION]
    exported_at: str
    project: ArchivedProject


def serialize_project_archive(project: models.Project, *, exported_at: str) -> dict[str, Any]:
    return {
        "schema": ARCHIVE_SCHEMA,
        "version": ARCHIVE_VERSION,
        "exported_at": exported_at,
        "project": {
            "title": str(project.title or "未命名项目"),
            "logline": str(project.logline or "未提供故事梗概"),
            "project_type": str(project.project_type or "movie"),
            "genre": project.genre,
            "global_context": {
                str(key): value
                for key, value in (
                    project.global_context.items()
                    if isinstance(project.global_context, dict)
                    else []
                )
                if not str(key).startswith("_")
            },
            "global_summary": project.global_summary,
            "next_step_cache": project.next_step_cache if isinstance(project.next_step_cache, dict) else None,
            "status": str(getattr(project.status, "value", project.status or "pending")),
            "scenes": [
                {
                    "scene_index": int(scene.scene_index),
                    "outline": str(scene.outline or f"第 {scene.scene_index} 场"),
                    "content": scene.content,
                    "summary": scene.summary,
                    "status": str(getattr(scene.status, "value", scene.status or "pending")),
                }
                for scene in sorted(project.scenes or [], key=lambda item: item.scene_index)
            ],
        },
    }


def encode_project_archive(project: models.Project, *, exported_at: str) -> bytes:
    payload = serialize_project_archive(project, exported_at=exported_at)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def parse_project_archive(raw: bytes) -> ProjectArchive:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("项目备份文件不能超过 20 MB")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("文件不是有效的 LuminaScript JSON 项目备份") from exc
    try:
        return ProjectArchive.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"项目备份结构无效：{str(exc)[:1_000]}") from exc


def create_imported_project(archive: ProjectArchive, *, owner_id: int) -> models.Project:
    source = archive.project
    suffix = "（导入副本）"
    title = f"{source.title[: max(1, 200 - len(suffix))]}{suffix}"
    project_status = source.status
    if project_status == models.ProcessingStatus.GENERATING:
        project_status = models.ProcessingStatus.FAILED
    if project_status == models.ProcessingStatus.COMPLETED and not source.scenes:
        project_status = models.ProcessingStatus.FAILED

    project = models.Project(
        title=title,
        logline=source.logline,
        project_type=source.project_type,
        genre=source.genre,
        global_context=dict(source.global_context),
        global_summary=source.global_summary,
        next_step_cache=dict(source.next_step_cache) if source.next_step_cache else None,
        owner_id=owner_id,
        total_tokens=0,
        status=project_status,
    )
    for item in sorted(source.scenes, key=lambda scene: scene.scene_index):
        scene_status = item.status
        if scene_status == models.ProcessingStatus.GENERATING:
            scene_status = models.ProcessingStatus.PENDING
        project.scenes.append(
            models.Scene(
                scene_index=item.scene_index,
                outline=item.outline,
                content=item.content,
                summary=item.summary,
                status=scene_status,
            )
        )
    return project
