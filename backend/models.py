from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, JSON, Enum, Index, UniqueConstraint, func
from sqlalchemy.orm import relationship
from database import Base
import enum

class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Integer, default=0) # 0=User, 1=Admin
    daily_token_limit = Column(Integer, default=0)
    monthly_token_limit = Column(Integer, default=0)
    
    projects = relationship("Project", back_populates="owner")
    login_logs = relationship("LoginLog", back_populates="user")
    ai_logs = relationship("AIInteractionLog", back_populates="user", foreign_keys="AIInteractionLog.user_id")
    project_memberships = relationship(
        "ProjectMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )

class LoginLog(Base):
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    ip_address = Column(String)
    user_agent = Column(String, nullable=True) # Browser/Device info
    location = Column(String, nullable=True) # Geo info (Optional)
    status = Column(String) # success, failed
    timestamp = Column(String) # ISO format

    user = relationship("User", back_populates="login_logs")

class AIInteractionLog(Base):
    __tablename__ = "ai_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    # NULL preserves the historical actor-based attribution; never backfill.
    billed_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    action = Column(String) # analyze, generate_scene, etc.
    prompt = Column(Text)
    response = Column(Text)
    tokens = Column(Integer, default=0)
    status = Column(String, default="success")
    step_key = Column(String, nullable=True)
    error_type = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    attempt = Column(Integer, default=1)
    timestamp = Column(String) # ISO format

    __table_args__ = (
        # Keeps legacy actor-billed records queryable without backfilling them.
        Index(
            "ix_ai_logs_billing_identity_timestamp",
            func.coalesce(billed_user_id, user_id),
            timestamp,
        ),
    )

    user = relationship("User", back_populates="ai_logs", foreign_keys=[user_id])
    billed_user = relationship("User", foreign_keys=[billed_user_id])

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, default="New Project")
    logline = Column(String)
    project_type = Column(String, default="movie") # movie, tv, short, etc.
    genre = Column(String, nullable=True)
    
    # Tracking
    total_tokens = Column(Integer, default=0)
    status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING)

    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="projects")
    
    # Stores global context like Character Bios, World View, etc.
    global_context = Column(JSON, default=dict)
    
    # Stores the next interaction step to cache specific questions
    next_step_cache = Column(JSON, nullable=True)
    # Unconfirmed working draft, deliberately independent of interaction cache.
    quick_setup_draft = Column(JSON(none_as_null=True), nullable=True)
    # Monotonic setup state and derived-question revisions; never restored from snapshots.
    setup_revision = Column(Integer, default=0, server_default="0", nullable=False)
    setup_cache_revision = Column(Integer, default=0, server_default="0", nullable=False)

    @property
    def context_revision(self) -> str:
        return f"setup-v2:{int(self.setup_revision or 0)}:{int(self.setup_cache_revision or 0)}"

    @property
    def has_quick_setup_draft(self) -> bool:
        return self.quick_setup_draft is not None

    @property
    def quick_setup_draft_stale(self) -> bool:
        from services.setup_drafts import inspect_draft
        return inspect_draft(self)[1]
    
    # Stores the overall summary/hook
    global_summary = Column(Text, nullable=True)

    scenes = relationship("Scene", back_populates="project", cascade="all, delete-orphan")
    generation_jobs = relationship(
        "GenerationJob",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    members = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    versions = relationship(
        "ProjectVersion",
        back_populates="project",
        cascade="all, delete-orphan",
    )

class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("project_id", "scene_index", name="uq_scenes_project_scene_index"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    scene_index = Column(Integer, index=True)
    
    # The one-line outline for this scene (Input)
    outline = Column(Text)
    
    # The generated script content (Output)
    content = Column(Text, nullable=True)
    
    # The summary of THIS scene (to be passed to next scene)
    summary = Column(Text, nullable=True)
    
    status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING)

    project = relationship("Project", back_populates="scenes")


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index("ix_generation_jobs_status_available_at_id", "status", "available_at", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    kind = Column(String, index=True)
    payload = Column(JSON, default=dict)
    status = Column(Enum(JobStatus), default=JobStatus.QUEUED, index=True)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    available_at = Column(String, index=True)
    locked_at = Column(String, nullable=True)
    lock_token = Column(String, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(String)
    updated_at = Column(String)
    cancel_requested = Column(Boolean, default=False)

    project = relationship("Project", back_populates="generation_jobs")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    role = Column(String, default="viewer", nullable=False)
    created_at = Column(String, nullable=False)

    project = relationship("Project", back_populates="members")
    user = relationship("User", back_populates="project_memberships")


class ProjectVersion(Base):
    __tablename__ = "project_versions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    label = Column(String, default="手动快照", nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(String, nullable=False)

    project = relationship("Project", back_populates="versions")


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    stage = Column(String, index=True, nullable=False)
    project_type = Column(String, index=True, default="all", nullable=False)
    content = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, unique=True, nullable=False)
    size_bytes = Column(Integer, default=0, nullable=False)
    status = Column(String, default="completed", nullable=False)
    backup_type = Column(String, default="manual", nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
