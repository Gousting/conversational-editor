"""FastAPI 主入口 — REST API + WebSocket"""

import json
import os
import yaml
import shutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .session import SessionManager
from .nlu import NLUParser
from .schemas import LoadVideoRequest, LoadVideoResponse
from .llm_config import get_config, update_config
from .model_fetch import fetch_models, fetch_vision_models
from .planner import EditPlanner
from .skills_manager import SkillManager
from .reference_analyzer import ReferenceAnalyzer
from engine.pipeline import PipelineEngine

app = FastAPI(title="对话式视频剪辑工作台")

# 挂载前端静态文件
WEB_DIR = Path(__file__).parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# 服务层
session_manager = SessionManager()
nlu = NLUParser()
skill_manager = SkillManager()  # 全局技能管理器，跨 session 共享
ref_analyzer = ReferenceAnalyzer()
pipeline_engine = PipelineEngine()  # 管道渲染引擎


@app.get("/")
async def root():
    """返回前端页面"""
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "对话式视频剪辑工作台 API", "docs": "/docs"}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """上传视频文件 — 拖入/选择文件的入口"""
    upload_dir = Path("/tmp/conversational-editor/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 保留原文件名，追加 session 前缀防止冲突
    safe_name = file.filename or "video.mp4"
    dest = upload_dir / safe_name

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 调用 load-video 逻辑
    session = session_manager.create_session()
    try:
        info = session.load_video(str(dest))
        return {
            "success": True,
            "session_id": session.id,
            "source_id": info["id"],
            "filename": info["filename"],
            "duration": info["duration"],
            "fps": info["fps"],
            "width": info["width"],
            "height": info["height"],
            "analysis": info.get("analysis"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/load-video")
async def load_video(req: LoadVideoRequest):
    """加载视频文件"""
    if not os.path.exists(req.filepath):
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.filepath}")

    session = session_manager.create_session()
    try:
        info = session.load_video(req.filepath)
        return {
            "success": True,
            "session_id": session.id,
            "source_id": info["id"],
            "filename": info["filename"],
            "duration": info["duration"],
            "fps": info["fps"],
            "width": info["width"],
            "height": info["height"],
            "analysis": info.get("analysis"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_llm_config():
    """获取 LLM 配置"""
    return get_config().to_dict()

@app.post("/api/config")
async def update_llm_config(data: dict):
    """更新 LLM 配置"""
    update_config(data)
    cfg = get_config()
    vision_cfg = {
        "vision_provider": cfg.vision_provider,
        "vision_ollama_url": cfg.vision_ollama_url,
        "vision_ollama_model": cfg.vision_ollama_model,
        "vision_openai_url": cfg.vision_openai_url,
        "vision_openai_key": cfg.vision_openai_key[:8] + "..." if cfg.vision_openai_key else "",
        "vision_openai_model": cfg.vision_openai_model,
    }
    result = cfg.to_dict()
    result.update(vision_cfg)
    return {"success": True, "config": result}

@app.get("/api/models")
async def list_models():
    """获取当前 provider 的模型列表"""
    return fetch_models()

@app.get("/api/vision-models")
async def list_vision_models():
    """获取视觉模型的模型列表"""
    return fetch_vision_models()

@app.get("/api/media/{session_id}")
async def stream_media(session_id: str):
    """流式传输源视频"""
    from fastapi.responses import FileResponse
    session = session_manager.get_session(session_id)
    if not session or not session.source_path:
        raise HTTPException(status_code=404, detail="无媒体")
    return FileResponse(session.source_path, media_type="video/mp4")


@app.get("/api/render-output/{session_id}")
async def serve_render_output(session_id: str):
    """流式传输最新渲染产物"""
    from fastapi.responses import FileResponse
    session = session_manager.get_session(session_id)
    if not session or not session.preview_path:
        raise HTTPException(status_code=404, detail="无渲染产物")
    path = session.preview_path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="渲染文件不存在")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/sessions")
async def list_sessions():
    """列出所有已保存的 session"""
    import json
    sessions = []
    storage = Path("/tmp/conversational-editor/sessions")
    if not storage.exists():
        return {"success": True, "sessions": []}
    for f in sorted(storage.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "id": data.get("id", f.stem),
                "filename": Path(data.get("source_path", "")).name or "未命名",
                "clips": len(data.get("timeline", [])),
                "markers": len(data.get("markers", [])),
            })
        except:
            pass
    return {"success": True, "sessions": sessions[:20]}


@app.get("/api/session/{session_id}")
async def get_session_info(session_id: str):
    """获取 session 信息（用于 ?session= URL 参数自动连接）"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return {
        "success": True,
        "session_id": session.id,
        "filename": session.media_store.get_source(session.current_source_id).filename if session.current_source_id else "",
        "duration": session.media_store.get_source(session.current_source_id).duration if session.current_source_id else 0,
    }


@app.get("/api/thumbnails/{session_id}")
async def get_thumbnails(session_id: str, count: int = 10):
    """获取视频缩略图条带"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")

    try:
        paths = session.media_store.get_thumbnails_strip(
            session.source_path, count)
        return {"paths": paths}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/markers/{session_id}")
async def update_markers(session_id: str, data: dict):
    """同步标记数据"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    session.markers = data.get("markers", [])
    session_manager._save_session(session)
    return {"success": True, "count": len(session.markers)}






# ─── Reference Video Analysis ───

@app.post("/api/analyze-reference")
async def analyze_reference(data: dict):
    """分析参考视频的编辑风格"""
    filepath = data.get("filepath", "")
    use_vision = data.get("use_vision", False)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        fp = ref_analyzer.analyze(filepath, use_vision=use_vision)
        return {
            "success": True,
            "fingerprint": fp.to_dict(),
            "summary": fp.summary(),
            "rhythm_pattern": " ".join(fp.rhythm_pattern[:30]),
            "style_description": fp.style_description,
            "keyframe_count": len(fp.keyframe_paths),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/replicate-style")
async def replicate_style(data: dict):
    """用参考视频风格复刻到源素材上"""
    ref_path = data.get("reference_path", "")
    session_id = data.get("session_id", "")

    if not ref_path or not os.path.exists(ref_path):
        raise HTTPException(status_code=404, detail="参考视频不存在")

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")

    try:
        # 分析参考视频
        fp = ref_analyzer.analyze(ref_path)

        # 获取源素材时长
        source = session.media_store.get_source(session.current_source_id)
        source_dur = source.duration if source else 260

        # 生成建议片段
        clips = fp.to_clip_template(source_dur)

        # 注入到 proposedClips
        return {
            "success": True,
            "reference": fp.to_dict(),
            "summary": fp.summary(),
            "rhythm": " ".join(fp.rhythm_pattern[:30]),
            "proposed_clips": clips,
            "clip_count": len(clips),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Skills API ───

@app.get("/api/skills")
async def list_skills():
    """列出所有技能"""
    skills = skill_manager.list_skills()
    return {"success": True, "skills": skills, "count": len(skills)}

@app.post("/api/skills/reload")
async def reload_skills():
    """重新加载所有技能"""
    skill_manager.reload()
    return {"success": True, "count": len(skill_manager.skills)}

@app.post("/api/skills/{name}")
async def create_or_update_skill(name: str, data: dict):
    """创建或更新技能"""
    skill = skill_manager.create_or_update(
        name=name,
        description=data.get("description", ""),
        triggers=data.get("triggers", []),
        always=data.get("always", False),
        body=data.get("body", ""),
    )
    if skill:
        return {"success": True, "skill": {"name": skill.name, "description": skill.description}}
    return {"success": False, "error": "创建失败"}

@app.delete("/api/skills/{name}")
async def delete_skill(name: str):
    """删除技能"""
    ok = skill_manager.delete(name)
    return {"success": ok}


# ═══════════════════════════════════════════
# Render & Export
# ═══════════════════════════════════════════

@app.post("/api/render/{session_id}")
async def trigger_render(session_id: str, data: dict = None):
    """触发管道渲染 — 多阶段生产流水线"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    if not session.timeline.items:
        raise HTTPException(status_code=400, detail="时间轴为空")
    if session.render_state == "rendering":
        raise HTTPException(status_code=409, detail="已有渲染任务进行中")

    session.render_state = "rendering"
    session.render_progress = 0
    session.render_cancelled = False
    pipeline_name = (data or {}).get("pipeline", "game-highlight")
    session.current_pipeline = pipeline_name

    import threading

    def pipeline_thread():
        try:
            def on_stage_start(name, idx, total):
                session.render_progress = (idx / max(total, 1)) * 90
                session.current_stage = name

            def on_stage_complete(result, idx, total):
                pct = ((idx + 1) / max(total, 1)) * 90
                session.render_progress = min(pct, 95)
                session.last_stage_result = result.to_dict()

            result = pipeline_engine.run(
                pipeline_name=pipeline_name,
                session=session,
                callbacks={
                    "on_stage_start": on_stage_start,
                    "on_stage_complete": on_stage_complete,
                },
            )
            if result["success"]:
                session.render_state = "done"
                session.render_progress = 100
                session.preview_path = result.get("output_path", "")
                session.pipeline_result = result
            else:
                session.render_state = "error"
                session.render_error = result
        except Exception:
            if session.render_state == "rendering":
                session.render_state = "error"
        finally:
            session_manager._save_session(session)

    threading.Thread(target=pipeline_thread, daemon=True).start()
    return {"success": True, "message": "管道渲染已启动", "pipeline": pipeline_name}

@app.get("/api/render/status/{session_id}")
async def render_status(session_id: str):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return {
        "state": session.render_state,
        "progress": session.render_progress,
        "current_stage": getattr(session, "current_stage", ""),
        "last_stage_result": getattr(session, "last_stage_result", None),
        "preview_url": f"/api/render-output/{session_id}" if session.preview_path else "",
    }

@app.get("/api/pipelines")
async def list_pipelines():
    """列出可用管道"""
    pipes = []
    pipe_dir = Path("/home/shrine/conversational-editor/pipelines")
    if pipe_dir.exists():
        for f in sorted(pipe_dir.glob("*.yaml")):
            try:
                with open(f) as fp:
                    p = yaml.safe_load(fp)
                stages = [s["name"] for s in p.get("stages", [])]
                pipes.append({
                    "name": f.stem,
                    "description": p.get("description", ""),
                    "stages": stages,
                    "version": p.get("version", "0.1"),
                })
            except Exception:
                pass
    return {"success": True, "pipelines": pipes}

@app.post("/api/render/cancel/{session_id}")
async def cancel_render(session_id: str):
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    session.render_cancelled = True
    session.renderer.cancel_render()
    return {"success": True}

@app.post("/api/export/{session_id}")
async def export_video(session_id: str, data: dict = None):
    from fastapi.responses import FileResponse
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session 不存在")
    if not session.timeline.items:
        raise HTTPException(status_code=400, detail="时间轴为空")

    preset = (data or {}).get("preset", "original")
    presets = {
        "douyin": "1080:1920",
        "bilibili": "1920:1080",
        "square": "1080:1080",
        "original": "",
    }
    size = presets.get(preset, "")
    export_path = session.renderer.media_dir / f"export_{session.id}_{preset}.mp4"

    sources = {session.current_source_id: session.source_path}
    concat_file = session.renderer._build_concat_script(session.timeline, sources)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
    ]
    if size:
        cmd += ["-vf", f"scale={size}:force_original_aspect_ratio=decrease,pad={size}:(ow-iw)/2:(oh-ih)/2"]
    cmd.append(str(export_path))

    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"导出失败: {result.stderr[-200:]}")

    filename = f"{session.project_name or 'export'}_{preset}.mp4"
    return FileResponse(str(export_path), media_type="video/mp4", filename=filename)

