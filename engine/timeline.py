"""时间轴引擎 — 核心数据模型与操作"""

import uuid
from dataclasses import dataclass, field
from typing import Optional
from copy import deepcopy


@dataclass
class Clip:
    id: str
    source_id: str           # 对应 ProjectJSON.sources[].id
    source_start: float      # 源视频起始时间（秒）
    source_end: float        # 源视频结束时间（秒）
    speed: float = 1.0
    volume: float = 1.0
    label: str = ""

    @property
    def source_duration(self) -> float:
        return self.source_end - self.source_start

    @property
    def output_duration(self) -> float:
        """考虑变速后的实际时长"""
        if self.speed <= 0:
            return 0
        return self.source_duration / self.speed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "speed": self.speed,
            "volume": self.volume,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        return cls(
            id=d["id"],
            source_id=d["source_id"],
            source_start=d["source_start"],
            source_end=d["source_end"],
            speed=d.get("speed", 1.0),
            volume=d.get("volume", 1.0),
            label=d.get("label", ""),
        )


@dataclass
class Transition:
    id: str
    effect: str              # "cut" | "flash" | "dissolve"
    duration: float = 0.3
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "effect": self.effect,
            "duration": self.duration,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        return cls(
            id=d["id"],
            effect=d["effect"],
            duration=d.get("duration", 0.3),
            params=d.get("params", {}),
        )


@dataclass
class TimelineItem:
    item_type: str  # "clip" | "transition"
    data: Clip | Transition

    def to_dict(self) -> dict:
        if self.item_type == "clip":
            return {"type": "clip", "data": self.data.to_dict()}
        else:
            return {"type": "transition", "data": self.data.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineItem":
        if d["type"] == "clip":
            return cls(item_type="clip", data=Clip.from_dict(d["data"]))
        else:
            return cls(item_type="transition", data=Transition.from_dict(d["data"]))


class Timeline:
    """不可变操作风格：每次操作返回新状态描述，由 UndoManager 管理"""

    def __init__(self):
        self.items: list[TimelineItem] = []
        self.version: int = 0

    # ─── 查询 ───

    @property
    def total_duration(self) -> float:
        d = 0.0
        for item in self.items:
            if item.item_type == "clip":
                d += item.data.output_duration
            else:
                d += item.data.duration
        return d

    @property
    def clip_count(self) -> int:
        return sum(1 for i in self.items if i.item_type == "clip")

    def get_clip(self, clip_id: str) -> Optional[Clip]:
        for item in self.items:
            if item.item_type == "clip" and item.data.id == clip_id:
                return item.data
        return None

    def get_item_index(self, item_id: str) -> int:
        for i, item in enumerate(self.items):
            if item.data.id == item_id:
                return i
        return -1

    # ─── 操作 ───

    def add_clip(self, source_id: str, start: float, end: float,
                 speed: float = 1.0, label: str = "",
                 after_item_id: Optional[str] = None) -> Clip:
        clip = Clip(
            id=str(uuid.uuid4())[:8],
            source_id=source_id,
            source_start=start,
            source_end=end,
            speed=speed,
            label=label,
        )
        item = TimelineItem(item_type="clip", data=clip)

        if after_item_id:
            idx = self.get_item_index(after_item_id)
            if idx >= 0:
                self.items.insert(idx + 1, item)
            else:
                self.items.append(item)
        else:
            self.items.append(item)

        self.version += 1
        return clip

    def remove_item(self, item_id: str) -> bool:
        idx = self.get_item_index(item_id)
        if idx >= 0:
            self.items.pop(idx)
            self.version += 1
            return True
        return False

    def update_clip(self, clip_id: str, **kwargs) -> bool:
        clip = self.get_clip(clip_id)
        if not clip:
            return False
        for k, v in kwargs.items():
            if hasattr(clip, k):
                setattr(clip, k, v)
        self.version += 1
        return True

    def reorder(self, item_id: str, new_index: int) -> bool:
        idx = self.get_item_index(item_id)
        if idx < 0 or new_index < 0 or new_index >= len(self.items):
            return False
        item = self.items.pop(idx)
        self.items.insert(new_index, item)
        self.version += 1
        return True

    def add_transition(self, after_item_id: str, effect: str,
                       duration: float = 0.3, **params) -> Optional[Transition]:
        idx = self.get_item_index(after_item_id)
        if idx < 0:
            return None
        trans = Transition(
            id=str(uuid.uuid4())[:8],
            effect=effect,
            duration=duration,
            params=params,
        )
        self.items.insert(idx + 1, TimelineItem(item_type="transition", data=trans))
        self.version += 1
        return trans

    # ─── 序列化 ───

    def to_list(self) -> list[dict]:
        return [item.to_dict() for item in self.items]

    @classmethod
    def from_list(cls, items_data: list[dict]) -> "Timeline":
        tl = cls()
        for d in items_data:
            tl.items.append(TimelineItem.from_dict(d))
        return tl

    def clone(self) -> "Timeline":
        """深拷贝用于 undo"""
        return deepcopy(self)


class UndoManager:
    """时间轴操作栈"""

    def __init__(self, max_stack: int = 50):
        self.undo_stack: list[Timeline] = []
        self.redo_stack: list[Timeline] = []
        self.max_stack = max_stack

    def snapshot(self, timeline: Timeline):
        self.undo_stack.append(timeline.clone())
        if len(self.undo_stack) > self.max_stack:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, current: Timeline) -> Optional[Timeline]:
        if not self.undo_stack:
            return None
        self.redo_stack.append(current.clone())
        return self.undo_stack.pop()

    def redo(self, current: Timeline) -> Optional[Timeline]:
        if not self.redo_stack:
            return None
        self.undo_stack.append(current.clone())
        return self.redo_stack.pop()

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0
