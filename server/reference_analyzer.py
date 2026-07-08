"""参考视频风格分析器 — 提取编辑指纹，支持风格复刻"""

import subprocess
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SegmentInfo:
    """参考视频中的一个片段"""
    start: float          # 起始时间（秒）
    duration: float       # 持续时长（秒）
    is_transition: bool   # 是否为转场段（极短片）
    transition_type: str  # "cut" | "flash" | "dissolve" | ""


@dataclass
class EditingFingerprint:
    """视频编辑风格指纹"""
    total_duration: float
    segment_count: int
    avg_duration: float
    min_duration: float
    max_duration: float
    duration_distribution: dict
    rhythm_pattern: list[str]
    segments: list[SegmentInfo]
    bpm_estimate: float
    source_path: str
    keyframe_paths: list[str] = field(default_factory=list)  # 关键帧截图路径
    style_description: str = ""  # VLM 生成的风格描述

    def summary(self) -> str:
        base = (
            f"参考视频: {Path(self.source_path).name}\n"
            f"总时长: {self.total_duration:.0f}s · {self.segment_count} 个片段\n"
            f"平均片段: {self.avg_duration:.1f}s (最短{self.min_duration:.1f}s, 最长{self.max_duration:.1f}s)\n"
            f"节奏分布: {self.duration_distribution}\n"
            f"估算BPM: {self.bpm_estimate:.0f}"
        )
        if self.style_description:
            base += f"\n\n风格描述:\n{self.style_description}"
        return base

    def to_clip_template(self, source_duration: float) -> list[dict]:
        """将指纹映射到源素材上，生成建议片段
        
        不是机械缩放时长，而是：
        1. 提取节奏模式（L/M/S/T 序列）
        2. 按源素材总时长分配比例
        3. 保留参考视频的节奏结构
        """
        if self.segment_count == 0 or source_duration <= 0:
            return []

        # 只取非转场片段
        content_segs = [s for s in self.segments if not s.is_transition]
        if not content_segs:
            return []

        # 计算每个片段应占的时长权重（基于节奏分类）
        def segment_weight(dur: float) -> float:
            if dur < 2: return 1.0      # short
            elif dur <= 6: return 2.5   # medium
            else: return 4.0            # long

        weights = [segment_weight(s.duration) for s in content_segs]
        total_weight = sum(weights)

        clips = []
        source_pos = 0.0

        for i, seg in enumerate(content_segs):
            # 按权重分配时长
            seg_dur = (weights[i] / total_weight) * source_duration

            # 限制范围
            if weights[i] <= 1.0:
                seg_dur = max(1.5, min(3.0, seg_dur))
            elif weights[i] <= 2.5:
                seg_dur = max(2.0, min(8.0, seg_dur))
            else:
                seg_dur = max(3.0, min(15.0, seg_dur))

            end_pos = min(source_pos + seg_dur, source_duration)
            if end_pos - source_pos < 1.0:
                continue

            # 找前一个 segment 的转场类型
            trans_after = ""
            seg_idx_in_all = self.segments.index(seg)
            if seg_idx_in_all + 1 < len(self.segments):
                next_seg = self.segments[seg_idx_in_all + 1]
                if next_seg.is_transition:
                    trans_after = next_seg.transition_type or "cut"

            clips.append({
                "start": round(source_pos, 1),
                "end": round(end_pos, 1),
                "duration": round(end_pos - source_pos, 1),
                "label": f"片段{len(clips)+1}",
                "speed": 1.0,
                "transition_after": trans_after,
            })
            source_pos = end_pos

            if source_pos >= source_duration:
                break

        return clips

    def to_dict(self) -> dict:
        return {
            "total_duration": self.total_duration,
            "segment_count": self.segment_count,
            "avg_duration": self.avg_duration,
            "rhythm_pattern": self.rhythm_pattern,
            "bpm_estimate": self.bpm_estimate,
            "source_path": self.source_path,
        }