@app.get("/api/skills/match")
async def match_skills(intent: str = ""):
    """根据意图匹配技能"""
    matched = skill_manager.match(intent)
    return {
        "success": True,
        "intent": intent,
        "matched": [s.name for s in matched],
        "context": skill_manager.get_context(intent),
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    """WebSocket 对话接口"""
    await ws.accept()

    session = session_manager.get_session(session_id)
    if not session:
        await ws.send_json({"type": "error", "message": "Session 不存在"})
        await ws.close()
        return

    # 发送初始状态
    await ws.send_json({
        "type": "session_ready",
        "session_id": session.id,
        "filename": session.media_store.get_source(session.current_source_id).filename
            if session.current_source_id else "",
    })

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            user_input = msg.get("text", "").strip()

            # 处理标记同步
            if "markers" in msg:
                session.markers = msg["markers"]

            if not user_input:
                continue

            # 回显用户消息
            await ws.send_json({"type": "user_message", "text": user_input})

            # NLU 解析
            context = session.get_context()
            action = nlu.parse(user_input, context)

            # 执行操作
            result = session.execute_action(action)

            # 返回结果
            await ws.send_json({
                "type": "edit_result",
                "action": action.get("action", "unknown"),
                "result": result,
            })

            # 如果是渲染，发送预览路径
            if result.get("preview_path"):
                await ws.send_json({
                    "type": "preview_ready",
                    "path": result["preview_path"],
                })

            # 自动持久化
            session_manager._save_session(session)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await ws.send_json({"type": "error", "message": str(e)})
