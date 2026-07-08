"""模型列表获取 — 从 Ollama / OpenAI 端点拉取可用模型"""

import json, urllib.request
from .llm_config import get_config


def fetch_models() -> dict:
    """获取当前 provider 的可用模型列表"""
    cfg = get_config()
    try:
        if cfg.provider == "ollama":
            return _ollama_models(cfg.ollama_url)
        else:
            return _openai_models(cfg.openai_url, cfg.openai_key)
    except Exception as e:
        return {"success": False, "error": str(e), "models": []}


def fetch_vision_models() -> dict:
    """获取视觉模型的可用模型列表"""
    cfg = get_config()
    try:
        if cfg.vision_provider == "ollama":
            return _ollama_models(cfg.vision_ollama_url)
        else:
            return _openai_models(cfg.vision_openai_url, cfg.vision_openai_key)
    except Exception as e:
        return {"success": False, "error": str(e), "models": []}


def _ollama_models(url: str) -> dict:
    req = urllib.request.Request(
        f"{url}/api/tags",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        return {"success": True, "models": models}


def _openai_models(url: str, key: str) -> dict:
    req = urllib.request.Request(
        f"{url}/models",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        # 过滤 chat 模型
        models = [
            m["id"] for m in data.get("data", [])
            if any(kw in m.get("id", "") for kw in ("chat", "instruct", "deepseek", "qwen", "llama", "gpt", "claude"))
        ]
        if not models:  # 没过滤到就全返
            models = [m["id"] for m in data.get("data", [])]
        return {"success": True, "models": models}
