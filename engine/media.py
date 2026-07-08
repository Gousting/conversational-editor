"""素材管理 — 视频元数据提取、缩略图生成"""

import subprocess
import json
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class VideoInfo:
    id: str
    path: str
    filename: str
    duration: float
    fps: float
    width: int
    height: int
    codec: str
    file_size: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "filename": self.filename,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "file_size": self.file_size,
        }


class MediaStore:
    def __init__(self, media_dir: str = "/tmp/conversational-editor/media"):
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._sources: dict[str, VideoInfo] = {}

    def probe(self, filepath: str) -> VideoInfo:
        """用 ffprobe 提取视频元数据"""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration,size,filename:stream=codec_name,width,height,r_frame_rate",
            "-of", "json",
            filepath,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        info = json.loads(result.stdout)

        fmt = info.get("format", {})
        streams = info.get("streams", [])

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        fps_str = video_stream.get("r_frame_rate", "30/1")
        try:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        except Exception:
            fps = 30.0

        filepath = os.path.abspath(filepath)
        vid = VideoInfo(
            id=os.path.basename(filepath).rsplit(".", 1)[0][:20],
            path=filepath,
            filename=os.path.basename(filepath),
            duration=float(fmt.get("duration", 0)),
            fps=fps,
            width=int(video_stream.get("width", 0)),
            height=int(video_stream.get("height", 0)),
            codec=video_stream.get("codec_name", "unknown"),
            file_size=int(fmt.get("size", 0)),
        )
        self._sources[vid.id] = vid
        return vid

    def get_thumbnail(self, filepath: str, time_sec: float = 0,
                       width: int = 320) -> str:
        """截取视频缩略图，返回文件路径"""
        thumbnail_dir = self.media_dir / "thumbnails"
        thumbnail_dir.mkdir(exist_ok=True)

        filename = os.path.basename(filepath).rsplit(".", 1)[0]
        out_path = thumbnail_dir / f"{filename}_{time_sec:.0f}.jpg"

        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(time_sec),
            "-i", filepath,
            "-vframes", "1",
            "-vf", f"scale={width}:-1",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, timeout=30)
        return str(out_path)

    def get_thumbnails_strip(self, filepath: str, count: int = 10) -> list[str]:
        """生成时间轴缩略图条带"""
        info = self.probe(filepath)
        step = info.duration / (count + 1)
        paths = []
        for i in range(1, count + 1):
            t = step * i
            p = self.get_thumbnail(filepath, t, width=160)
            paths.append(p)
        return paths

    def list_sources(self) -> list[dict]:
        return [v.to_dict() for v in self._sources.values()]

    def get_source(self, source_id: str) -> VideoInfo | None:
        return self._sources.get(source_id)
