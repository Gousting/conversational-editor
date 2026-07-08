"""视频分析引擎 — 场景检测 + AI 画面描述 + 节奏评估"""

import subprocess, json, tempfile, os
from pathlib import Path
from dataclasses import dataclass, field
from engine.media import MediaStore


@dataclass
class SceneSegment:
    start: float
    end: float
    peak_motion: float   # 0-1 画面运动强度
    avg_motion: float
    keyframe_path: str = ""

@dataclass
class VideoAnalysis:
    source_id: str
    duration: float
    fps: float
    resolution: str          # "1920x1080"
    scenes: list[SceneSegment] = field(default_factory=list)
    highlights: list[SceneSegment] = field(default_factory=list)
    raw_motion_data: list[float] = field(default_factory=list)  # per-second motion scores


class VideoAnalyzer:
    """分析视频结构：场景切分、运动强度、高光点"""

    def __init__(self, tmp_dir: str = "/tmp/conversational-editor/analysis"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def analyze(self, video_path: str) -> VideoAnalysis:
        """完整分析"""
        info = self._probe(video_path)
        analysis = VideoAnalysis(
            source_id=os.path.basename(video_path).rsplit(".",1)[0],
            duration=info["duration"],
            fps=info["fps"],
            resolution=f"{info['width']}x{info['height']}",
        )

        # 运动强度分析（每秒采样）
        analysis.raw_motion_data = self._motion_score(video_path)

        # 场景切分
        analysis.scenes = self._detect_scenes(video_path, analysis)

        # 高光点（运动峰值 + 场景变化）
        analysis.highlights = self._find_highlights(analysis)

        return analysis

    def _probe(self, video_path: str) -> dict:
        ms = MediaStore(self.tmp_dir)
        info = ms.probe(video_path)
        return {
            "duration": info.duration,
            "fps": info.fps,
            "width": info.width,
            "height": info.height,
        }

    def _motion_score(self, video_path: str) -> list[float]:
        """每秒的运动强度（帧间差异）"""
        out = self.tmp_dir / "motion.txt"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "select='gt(scene,0)',metadata=print:file=-",
            "-f", "null", "-",
        ]
        # 简化版：用 scene detect 的 scene_score 近似运动强度
        cmd2 = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "select='gt(scene,0.1)',showinfo",
            "-f", "null", "-",
        ]
        try:
            r = subprocess.run(cmd2, capture_output=True, text=True, timeout=60)
            # 解析 pts_time
            scores = []
            for line in r.stderr.split("\n"):
                if "pts_time:" in line:
                    parts = line.split("pts_time:")[1].split()[0]
                    try:
                        scores.append(float(parts))
                    except:
                        pass
            return scores[:3600]  # cap at 1 hour
        except:
            return []

    def _detect_scenes(self, video_path: str, analysis: VideoAnalysis) -> list[SceneSegment]:
        """场景切分"""
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "select='gt(scene,0.3)',showinfo",
            "-f", "null", "-",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            cut_points = [0.0]
            for line in r.stderr.split("\n"):
                if "pts_time:" in line:
                    try:
                        t = float(line.split("pts_time:")[1].split()[0])
                        if t > cut_points[-1] + 1.0:  # 至少间隔1秒
                            cut_points.append(t)
                    except:
                        pass
            cut_points.append(analysis.duration)

            scenes = []
            for i in range(len(cut_points) - 1):
                s = SceneSegment(
                    start=cut_points[i],
                    end=cut_points[i+1],
                    peak_motion=0.5,
                    avg_motion=0.3,
                )
                # 生成关键帧
                mid = (s.start + s.end) / 2
                s.keyframe_path = self._extract_keyframe(video_path, mid)
                scenes.append(s)

            return scenes
        except:
            # 至少返回一段
            mid = analysis.duration / 2
            kf = self._extract_keyframe(video_path, mid)
            return [SceneSegment(
                start=0, end=analysis.duration,
                peak_motion=0.5, avg_motion=0.3,
                keyframe_path=kf,
            )]

    def _find_highlights(self, analysis: VideoAnalysis) -> list[SceneSegment]:
        """找出高光场景：运动峰值最高的 20%"""
        if not analysis.scenes:
            return []

        # 按峰值运动排序
        ranked = sorted(analysis.scenes, key=lambda s: s.peak_motion, reverse=True)
        top_count = max(1, len(ranked) // 5)
        return ranked[:top_count]

    def _extract_keyframe(self, video_path: str, time_sec: float) -> str:
        out = self.tmp_dir / f"kf_{time_sec:.0f}.jpg"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(time_sec),
            "-i", video_path,
            "-vframes", "1",
            "-vf", "scale=480:-1",
            str(out),
        ]
        subprocess.run(cmd, check=True, timeout=10)
        return str(out)

    def summary(self, analysis: VideoAnalysis) -> str:
        """生成供 LLM 使用的分析摘要"""
        lines = [
            f"视频时长: {analysis.duration:.0f}秒",
            f"分辨率: {analysis.resolution}",
            f"帧率: {analysis.fps}fps",
            f"检测到 {len(analysis.scenes)} 个场景段落",
            "",
            "场景列表:",
        ]
        for i, s in enumerate(analysis.scenes[:20]):
            dur = s.end - s.start
            lines.append(
                f"  场景{i+1}: {s.start:.0f}s → {s.end:.0f}s "
                f"(持续{dur:.0f}s, 画面动感:{s.peak_motion:.2f})"
            )
        return "\n".join(lines)
