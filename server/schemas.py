"""服务层 — 请求/响应模型"""

from pydantic import BaseModel
from typing import Optional


class LoadVideoRequest(BaseModel):
    filepath: str

class LoadVideoResponse(BaseModel):
    success: bool
    session_id: str
    source_id: str
    filename: str
    duration: float
    fps: float
    width: int
    height: int
    analysis: Optional[dict] = None  # 视频分析结果
    error: str = ""

class TimelineState(BaseModel):
    items: list[dict]
    total_duration: float
    clip_count: int

class WSMessage(BaseModel):
    type: str
    payload: dict = {}
