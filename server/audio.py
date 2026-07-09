"""音频分析模块 — BGM 节拍检测 + 波形生成 + 情绪分段

纯 ffmpeg 实现，零 Python 音频库依赖。足够用于视频切点对齐。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── 数据模型 ───────────────────────────────────────────────────────

@dataclass
class BeatMarker:
    """节拍标记点"""
    time: float           # 秒
    strength: float       # 0.0 ~ 1.0，强度
    is_drop: bool = False  # 是否是 Drop/重拍点


@dataclass  
class BgmAnalysis:
    """BGM 完整分析结果"""
    path: str
    duration: float
    sample_rate: int
    beats: list[BeatMarker] = field(default_factory=list)      # 节拍点
    waveform: list[float] = field(default_factory=list)         # 归一化波形数据 (每秒 10 个采样点)
    energy_segments: list[dict] = field(default_factory=list)   # 能量段 [{start, end, level}]
    drop_sections: list[dict] = field(default_factory=list)     # Drop/高潮段 [{start, end, intensity}]
    valley_sections: list[dict] = field(default_factory=list)   # 低谷段 [{start, end}]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "sample_rate": self.sample_rate,
            "beats": [{"time": b.time, "strength": b.strength, "is_drop": b.is_drop} for b in self.beats],
            "waveform": self.waveform,
            "energy_segments": self.energy_segments,
            "drop_sections": self.drop_sections,
            "valley_sections": self.valley_sections,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BgmAnalysis":
        analysis = cls(
            path=d.get("path", ""),
            duration=d.get("duration", 0),
            sample_rate=d.get("sample_rate", 44100),
        )
        analysis.beats = [BeatMarker(**b) for b in d.get("beats", [])]
        analysis.waveform = d.get("waveform", [])
        analysis.energy_segments = d.get("energy_segments", [])
        analysis.drop_sections = d.get("drop_sections", [])
        analysis.valley_sections = d.get("valley_sections", [])
        return analysis

    def nearest_beat(self, time: float) -> Optional[BeatMarker]:
        """找到最接近给定时间的节拍点"""
        if not self.beats:
            return None
        return min(self.beats, key=lambda b: abs(b.time - time))


# ─── 分析器 ─────────────────────────────────────────────────────────

class AudioAnalyzer:
    """BGM 分析器。纯 ffmpeg，无外部依赖。"""

    WAVEFORM_SAMPLE_RATE = 10   # 每秒 10 个采样点，适合前端渲染

    def analyze(self, filepath: str) -> BgmAnalysis:
        """分析 BGM 文件，返回完整分析结果。"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"音频文件不存在: {filepath}")

        # 1. 基础探测
        duration, sample_rate = self._probe(filepath)

        # 2. 波形提取
        waveform = self._extract_waveform(filepath, duration)

        # 3. 节拍检测（基于能量峰值）
        beats = self._detect_beats(waveform, duration)

        # 4. 能量分段（找出 Drop/低谷段）
        energy_segments, drops, valleys = self._segment_energy(waveform, duration)

        return BgmAnalysis(
            path=filepath,
            duration=duration,
            sample_rate=sample_rate,
            beats=beats,
            waveform=waveform,
            energy_segments=energy_segments,
            drop_sections=drops,
            valley_sections=valleys,
        )

    def _probe(self, filepath: str) -> tuple[float, int]:
        """探测音频时长和采样率"""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=sample_rate",
            "-of", "json",
            filepath,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))
        
        sample_rate = 44100
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                sample_rate = int(stream.get("sample_rate", 44100))
                break
        
        return duration, sample_rate

    def _extract_waveform(self, filepath: str, duration: float) -> list[float]:
        """提取归一化波形数据。

        使用 ffmpeg astats 获取每 ~0.1s 的 RMS 音量。
        """
        segment_dur = 1.0 / self.WAVEFORM_SAMPLE_RATE  # 0.1s

        cmd = [
            "ffmpeg", "-v", "info",
            "-i", filepath,
            "-af", f"astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(60, int(duration * 3)))

        # 解析 RMS 值：格式为 "[Parsed_ametadata_1 @ ...] lavfi.astats.Overall.RMS_level=-26.68"
        import re
        rms_values = []
        for line in result.stderr.split("\n"):
            m = re.search(r'RMS_level=(-?[\d.]+)', line)
            if m:
                try:
                    val = float(m.group(1))
                    if val > -80:  # 过滤无效/静音值
                        rms_values.append(val)
                except ValueError:
                    continue

        if not rms_values:
            return [0.0] * max(1, int(duration * self.WAVEFORM_SAMPLE_RATE))

        # 下采样到目标采样率
        total_samples = max(1, int(duration * self.WAVEFORM_SAMPLE_RATE))
        if len(rms_values) <= total_samples:
            waveform = rms_values
        else:
            step = len(rms_values) / total_samples
            waveform = [rms_values[int(i * step)] for i in range(total_samples)]

        # 归一化到 0~1
        min_val = min(waveform)
        max_val = max(waveform)
        if max_val - min_val < 0.5:
            return [0.5] * len(waveform)

        return [(v - min_val) / (max_val - min_val) for v in waveform]

    def _detect_beats(self, waveform: list[float], duration: float) -> list[BeatMarker]:
        """基于能量峰值检测节拍。

        算法：滑动窗口找局部峰值，过滤掉太弱的峰，
        然后用峰值间距估算 BPM 做二次过滤。
        """
        if len(waveform) < 3:
            return []

        # 参数
        MIN_PEAK = 0.4              # 最小峰值强度
        MIN_INTERVAL = 0.15         # 最小节拍间隔（秒），对应最快 400 BPM
        window = 4                  # 局部窗口半宽（采样点）

        # Step 1: 找局部峰值
        peaks = []
        for i in range(1, len(waveform) - 1):
            left = max(0, i - window)
            right = min(len(waveform), i + window + 1)
            if waveform[i] > MIN_PEAK and waveform[i] == max(waveform[left:right]):
                time = i / self.WAVEFORM_SAMPLE_RATE
                if not peaks or (time - peaks[-1].time) >= MIN_INTERVAL:
                    peaks.append(BeatMarker(time=time, strength=waveform[i]))

        if not peaks:
            return []

        # Step 2: 估算 BPM（取最常见间距）
        intervals = [peaks[i + 1].time - peaks[i].time for i in range(len(peaks) - 1)]
        if not intervals:
            return peaks

        # 找最集中的区间模式
        from collections import Counter
        # 四舍五入到 0.05s 精度
        rounded = [round(i * 20) / 20 for i in intervals]
        most_common_interval, _ = Counter(rounded).most_common(1)[0] if rounded else (0.5, 0)

        # Step 3: 标记 Drop 点（显著强于相邻的点）
        if len(peaks) >= 3:
            for i in range(1, len(peaks) - 1):
                prev_str = peaks[i - 1].strength
                curr_str = peaks[i].strength
                next_str = peaks[i + 1].strength
                if curr_str > prev_str * 1.3 and curr_str > next_str * 1.3:
                    peaks[i].is_drop = True

        return peaks

    def _segment_energy(
        self, waveform: list[float], duration: float
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """将音频分成能量段：高能(Drop)、中能、低能(Valley)。"""
        if len(waveform) < 5:
            return [], [], []

        HIGH_THRESHOLD = 0.65
        LOW_THRESHOLD = 0.25

        segments = []
        i = 0
        while i < len(waveform):
            level = "mid"
            val = waveform[i]
            if val >= HIGH_THRESHOLD:
                level = "high"
            elif val <= LOW_THRESHOLD:
                level = "low"

            # 扩展到连续同能级段
            j = i + 1
            while j < len(waveform):
                v = waveform[j]
                if level == "high" and v >= HIGH_THRESHOLD:
                    j += 1
                elif level == "low" and v <= LOW_THRESHOLD:
                    j += 1
                elif level == "mid" and LOW_THRESHOLD < v < HIGH_THRESHOLD:
                    j += 1
                else:
                    break

            start_time = i / self.WAVEFORM_SAMPLE_RATE
            end_time = min(j / self.WAVEFORM_SAMPLE_RATE, duration)
            avg_energy = sum(waveform[i:j]) / max(1, j - i)

            segments.append({"start": round(start_time, 2), "end": round(end_time, 2),
                           "level": level, "avg_energy": round(avg_energy, 3)})
            i = j

        # 合并相邻同类段（间隔 < 1s）
        merged = []
        for seg in segments:
            if merged and merged[-1]["level"] == seg["level"] and (seg["start"] - merged[-1]["end"]) < 1.0:
                merged[-1]["end"] = seg["end"]
                merged[-1]["avg_energy"] = (merged[-1]["avg_energy"] + seg["avg_energy"]) / 2
            else:
                merged.append(seg)

        drops = [s for s in merged if s["level"] == "high" and (s["end"] - s["start"]) >= 2.0]
        valleys = [s for s in merged if s["level"] == "low" and (s["end"] - s["start"]) >= 1.0]

        return merged, drops, valleys


# ─── 波形数据持久化 ─────────────────────────────────────────────────

def waveform_cache_path(bgm_path: str) -> Path:
    """获取波形缓存文件路径"""
    import hashlib
    h = hashlib.md5(bgm_path.encode()).hexdigest()[:12]
    cache_dir = Path("/tmp/conversational-editor/waveforms")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"wf_{h}.json"


def save_analysis(analysis: BgmAnalysis) -> Path:
    """保存分析结果到磁盘缓存"""
    p = waveform_cache_path(analysis.path)
    p.write_text(json.dumps(analysis.to_dict(), ensure_ascii=False))
    return p


def load_analysis(bgm_path: str) -> Optional[BgmAnalysis]:
    """从缓存加载分析结果"""
    p = waveform_cache_path(bgm_path)
    if p.exists():
        try:
            return BgmAnalysis.from_dict(json.loads(p.read_text()))
        except Exception:
            pass
    return None
