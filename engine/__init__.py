"""对话式视频剪辑引擎 — 纯 Python，零服务依赖"""

from .timeline import Timeline, Clip, Transition, UndoManager
from .renderer import Renderer
from .media import MediaStore
from .project import ProjectIO
from .artifact import (Artifact, Finding, Severity, StageResult,
                       ValidationReport, AudioManifest, SubtitleManifest,
                       CompositionReport, QCReport)
from .reviewer import Reviewer
from .pipeline import PipelineEngine, STAGE_HANDLERS

__all__ = [
    "Timeline", "Clip", "Transition", "Renderer", "MediaStore", "ProjectIO",
    "PipelineEngine", "Reviewer", "STAGE_HANDLERS",
    "Artifact", "Finding", "Severity", "StageResult",
    "ValidationReport", "AudioManifest", "SubtitleManifest",
    "CompositionReport", "QCReport",
]
