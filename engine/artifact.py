"""管道 Artifact 数据结构 — 声明式多阶段生产的核心契约

每个阶段产出一种 Artifact，下游阶段按需消费。
Artifact 本身是 JSON-serializable 的 Pydantic 模型，
既能在 Python 侧做类型检查，也能存入 checkpoint 文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


# ─── Finding — 质检发现 ────────────────────────────────────────────

class Severity(str, Enum):
    critical = "critical"       # 必须修复才能继续
    suggestion = "suggestion"   # 显著提升质量，但不阻塞
    nitpick = "nitpick"         # 锦上添花
    investigation = "investigation"  # 真问题但无法定位修复方案

@dataclass
class Finding:
    """一条质检发现。

    CHAI 规则（Accurate / Complete / Constructive）：
    - 每条 finding 必须指向具体字段/帧号（Accurate）
    - 发现一个问题后扫描同类全貌（Complete）
    - critical 必须附带 proposed_fix（Constructive）
    """
    severity: Severity
    criterion: str              # 对应 review_focus 哪条
    location: str               # 具体定位："片段#3 入点 5.2s"
    detail: str                 # 问题描述
    proposed_fix: str = ""      # critical 必须有

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        d["severity"] = Severity(d["severity"])
        return cls(**d)


# ─── Artifact 基类 ──────────────────────────────────────────────────

@dataclass
class Artifact:
    """一个阶段产出的结构化数据。

    子类覆盖 schema_version 和 validate()。
    """
    schema_version: str = "1.0"
    stage_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Artifact":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> list[Finding]:
        """返回 schema 校验发现。子类覆盖。"""
        return []


# ─── 具体 Artifact 类型 ─────────────────────────────────────────────

@dataclass
class ValidationReport(Artifact):
    """validate 阶段产出"""
    clip_count: int = 0
    total_duration: float = 0.0
    transition_count: int = 0
    issues: list[dict] = field(default_factory=list)
    passed: bool = False

    def validate(self) -> list[Finding]:
        findings = []
        if self.clip_count < 1:
            findings.append(Finding(Severity.critical, "clip_count",
                "timeline", "时间轴为空", "至少添加 1 个片段"))
        if self.total_duration <= 0:
            findings.append(Finding(Severity.critical, "duration",
                "timeline", "总时长为 0", "检查片段时长设置"))
        return findings


@dataclass
class AudioManifest(Artifact):
    """audio_prep 阶段产出"""
    tracks: list[dict] = field(default_factory=list)  # [{type, path, duration, peak_db}]
    bgm_path: str = ""
    bgm_volume_db: float = -12.0
    silence_segments: list[dict] = field(default_factory=list)  # [{start, end, duration}]
    peak_level: float = 0.0  # dB
    dialogue_to_bgm_ratio: float = 4.0

    def validate(self) -> list[Finding]:
        findings = []
        if self.dialogue_to_bgm_ratio < 2.0 or self.dialogue_to_bgm_ratio > 5.0:
            findings.append(Finding(Severity.suggestion, "dialogue_to_bgm_ratio",
                "audio_prep", f"对白/BGM 比例 {self.dialogue_to_bgm_ratio:.1f}:1 不在 2:1~5:1 范围",
                "调整 BGM 音量使比例在 3:1~4:1"))
        if self.silence_segments:
            long_silences = [s for s in self.silence_segments if s.get("duration", 0) > 2.0]
            if long_silences:
                findings.append(Finding(Severity.suggestion, "silence",
                    "audio_prep", f"{len(long_silences)} 段静音超过 2 秒",
                    "切掉或用 BGM 填充静音段"))
        return findings


@dataclass
class SubtitleManifest(Artifact):
    """subtitle 阶段产出"""
    subtitles: list[dict] = field(default_factory=list)  # [{start, end, text, style}]
    style: dict = field(default_factory=dict)  # {font, size, color, position}
    srt_path: str = ""
    burned: bool = False

    def validate(self) -> list[Finding]:
        findings = []
        for i, sub in enumerate(self.subtitles):
            text = sub.get("text", "")
            if len(text) > 20:
                findings.append(Finding(Severity.suggestion, "subtitle_length",
                    f"字幕 #{i+1}", f"单行 {len(text)} 字超过 20 字上限",
                    "拆分为两行或两句"))
            dur = sub.get("end", 0) - sub.get("start", 0)
            if dur < 0.5:
                findings.append(Finding(Severity.suggestion, "subtitle_duration",
                    f"字幕 #{i+1}", f"显示时长 {dur:.1f}s 太短",
                    "延长到至少 1.0 秒"))
        return findings


@dataclass
class CompositionReport(Artifact):
    """compose 阶段产出"""
    output_path: str = ""
    output_duration: float = 0.0
    expected_duration: float = 0.0
    file_size_bytes: int = 0
    encoding_params: dict = field(default_factory=dict)
    xfade_transitions: int = 0

    def validate(self) -> list[Finding]:
        findings = []
        if self.output_path and not Path(self.output_path).exists():
            findings.append(Finding(Severity.critical, "output_exists",
                "compose", "渲染产物文件不存在",
                "检查 ffmpeg 命令和磁盘空间"))
        if self.expected_duration > 0:
            diff_pct = abs(self.output_duration - self.expected_duration) / self.expected_duration
            if diff_pct > 0.01:
                findings.append(Finding(Severity.suggestion, "duration_match",
                    "compose", f"输出时长 {self.output_duration:.1f}s 与预期 {self.expected_duration:.1f}s 偏差 {diff_pct*100:.1f}%",
                    "检查时间轴计算或片段裁剪参数"))
        return findings


@dataclass
class QCReport(Artifact):
    """quality_check 阶段产出"""
    black_frames: int = 0
    audio_present: bool = True
    audio_sync_ok: bool = True
    file_size_mb: float = 0.0
    passed: bool = False

    def validate(self) -> list[Finding]:
        findings = []
        if self.black_frames > 0:
            findings.append(Finding(Severity.critical, "black_frames",
                "quality_check", f"检测到 {self.black_frames} 帧黑帧",
                "检查过渡片段或片段入点/出点"))
        if not self.audio_present:
            findings.append(Finding(Severity.critical, "audio",
                "quality_check", "输出无音频轨道",
                "检查音频混音参数"))
        if not self.audio_sync_ok:
            findings.append(Finding(Severity.critical, "audio_sync",
                "quality_check", "音画不同步",
                "检查音频采样率和帧率匹配"))
        return findings


# ─── Stage Result — 阶段执行结果 ────────────────────────────────────

@dataclass
class StageResult:
    """一个阶段的完整执行结果"""
    stage_name: str
    status: str  # "completed" | "failed" | "skipped"
    artifact: Artifact | None = None
    findings: list[Finding] = field(default_factory=list)
    checkpoint_path: str = ""
    duration_seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "stage_name": self.stage_name,
            "status": self.status,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "findings": [f.to_dict() for f in self.findings],
            "checkpoint_path": self.checkpoint_path,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


# ─── Artifact Registry — schema 名 → 类 ─────────────────────────────

ARTIFACT_REGISTRY: dict[str, type[Artifact]] = {
    "validation_report": ValidationReport,
    "audio_manifest": AudioManifest,
    "subtitle_manifest": SubtitleManifest,
    "composition_report": CompositionReport,
    "qc_report": QCReport,
}


def artifact_from_dict(d: dict) -> Artifact:
    """从 dict 反序列化为正确的 Artifact 子类"""
    stage = d.get("stage_name", "")
    schema_map = {
        "validate": ValidationReport,
        "audio_prep": AudioManifest,
        "subtitle": SubtitleManifest,
        "compose": CompositionReport,
        "quality_check": QCReport,
    }
    cls = schema_map.get(stage, Artifact)
    return cls.from_dict(d)
