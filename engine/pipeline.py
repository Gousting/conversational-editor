"""管道执行引擎 — YAML 驱动的多阶段生产流水线

用法:
    engine = PipelineEngine("/tmp/conversational-editor/pipelines")
    result = engine.run("game-highlight", session, callbacks={...})

管道定义 (pipelines/game-highlight.yaml):
    stages:
      - name: validate
        produces: validation_report
        review_focus: [...]
        success_criteria: [...]
      - name: compose
        requires: [validation_report]
        produces: composition_report
        ...

每个阶段由 StageHandler 函数实现，通过 STAGE_HANDLERS 注册表映射。
"""

from __future__ import annotations

import json
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .artifact import (
    Artifact, Finding, StageResult,
    ValidationReport, AudioManifest, SubtitleManifest, CompositionReport, QCReport,
    ARTIFACT_REGISTRY, artifact_from_dict,
)
from .reviewer import Reviewer


# ─── Stage Handler 类型 ─────────────────────────────────────────────

# handler(stage_config, session, input_artifacts, checkpoint_dir) → Artifact
StageHandler = Callable[[dict, Any, dict[str, Artifact], Path], Artifact]


# ─── 默认 Stage Handler 实现 ────────────────────────────────────────

def _handle_validate(stage_config: dict, session, inputs: dict, ckpt_dir: Path) -> ValidationReport:
    """验证时间轴合法性"""
    timeline = session.timeline

    issues = []
    clips = timeline.items

    if not clips:
        issues.append({"type": "no_clips", "msg": "时间轴为空"})

    for i, item in enumerate(clips):
        if item.item_type == "clip":
            c = item.data
            dur = c.output_duration
            if dur < 1.5:
                issues.append({"type": "clip_too_short", "msg": f"片段 #{i+1} 时长 {dur:.1f}s < 1.5s"})

    return ValidationReport(
        stage_name="validate",
        clip_count=len([i for i in clips if i.item_type == "clip"]),
        total_duration=timeline.total_duration,
        transition_count=sum(1 for i in clips if i.item_type == "transition"),
        issues=issues,
        passed=len(issues) == 0,
    )


def _handle_audio_prep(stage_config: dict, session, inputs: dict, ckpt_dir: Path) -> AudioManifest:
    """音频准备：BGM 分析 + 视频原声提取"""
    tracks = []
    silence_segments = []
    source_path = session.source_path

    if source_path:
        tracks.append({
            "type": "dialogue",
            "path": source_path,
            "duration": session.media_store.get_source(session.current_source_id).duration if session.current_source_id else 0,
            "peak_db": -3.0,
        })

    bgm_path = ""
    bgm_volume = -12.0
    bgm_duration = 0.0
    dialogue_to_bgm = 4.0

    # 如果有 BGM，使用真实分析数据
    if session.bgm_analysis:
        bgm = session.bgm_analysis
        bgm_path = bgm.path
        bgm_duration = bgm.duration
        
        # 计算对白/BGM 音量比：取波形中位数作为估算
        if bgm.waveform:
            import statistics
            median_energy = statistics.median(bgm.waveform)
            # 中等能量 = BGM 正常音量 → 约 -12dB
            dialogue_to_bgm = 4.0 if median_energy < 0.4 else 3.0  # 高能 BGM 时比例调紧

        # 标记 BGM 低谷段为潜在静音
        if bgm.valley_sections:
            silence_segments = [
                {"start": v["start"], "end": v["end"], "type": "bgm_valley"}
                for v in bgm.valley_sections
            ]

    return AudioManifest(
        stage_name="audio_prep",
        tracks=tracks,
        bgm_path=bgm_path,
        bgm_volume_db=bgm_volume,
        silence_segments=silence_segments,
        peak_level=-3.0,
        dialogue_to_bgm_ratio=dialogue_to_bgm,
    )


def _handle_subtitle(stage_config: dict, session, inputs: dict, ckpt_dir: Path) -> SubtitleManifest:
    """字幕生成"""
    subtitles = []
    srt_path = ""

    # 如果有标记点，生成占位字幕
    markers = getattr(session, "markers", [])
    for m in markers:
        label = m.get("label", "")
        if label:
            subtitles.append({
                "start": m.get("time", 0),
                "end": m.get("time", 0) + 2.0,
                "text": label,
                "style": "default",
            })

    return SubtitleManifest(
        stage_name="subtitle",
        subtitles=subtitles,
        style={"font": "Arial", "size": 24, "color": "#FFFFFF", "position": "bottom"},
        srt_path=srt_path,
        burned=False,
    )