class ReferenceAnalyzer:
    """分析参考视频的编辑风格"""

    def __init__(self):
        pass

    def analyze(self, filepath: str, use_vision: bool = False) -> EditingFingerprint:
        """分析视频，提取编辑指纹"""
        keyframes = self._extract_keyframes(filepath)
        if not keyframes:
            raise ValueError("无法提取关键帧")

        segments = self._build_segments(keyframes)
        duration_dist = self._classify_durations(segments)
        rhythm = self._build_rhythm_pattern(segments)
        bpm = self._estimate_bpm(segments)

        durations = [s.duration for s in segments if not s.is_transition]
        avg_dur = sum(durations) / len(durations) if durations else 0

        fp = EditingFingerprint(
            total_duration=keyframes[-1] if keyframes else 0,
            segment_count=len([s for s in segments if not s.is_transition]),
            avg_duration=round(avg_dur, 1),
            min_duration=round(min(durations), 1) if durations else 0,
            max_duration=round(max(durations), 1) if durations else 0,
            duration_distribution=duration_dist,
            rhythm_pattern=rhythm[:20],
            segments=segments,
            bpm_estimate=round(bpm, 0),
            source_path=filepath,
        )

        # 视觉分析（可选，需要 VLM）
        if use_vision:
            try:
                kf_paths = self._extract_keyframe_images(filepath, keyframes, max_frames=6)
                fp.keyframe_paths = kf_paths
                if kf_paths:
                    fp.style_description = self._vision_analyze_style(kf_paths, fp)
            except Exception as e:
                fp.style_description = f"(视觉分析失败: {e})"

        return fp

    def _extract_keyframe_images(self, filepath: str, keyframes: list[float],
                                  max_frames: int = 6) -> list[str]:
        """提取关键帧截图（均匀采样）"""
        import tempfile
        tmpdir = Path(tempfile.mkdtemp(prefix="ref_frames_"))

        # 均匀采样 max_frames 个关键帧
        if len(keyframes) <= max_frames:
            samples = keyframes
        else:
            step = len(keyframes) / max_frames
            samples = [keyframes[int(i * step)] for i in range(max_frames)]

        paths = []
        for i, ts in enumerate(samples):
            out = tmpdir / f"frame_{i:02d}_{ts:.0f}s.jpg"
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", str(ts),
                "-i", filepath,
                "-vframes", "1",
                "-q:v", "2",
                str(out),
            ]
            try:
                subprocess.run(cmd, check=True, timeout=10)
                if out.exists() and out.stat().st_size > 100:
                    paths.append(str(out))
            except:
                continue

        return paths

    def _vision_analyze_style(self, image_paths: list[str],
                               fp: EditingFingerprint) -> str:
        """用 VLM 分析关键帧，描述剪辑风格"""
        from .llm_config import get_client

        rhythm_str = " ".join(fp.rhythm_pattern[:15])
        prompt = f"""你是视频剪辑风格分析师。下面是这个视频的 {len(image_paths)} 个关键帧截图（按时间顺序）。

已知数据：
- 总时长 {fp.total_duration:.0f}s，{fp.segment_count} 个片段
- 平均片段 {fp.avg_duration:.1f}s，节奏模式: {rhythm_str}
- BPM 约 {fp.bpm_estimate:.0f}

请根据关键帧描述这个视频的**剪辑风格**，重点分析：
1. 画面内容特征（游戏/实拍/动画？色调？运镜风格？）
2. 剪辑节奏（快切还是慢剪？有无明显变速段落？）
3. 转场风格（硬切为主还是有闪白/淡入淡出？）
4. 情绪曲线（如何开场→高潮→收尾？）
5. 可复刻的关键手法（用 3-5 条简洁规则概括）

请用中文回答，控制在 200 字以内，只输出分析结果不要寒暄。"""

        try:
            client = get_client()
            return client.vision_generate(prompt, image_paths, temperature=0.3, max_tokens=512)
        except Exception as e:
            return f"VLM 调用失败: {e}"

    def _extract_keyframes(self, filepath: str) -> list[float]:
        """用 ffprobe 提取所有关键帧时间戳"""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,flags",
            "-of", "csv=p=0",
            filepath,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not result.stdout:
            # fallback: 用 ffmpeg 做场景检测
            return self._fallback_scene_detect(filepath)

        timestamps = []
        for line in result.stdout.strip().split("\n"):
            if ",K" in line or "K_" in line:
                try:
                    ts = float(line.split(",")[0])
                    timestamps.append(ts)
                except ValueError:
                    continue

        return timestamps

    def _fallback_scene_detect(self, filepath: str) -> list[float]:
        """场景检测作为 fallback"""
        cmd = [
            "ffprobe", "-v", "error",
            "-f", "lavfi",
            f"movie={filepath},select='gt(scene\\,0.3)'",
            "-show_entries", "frame=pkt_pts_time",
            "-of", "csv=p=0",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            timestamps = []
            for line in result.stdout.strip().split("\n"):
                try:
                    timestamps.append(float(line.strip()))
                except ValueError:
                    continue
            return timestamps
        except:
            return []

    def _build_segments(self, keyframes: list[float]) -> list[SegmentInfo]:
        """从关键帧构建片段列表"""
        segments = []
        for i in range(1, len(keyframes)):
            start = keyframes[i - 1]
            dur = keyframes[i] - start

            # 极短片 (< 0.5s) 标记为转场
            is_trans = dur < 0.5
            trans_type = ""
            if is_trans:
                # 尝试识别转场类型
                if 0.1 <= dur <= 0.5:
                    trans_type = "flash"  # 闪白通常 0.2-0.5s
                elif dur < 0.1:
                    trans_type = "cut"    # 极短 = 硬切

            segments.append(SegmentInfo(
                start=start,
                duration=dur,
                is_transition=is_trans,
                transition_type=trans_type,
            ))

        return segments

    def _classify_durations(self, segments: list[SegmentInfo]) -> dict:
        """分类片段时长"""
        result = {"short(<2s)": 0, "medium(2-6s)": 0, "long(>6s)": 0}
        for s in segments:
            if s.is_transition:
                continue
            if s.duration < 2:
                result["short(<2s)"] += 1
            elif s.duration <= 6:
                result["medium(2-6s)"] += 1
            else:
                result["long(>6s)"] += 1
        return result

    def _build_rhythm_pattern(self, segments: list[SegmentInfo]) -> list[str]:
        """构建节奏模式（缩写版）"""
        pattern = []
        for s in segments:
            if s.is_transition:
                pattern.append("T")  # transition
            elif s.duration < 2:
                pattern.append("S")  # short
            elif s.duration <= 6:
                pattern.append("M")  # medium
            else:
                pattern.append("L")  # long
        return pattern

    def _estimate_bpm(self, segments: list[SegmentInfo]) -> float:
        """从片段时长估算 BPM（用于卡点）"""
        durations = [s.duration for s in segments if not s.is_transition and 0.5 < s.duration < 15]
        if len(durations) < 3:
            return 120.0  # 默认

        # 片段时长 → 每分钟节拍数（一段 ≈ 4拍）
        # 平均片段时长 2s → 每拍 0.5s → 120 BPM
        avg = sum(durations) / len(durations)
        bpm_per_segment = 60 / avg * 4

        # 量化到常见 BPM: 60, 80, 90, 100, 110, 120, 128, 140, 150, 160
        common_bpms = [60, 70, 80, 90, 100, 110, 120, 128, 140, 150, 160, 175, 180]
        return min(common_bpms, key=lambda x: abs(x - bpm_per_segment))
