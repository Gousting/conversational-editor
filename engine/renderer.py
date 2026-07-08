"""渲染引擎 — ffmpeg 封装"""

import subprocess
import tempfile
import os
import json
from pathlib import Path
from .timeline import Timeline


class Renderer:
    def __init__(self, media_dir: str = "/tmp/conversational-editor"):
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def _build_concat_script(self, timeline: Timeline,
                              source_paths: dict[str, str]) -> str:
        """生成 ffmpeg concat demuxer 文件

        ffmpeg concat 格式:
            file '/path/to/segment.mp4'
            duration 3.0
        """
        lines = []
        temp_dir = Path(tempfile.mkdtemp(dir=self.media_dir, prefix="segments_"))

        for i, item in enumerate(timeline.items):
            if item.item_type == "clip":
                clip = item.data
                src = source_paths.get(clip.source_id, "")
                seg_path = temp_dir / f"seg_{i:04d}.mp4"

                # 切出片段（带变速）
                if clip.speed != 1.0:
                    # speed filter: setpts 调整播放速度
                    speed_pts = 1.0 / clip.speed
                    vf = f"setpts={speed_pts}*PTS"
                    af = f"atempo={clip.speed}"
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-ss", str(clip.source_start),
                        "-t", str(clip.source_duration),
                        "-i", src,
                        "-vf", vf,
                        "-af", af,
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "28",
                        "-c:a", "aac",
                        str(seg_path),
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-ss", str(clip.source_start),
                        "-t", str(clip.source_duration),
                        "-i", src,
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "28",
                        "-c:a", "aac",
                        str(seg_path),
                    ]
                subprocess.run(cmd, check=True, timeout=120)

                lines.append(f"file '{seg_path}'")
                lines.append(f"duration {clip.output_duration}")

            elif item.item_type == "transition":
                trans = item.data
                if trans.effect == "flash":
                    # 生成纯色过渡片段
                    trans_path = temp_dir / f"trans_{i:04d}.mp4"
                    color = trans.params.get("color", "#FFE4B5").lstrip("#")
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi",
                        "-i", f"color=c=0x{color}:s=1920x1080:d={trans.duration}:r=30",
                        "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={trans.duration}",
                        "-shortest",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "18",
                        "-c:a", "aac",
                        str(trans_path),
                    ]
                    subprocess.run(cmd, check=True, timeout=30)
                    lines.append(f"file '{trans_path}'")
                    lines.append(f"duration {trans.duration}")
                elif trans.effect == "dissolve":
                    # 简单实现：黑色淡入淡出
                    trans_path = temp_dir / f"trans_{i:04d}.mp4"
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi",
                        "-i", f"color=c=black:s=1920x1080:d={trans.duration}:r=30",
                        "-f", "lavfi",
                        "-i", f"anullsrc=r=44100:cl=stereo",
                        "-shortest",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "18",
                        "-c:a", "aac",
                        str(trans_path),
                    ]
                    subprocess.run(cmd, check=True, timeout=30)
                    lines.append(f"file '{trans_path}'")
                    lines.append(f"duration {trans.duration}")
                else:
                    # "cut" — 硬切，什么都不加
                    pass

        # ffmpeg concat 要求最后一行是 file 而不是 duration
        concat_file = temp_dir / "concat.txt"
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        return str(concat_file)

    def render(self, timeline: Timeline, source_paths: dict[str, str],
               output_path: str, preview: bool = False) -> str:
        """渲染时间轴

        Args:
            timeline: 时间轴对象
            source_paths: {source_id: 文件路径}
            output_path: 输出文件路径
            preview: True = 低分辨率快速预览

        Returns:
            输出文件路径
        """
        if not timeline.items:
            raise ValueError("时间轴为空")

        concat_file = self._build_concat_script(timeline, source_paths)

        if preview:
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-vf", "scale=854:480",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "32",
                "-c:a", "aac",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c:v", "libx264", "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-movflags", "+faststart",
                output_path,
            ]

        subprocess.run(cmd, check=True, timeout=300)
        return output_path

    def render_preview(self, timeline: Timeline,
                       source_paths: dict[str, str]) -> str:
        """生成低分辨率预览"""
        preview_path = self.media_dir / f"preview_{timeline.version}.mp4"
        return self.render(timeline, source_paths, str(preview_path), preview=True)

    def render_final(self, timeline: Timeline,
                     source_paths: dict[str, str],
                     output_path: str) -> str:
        """最终渲染 — 使用 xfade 真转场"""
        return self._render_xfade(timeline, source_paths, output_path)

    def _render_xfade(self, timeline: Timeline,
                      source_paths: dict[str, str],
                      output_path: str) -> str:
        """用 filter_complex xfade 实现真转场"""
        if not timeline.items:
            raise ValueError("时间轴为空")

        import subprocess, tempfile

        # 只取 clip，转场通过 xfade 实现
        clips = [item.data for item in timeline.items if item.item_type == "clip"]
        transitions = []
        for item in timeline.items:
            if item.item_type == "transition":
                transitions.append(item.data)

        if len(clips) < 2:
            # 单片段直接输出
            c = clips[0]
            src = source_paths.get(c.source_id, "")
            speed_pts = 1.0 / c.speed if c.speed != 1.0 else 1.0
            cmd = ["ffmpeg", "-y", "-v", "error",
                   "-ss", str(c.source_start), "-t", str(c.source_duration),
                   "-i", src]
            if c.speed != 1.0:
                cmd += ["-vf", f"setpts={speed_pts}*PTS", "-af", f"atempo={c.speed}"]
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
                    "-c:a", "aac", output_path]
            subprocess.run(cmd, check=True, timeout=300)
            return output_path

        # 构建 filter_complex
        filter_parts = []
        prev_label = None
        cumulative_offset = 0.0

        for i, clip in enumerate(clips):
            src = source_paths.get(clip.source_id, "")
            label = f"v{i}"
            dur = clip.source_duration / clip.speed

            # trim + setpts for speed
            if clip.speed != 1.0:
                speed_pts = 1.0 / clip.speed
                filter_parts.append(
                    f"[{i}:v]trim=start={clip.source_start}:duration={clip.source_duration},"
                    f"setpts={speed_pts}*(PTS-STARTPTS)[{label}]"
                )
            else:
                filter_parts.append(
                    f"[{i}:v]trim=start={clip.source_start}:duration={clip.source_duration},"
                    f"setpts=PTS-STARTPTS[{label}]"
                )

            if i > 0:
                # 找这个 clip 之前的转场
                trans = transitions[i-1] if i-1 < len(transitions) else None
                xfade_dur = 0.3
                xfade_type = "fade"
                if trans:
                    xfade_dur = trans.duration
                    if trans.effect == "flash":
                        xfade_type = "fadewhite"
                    elif trans.effect == "dissolve":
                        xfade_type = "fade"

                offset = cumulative_offset - xfade_dur if cumulative_offset > xfade_dur else 0
                out_label = f"x{i}"
                filter_parts.append(
                    f"[{prev_label}][{label}]xfade=transition={xfade_type}:"
                    f"duration={xfade_dur}:offset={offset:.2f}[{out_label}]"
                )
                prev_label = out_label
                cumulative_offset += dur - xfade_dur
            else:
                prev_label = label
                cumulative_offset = dur

        filter_graph = ";".join(filter_parts)

        # Build ffmpeg command
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for clip in clips:
            src = source_paths.get(clip.source_id, "")
            cmd += ["-ss", str(clip.source_start), "-t", str(clip.source_duration), "-i", src]

        cmd += [
            "-filter_complex", filter_graph,
            "-map", f"[{prev_label}]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-an",
            "-movflags", "+faststart",
            output_path,
        ]

        subprocess.run(cmd, check=True, timeout=300)
        return output_path

    def render_with_progress(self, timeline: Timeline,
                              source_paths: dict[str, str],
                              output_path: str,
                              preview: bool = True,
                              on_progress=None,
                              on_cancel_check=None) -> str:
        """带进度回调的渲染

        Args:
            on_progress: callable(pct: float, msg: str) 
            on_cancel_check: callable() -> bool, 返回 True 则取消
        """
        if not timeline.items:
            raise ValueError("时间轴为空")

        concat_file = self._build_concat_script(timeline, source_paths)

        # 计算预估总时长
        total_dur = timeline.total_duration

        if preview:
            cmd = [
                "ffmpeg", "-y",
                "-progress", "pipe:1",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-vf", "scale=854:480",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "32",
                "-c:a", "aac",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-progress", "pipe:1",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c:v", "libx264", "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-movflags", "+faststart",
                output_path,
            ]

        import subprocess
        import re
        import time

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # 存 proc ref 供取消
        if hasattr(self, '_current_proc'):
            self._current_proc = proc

        last_pct = 0
        try:
            for line in proc.stdout:
                # 检查取消
                if on_cancel_check and on_cancel_check():
                    proc.terminate()
                    proc.wait(timeout=5)
                    raise Exception("用户取消")

                m = re.search(r'out_time_ms=(\d+)', line)
                if m and total_dur > 0:
                    ms = int(m.group(1))
                    current = ms / 1_000_000  # 微秒转秒
                    pct = min(current / total_dur * 100, 99)
                    if pct - last_pct >= 1:  # 至少 1% 才推送
                        last_pct = pct
                        if on_progress:
                            on_progress(pct, f"渲染中 {pct:.0f}%")

            proc.wait(timeout=300)
            if proc.returncode != 0:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise Exception(f"ffmpeg 返回码 {proc.returncode}: {stderr[-200:]}")

            if on_progress:
                on_progress(100, "渲染完成")
            return output_path
        finally:
            if hasattr(self, '_current_proc'):
                del self._current_proc

    def cancel_render(self):
        """取消正在进行的渲染"""
        if hasattr(self, '_current_proc') and self._current_proc:
            self._current_proc.terminate()
            try:
                self._current_proc.wait(timeout=5)
            except:
                self._current_proc.kill()
