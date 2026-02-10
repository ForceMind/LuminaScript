from sqlalchemy import Column, Integer, String, Text, ForeignKey, JSON, Enum
from sqlalchemy.orm import relationship
from database import Base
import enum

class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    
    projects = relationship("Project", back_populates="owner")

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
    global_context = Column(JSON, default={}) 
    
    # Stores the next interaction step to cache specific questions
    next_step_cache = Column(JSON, nullable=True)
    
    # Stores the overall summary/hook
    global_summary = Column(Text, nullable=True)

    scenes = relationship("Scene", back_populates="project", cascade="all, delete-orphan")

class Scene(Base):
    __tablename__ = "scenes"

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
