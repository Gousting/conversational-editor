"""LLM 配置 — 统一管理 Ollama 和 OpenAI-compatible 端点（含视觉模型）"""

import json, urllib.request, base64
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class LLMConfig:
    provider: str = "ollama"
    ollama_url: str = "http://192.168.0.104:11434"
    ollama_model: str = "qwen3.5:9b"
    openai_url: str = "https://api.siliconflow.cn/v1"
    openai_key: str = ""
    openai_model: str = "deepseek-ai/DeepSeek-V4-Pro"

    # 视觉模型配置（独立于文本模型）
    vision_provider: str = "ollama"
    vision_ollama_url: str = "http://192.168.0.104:11434"
    vision_ollama_model: str = "minicpm-v:8b"
    vision_openai_url: str = ""
    vision_openai_key: str = ""
    vision_openai_model: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "openai_url": self.openai_url,
            "openai_key": self.openai_key[:8] + "..." if self.openai_key else "",
            "openai_model": self.openai_model,
            # Vision
            "vision_provider": self.vision_provider,
            "vision_ollama_url": self.vision_ollama_url,
            "vision_ollama_model": self.vision_ollama_model,
            "vision_openai_url": self.vision_openai_url,
            "vision_openai_key": self.vision_openai_key[:8] + "..." if self.vision_openai_key else "",
            "vision_openai_model": self.vision_openai_model,
            "available": self.check_available(),
        }

    def check_available(self) -> str:
        if self.provider == "ollama":
            try:
                req = urllib.request.Request(
                    f"{self.ollama_url}/api/tags",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read())
                    return "online" if "models" in data else "offline"
            except:
                return "offline"
        else:
            if not self.openai_key:
                return "no_key"
            try:
                body = json.dumps({
                    "model": self.openai_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }).encode()
                req = urllib.request.Request(
                    f"{self.openai_url}/chat/completions",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.openai_key}",
                    },
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return "online" if resp.status < 400 else "offline"
            except:
                return "offline"


class LLMClient:
    """统一的 LLM 调用接口（含视觉）"""

    def __init__(self, config: LLMConfig):
        self.config = config

    # ── 文本生成 ──

    def generate(self, prompt: str, system: str = "",
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
        if self.config.provider == "ollama":
            return self._ollama_generate(prompt, system, temperature, max_tokens)
        else:
            return self._openai_generate(prompt, system, temperature, max_tokens)

    # ── 视觉生成 ──

    def vision_generate(self, prompt: str, image_paths: list[str],
                        temperature: float = 0.3, max_tokens: int = 1024) -> str:
        """用视觉模型分析图片"""
        if self.config.vision_provider == "ollama":
            return self._ollama_vision(
                prompt, image_paths,
                self.config.vision_ollama_url,
                self.config.vision_ollama_model,
                temperature, max_tokens,
            )
        else:
            return self._openai_vision(
                prompt, image_paths,
                temperature, max_tokens,
            )

    def _ollama_vision(self, prompt: str, image_paths: list[str],
                       url: str, model: str,
                       temperature: float, max_tokens: int) -> str:
        """Ollama 视觉 API"""
        images_b64 = []
        for p in image_paths:
            try:
                with open(p, "rb") as f:
                    images_b64.append(base64.b64encode(f.read()).decode())
            except Exception:
                continue

        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "images": images_b64,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()

        req = urllib.request.Request(
            f"{url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read()).get("response", "")

    def _openai_vision(self, prompt: str, image_paths: list[str],
                       temperature: float, max_tokens: int) -> str:
        """OpenAI 兼容视觉 API"""
        content = [{"type": "text", "text": prompt}]
        for p in image_paths:
            try:
                with open(p, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except Exception:
                continue

        body = json.dumps({
            "model": self.config.vision_openai_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        req = urllib.request.Request(
            f"{self.config.vision_openai_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.vision_openai_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]

    # ── 内部实现 ──

    def _ollama_generate(self, prompt: str, system: str,
                         temperature: float, max_tokens: int) -> str:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        body = json.dumps({
            "model": self.config.ollama_model,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(
            f"{self.config.ollama_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read()).get("response", "")

    def _openai_generate(self, prompt: str, system: str,
                         temperature: float, max_tokens: int) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.config.openai_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        req = urllib.request.Request(
            f"{self.config.openai_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.openai_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]


# 全局单例
_config = LLMConfig()
_client = LLMClient(_config)


def get_config() -> LLMConfig:
    return _config

def get_client() -> LLMClient:
    return _client

def update_config(updates: dict):
    """运行时更新配置"""
    for k, v in updates.items():
        if hasattr(_config, k):
            if k in ("openai_key", "vision_openai_key") and not v:
                continue
            setattr(_config, k, v)
    global _client
    _client = LLMClient(_config)
