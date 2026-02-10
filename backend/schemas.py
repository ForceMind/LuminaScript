from pydantic import BaseModel
from typing import List, Optional, Any, Dict, Union
from models import ProcessingStatus

# --- Core Data Schemas ---

class SceneBase(BaseModel):
    scene_index: int
    outline: str

class SceneCreate(SceneBase):
    pass

class SceneResponse(SceneBase):
    id: int
    content: Optional[str] = None
    summary: Optional[str] = None
    status: ProcessingStatus

    class Config:
        from_attributes = True

class ProjectBase(BaseModel):
    logline: str
    title: Optional[str] = "Untitled Script"
    project_type: Optional[str] = "movie"

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    project_type: Optional[str] = None

class ProjectResponse(ProjectBase):
    id: int
    genre: Optional[str] = None
    project_type: Optional[str] = "movie"
    global_context: Dict[str, Any] = {}
    scenes: List[SceneResponse] = []
    owner_id: int
    total_tokens: int = 0
    status: ProcessingStatus = ProcessingStatus.PENDING

    class Config:
        from_attributes = True

# --- Auth Schemas ---
class UserCreate(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

# --- Interaction Protocol Schemas ---

class OptionItem(BaseModel):
    label: str
    value: str

class InteractionPayload(BaseModel):
    question: str
    options: List[OptionItem]

class InteractionResponse(BaseModel):
    type: str = "interaction_required"
    payload: InteractionPayload

