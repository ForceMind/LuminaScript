from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional, Any, Dict, Literal
from models import ProcessingStatus

# --- Core Data Schemas ---

ProjectType = Literal["movie", "tv", "short", "short_video", "pending"]


class SceneBase(BaseModel):
    scene_index: int = Field(ge=1, le=1000)
    outline: str = Field(min_length=1, max_length=50000)

class SceneCreate(SceneBase):
    pass

class SceneResponse(SceneBase):
    id: int
    content: Optional[str] = None
    summary: Optional[str] = None
    status: ProcessingStatus

    model_config = ConfigDict(from_attributes=True)

class ProjectBase(BaseModel):
    logline: str = Field(min_length=1, max_length=20000)
    title: Optional[str] = Field(default="Untitled Script", max_length=200)
    project_type: ProjectType = "movie"

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    project_type: Optional[ProjectType] = None

class ProjectListResponse(ProjectBase):
    id: int
    genre: Optional[str] = None
    project_type: ProjectType = "movie"
    global_context: Dict[str, Any] = Field(default_factory=dict)
    owner_id: int
    total_tokens: int = 0
    status: ProcessingStatus = ProcessingStatus.PENDING

    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(ProjectListResponse):
    scenes: List[SceneResponse] = Field(default_factory=list)

# --- Auth Schemas ---
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("用户名至少需要 3 个字符")
        if any(character.isspace() or ord(character) < 32 for character in normalized):
            raise ValueError("用户名不能包含空白或控制字符")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码长度不能超过 72 字节")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=10, max_length=72)

    @field_validator("current_password", "new_password")
    @classmethod
    def validate_bcrypt_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码长度不能超过 72 字节")
        return value

class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: int = 0
    
    model_config = ConfigDict(from_attributes=True)


class AdminRoleUpdate(BaseModel):
    is_admin: bool


class LoginLogResponse(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None # Computed field
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class AIInteractionLogResponse(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    project_id: Optional[int] = None
    action: Optional[str] = ""
    prompt: Optional[str] = ""
    response: Optional[str] = ""
    tokens: Optional[int] = 0
    status: Optional[str] = "success"
    step_key: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    attempt: int = 1
    timestamp: Optional[str] = ""

    model_config = ConfigDict(from_attributes=True)

class PaginatedLoginLogs(BaseModel):
    total: int
    items: List[LoginLogResponse]

class PaginatedAILogs(BaseModel):
    total: int
    items: List[AIInteractionLogResponse]

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

