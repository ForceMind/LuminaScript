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

class ProjectCreate(ProjectBase):
    pass

class ProjectResponse(ProjectBase):
    id: int
    genre: Optional[str] = None
    global_context: Dict[str, Any] = {}
    scenes: List[SceneResponse] = []

    class Config:
        from_attributes = True

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

