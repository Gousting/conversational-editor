"""项目 IO — JSON 格式持久化"""

import json
import os
from datetime import datetime
from pathlib import Path
from .timeline import Timeline, UndoManager
from .media import MediaStore


class ProjectIO:
    """项目文件格式:
    {
      "version": "1.0",
      "meta": { "name": "...", "created": "...", "modified": "..." },
      "sources": [ VideoInfo.to_dict(), ... ],
      "timeline": { "items": [ TimelineItem.to_dict(), ... ] },
      "undo_stack": []
    }
    """

    CURRENT_VERSION = "1.0"

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.media_store = MediaStore()

    def save(self, timeline: Timeline, undo_manager: UndoManager,
             name: str = "未命名项目") -> str:
        """保存项目到文件"""
        data = {
            "version": self.CURRENT_VERSION,
            "meta": {
                "name": name,
                "created": self._load_meta().get("created",
                    datetime.now().isoformat()),
                "modified": datetime.now().isoformat(),
            },
            "sources": self.media_store.list_sources(),
            "timeline": {
                "items": timeline.to_list(),
            },
        }

        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(self.project_path)

    def load(self) -> tuple[Timeline, UndoManager, list[dict]]:
        """加载项目，返回 (timeline, undo_manager, sources)"""
        if not self.project_path.exists():
            raise FileNotFoundError(f"项目文件不存在: {self.project_path}")

        data = json.loads(self.project_path.read_text(encoding="utf-8"))

        timeline = Timeline.from_list(data["timeline"]["items"])
        undo_manager = UndoManager()

        # 恢复素材注册
        sources = data.get("sources", [])
        for src in sources:
            if os.path.exists(src["path"]):
                self.media_store.probe(src["path"])

        return timeline, undo_manager, sources

    def _load_meta(self) -> dict:
        if self.project_path.exists():
            data = json.loads(self.project_path.read_text(encoding="utf-8"))
            return data.get("meta", {})
        return {}

    def export_timeline_json(self, timeline: Timeline) -> str:
        """导出纯时间轴 JSON（供下游脚本使用）"""
        export_path = self.project_path.with_suffix(".export.json")
        data = {
            "items": timeline.to_list(),
            "total_duration": timeline.total_duration,
            "clip_count": timeline.clip_count,
        }
        export_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(export_path)