def _handle_compose(stage_config: dict, session, inputs: dict, ckpt_dir: Path) -> CompositionReport:
    """视频合成：拼接片段 + 转场 + BGM 混音 + 音频闪避"""
    import subprocess, tempfile, os

    timeline = session.timeline
    sources = {session.current_source_id: session.source_path}
    output = ckpt_dir / f"compose_output_{session.id}.mp4"

    # 1. 拼接视频片段
    concat_file = session.renderer._build_concat_script(timeline, sources)

    # 2. 构建 ffmpeg 命令
    cmd = ["ffmpeg", "-y", "-v", "error"]

    # 视频输入
    cmd += ["-f", "concat", "-safe", "0", "-i", concat_file]

    # BGM 输入（如果有）
    bgm_path = ""
    if session.bgm_path and os.path.exists(session.bgm_path):
        bgm_path = session.bgm_path
        cmd += ["-stream_loop", "-1", "-i", bgm_path]

    has_bgm = bool(bgm_path)

    # 构建 filter_complex
    if has_bgm:
        # 音频闪避：标记为 dialogue 的片段压 BGM
        duck_filter = _build_duck_filter(session, bgm_path)
        filter_str = (
            f"[0:a]volume=1.0[vid_a];"
            f"[1:a]{duck_filter}[bgm_a];"
            f"[vid_a][bgm_a]amix=inputs=2:duration=first:weights=1.0 0.7[out_a]"
        )
        cmd += ["-filter_complex", filter_str, "-map", "0:v", "-map", "[out_a]"]
    else:
        cmd += ["-map", "0:v", "-map", "0:a?"]

    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"合成失败: {result.stderr[-300:]}")

    file_size = output.stat().st_size if output.exists() else 0

    duration = timeline.total_duration
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(probe.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        pass

    session.preview_path = str(output)

    return CompositionReport(
        stage_name="compose",
        output_path=str(output),
        output_duration=duration,
        expected_duration=timeline.total_duration,
        file_size_bytes=file_size,
        encoding_params={"codec": "libx264", "crf": 28, "preset": "ultrafast",
                        "bgm": bool(bgm_path), "audio_ducking": has_bgm},
        xfade_transitions=sum(1 for i in timeline.items if i.item_type == "transition"),
    )


def _build_duck_filter(session, bgm_path: str) -> str:
    """构建音频闪避过滤器：对话段 BGM 压低到 25%，动作段推到 100%"""
    if not hasattr(session, 'emotion_map') or not session.emotion_map:
        # 无情绪标记 → 中性处理
        return "volume=0.6"

    timeline = session.timeline
    segments = []
    for i, item in enumerate(timeline.items):
        if item.item_type != "clip":
            continue
        emotion = session.emotion_map.get(i, "action")
        clip_start = sum(
            (t.data.output_duration for t in timeline.items[:i]
             if t.item_type == "clip"), 0.0
        )
        dur = item.data.output_duration
        volume = "0.25" if emotion == "dialogue" else "1.0"
        segments.append(
            f"volume='if(between(t,{clip_start:.3f},{clip_start+dur:.3f}),{volume},1)':eval=frame"
        )

    if not segments:
        return "volume=0.7"

    # 链式：先在静默段给默认值，再逐段覆盖
    chained = "volume=0.7"
    for seg in segments:
        chained += "," + seg
    return chained


def _handle_quality_check(stage_config: dict, session, inputs: dict, ckpt_dir: Path) -> QCReport:
    """最终质量检查"""
    import subprocess, os

    comp = inputs.get("composition_report")
    if not isinstance(comp, CompositionReport):
        return QCReport(stage_name="quality_check", passed=False)

    output_path = comp.output_path
    black_frames = 0
    audio_present = False
    audio_sync_ok = True

    if output_path and os.path.exists(output_path):
        # 检测黑帧
        try:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", output_path,
                 "-vf", "blackdetect=d=0.1:pix_th=0.10",
                 "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            for line in result.stderr.split("\n"):
                if "black_start" in line:
                    black_frames += 1
        except subprocess.SubprocessError:
            pass

        # 检测音频
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=codec_type",
                 "-of", "csv=p=0", output_path],
                capture_output=True, text=True, timeout=10,
            )
            audio_present = "audio" in result.stdout
        except subprocess.SubprocessError:
            pass

    file_size_mb = comp.file_size_bytes / (1024 * 1024) if comp.file_size_bytes else 0

    return QCReport(
        stage_name="quality_check",
        black_frames=black_frames,
        audio_present=audio_present,
        audio_sync_ok=audio_sync_ok,
        file_size_mb=round(file_size_mb, 1),
        passed=black_frames == 0 and audio_present,
    )


# ─── Stage Handler 注册表 ───────────────────────────────────────────

STAGE_HANDLERS: dict[str, StageHandler] = {
    "validate": _handle_validate,
    "audio_prep": _handle_audio_prep,
    "subtitle": _handle_subtitle,
    "compose": _handle_compose,
    "quality_check": _handle_quality_check,
}


# ─── 管道引擎 ───────────────────────────────────────────────────────

