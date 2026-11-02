"""HTTP API 的请求与响应契约。"""

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    thread_id: str | None = None


class PendingAction(BaseModel):
    confirmation_id: str
    tool: str
    order_id: str
    summary: str
    status: str
    expires_at: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    thread_id: str
    pending_action: PendingAction | None = None
    history_saved: bool
    warning: str | None = None


class HealthResponse(BaseModel):
    status: str
    demo_user_id: str
    version: str