class PipelineEngine:
    """YAML 驱动的多阶段生产引擎。

    用法:
        engine = PipelineEngine(pipelines_dir="/tmp/conversational-editor/pipelines")
        result = engine.run(
            pipeline_name="game-highlight",
            session=edit_session,
            callbacks={"on_stage_complete": fn, "on_checkpoint": fn},
        )
    """

    def __init__(self, pipelines_dir: str = "/tmp/conversational-editor/pipelines"):
        self.pipelines_dir = Path(pipelines_dir)
        self.reviewer = Reviewer()
        self._handlers = dict(STAGE_HANDLERS)

    def register_handler(self, name: str, handler: StageHandler) -> None:
        """注册自定义阶段处理器"""
        self._handlers[name] = handler

    def run(
        self,
        pipeline_name: str,
        session: Any,
        callbacks: dict[str, Callable] | None = None,
    ) -> dict:
        """执行完整管道。

        Args:
            pipeline_name: YAML 文件名（不含 .yaml）
            session: EditSession 实例
            callbacks:
                on_stage_start(stage_name, stage_index, total_stages)
                on_stage_complete(stage_result: StageResult, stage_index, total_stages)
                on_pipeline_complete(all_results: list[StageResult])

        Returns:
            {"success": bool, "stages": [...], "output_path": str}
        """
        callbacks = callbacks or {}
        yaml_path = self.pipelines_dir / f"{pipeline_name}.yaml"

        if not yaml_path.exists():
            raise FileNotFoundError(f"管道定义不存在: {yaml_path}")

        with open(yaml_path) as f:
            pipeline = yaml.safe_load(f)

        stages = pipeline.get("stages", [])
        total = len(stages)
        results: list[StageResult] = []
        artifacts: dict[str, Artifact] = {}
        checkpoint_dir = Path(f"/tmp/conversational-editor/checkpoints/{session.id}")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        on_stage_start = callbacks.get("on_stage_start")
        on_stage_complete = callbacks.get("on_stage_complete")
        on_pipeline_complete = callbacks.get("on_pipeline_complete")

        for i, stage_cfg in enumerate(stages):
            stage_name = stage_cfg["name"]
            required = stage_cfg.get("requires", [])
            if isinstance(required, str):
                required = [required]
            produces = stage_cfg.get("produces", "")
            review_focus = stage_cfg.get("review_focus", [])
            success_criteria = stage_cfg.get("success_criteria", [])
            checkpoint_required = stage_cfg.get("checkpoint_required", False)
            human_approval = stage_cfg.get("human_approval_default", False)

            if on_stage_start:
                on_stage_start(stage_name, i, total)

            t0 = time.time()
            result = StageResult(stage_name=stage_name, status="completed")
            checkpoint_path = str(checkpoint_dir / f"checkpoint_{stage_name}.json")

            # 检查是否已有通过的 checkpoint
            if checkpoint_required and Path(checkpoint_path).exists():
                try:
                    ck = json.loads(Path(checkpoint_path).read_text())
                    if ck.get("status") == "completed":
                        artifact = artifact_from_dict(ck.get("artifact", {}))
                        artifacts[produces] = artifact
                        result.status = "skipped"
                        result.artifact = artifact
                        result.checkpoint_path = checkpoint_path
                        results.append(result)
                        if on_stage_complete:
                            on_stage_complete(result, i, total)
                        continue
                except Exception:
                    pass  # 损坏的 checkpoint，重新执行

            try:
                # 收集输入 artifacts
                input_artifacts = {}
                for req in required:
                    if req in artifacts:
                        input_artifacts[req] = artifacts[req]

                # 执行阶段处理器
                handler = self._handlers.get(stage_name)
                if not handler:
                    raise ValueError(f"未注册的阶段处理器: {stage_name}")

                artifact = handler(stage_cfg, session, input_artifacts, checkpoint_dir)

                # 质检
                playbook = stage_cfg.get("playbook")
                findings = self.reviewer.review(artifact, review_focus, success_criteria, playbook)

                # 存储 artifact
                if produces:
                    artifacts[produces] = artifact

                result.artifact = artifact
                result.findings = findings
                result.duration_seconds = round(time.time() - t0, 2)
                result.checkpoint_path = checkpoint_path

                # checkpoint
                if checkpoint_required:
                    ck_data = {
                        "stage_name": stage_name,
                        "status": "completed",
                        "artifact": artifact.to_dict() if artifact else None,
                        "timestamp": datetime.now().isoformat(),
                    }
                    Path(checkpoint_path).write_text(json.dumps(ck_data, indent=2, ensure_ascii=False))

            except Exception as e:
                result.status = "failed"
                result.error = str(e)
                result.duration_seconds = round(time.time() - t0, 2)
                results.append(result)
                if on_stage_complete:
                    on_stage_complete(result, i, total)
                # 失败不继续
                break

            results.append(result)
            if on_stage_complete:
                on_stage_complete(result, i, total)

        # 汇总
        all_passed = all(r.status == "completed" or r.status == "skipped" for r in results)
        output_path = ""
        if "composition_report" in artifacts:
            comp = artifacts["composition_report"]
            if isinstance(comp, CompositionReport):
                output_path = comp.output_path

        pipeline_result = {
            "success": all_passed,
            "pipeline": pipeline_name,
            "stages": [r.to_dict() for r in results],
            "output_path": output_path,
        }

        if on_pipeline_complete:
            on_pipeline_complete(pipeline_result)

        return pipeline_result
