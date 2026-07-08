"""集成自测 — 完整对话流程"""
import json, urllib.request, asyncio, websockets, sys, os

BASE = "http://localhost:8765"
WS = "ws://localhost:8765"
passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")

def api_post(path, body):
    req = urllib.request.Request(f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# ─── Test 1: Load video ───
print("\n📹 测试1: 加载视频")
try:
    resp = api_post("/api/load-video", {
        "filepath": "/tmp/conversational-editor/test_video.mp4"
    })
    test("API返回success", resp.get("success"))
    SID = resp["session_id"]
    test("有session_id", bool(SID), f"got: {SID}")
    test("有source_id", bool(resp.get("source_id")))
    test("duration=30s", resp.get("duration") == 30.0)
except Exception as e:
    print(f"  ❌ 加载视频失败: {e}")
    failed += 1
    sys.exit(1)

# ─── Test 2: WebSocket conversation ───
print(f"\n💬 测试2: 对话流程 (session={SID})")
async def ws_test():
    global passed, failed
    async with websockets.connect(f"{WS}/ws/{SID}") as ws:
        # Wait for session_ready
        msg = json.loads(await ws.recv())
        test("收到session_ready", msg["type"] == "session_ready")

        # 2a: 添加片段 "从5秒到10秒"
        await ws.send(json.dumps({"text": "从 5 到 10 提取"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("add_clip成功", r["success"] and r["clip_count"] == 1,
             f"msg: {r.get('message')}")

        # 2b: 添加第2段
        await ws.send(json.dumps({"text": "再接上 20 到 25"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("第2段添加成功", r["success"] and r["clip_count"] == 2,
             f"clips: {r['clip_count']}")

        # 2c: 修改速度
        await ws.send(json.dumps({"text": "第一段放慢 0.5 倍"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("修改速度成功", r["success"],
             f"msg: {r.get('message')}")

        # 2d: 添加过渡
        await ws.send(json.dumps({"text": "中间加闪白 0.3 秒"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("过渡添加成功", r["success"],
             f"msg: {r.get('message')}")

        # 2e: 撤销
        await ws.send(json.dumps({"text": "撤销"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("撤销成功", r["success"],
             f"msg: {r.get('message')}")

        # 2f: 重做
        await ws.send(json.dumps({"text": "重做"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("重做成功", r["success"])

        # 2g: 保存
        await ws.send(json.dumps({"text": "保存"}))
        msg = json.loads(await ws.recv())  # user_message
        msg = json.loads(await ws.recv())  # edit_result
        r = msg["result"]
        test("保存成功", r["success"])

        # 2h: 删除最后一个
        await ws.send(json.dumps({"text": "删掉最后一个"}))
        msg = json.loads(await ws.recv())
        msg = json.loads(await ws.recv())
        r = msg["result"]
        test("删除成功", r["success"])

        # 2i: 渲染预览
        await ws.send(json.dumps({"text": "渲染预览"}))
        msgs = []
        for _ in range(4):  # user_message + edit_result + preview_ready
            msgs.append(json.loads(await ws.recv()))
        preview_msg = next((m for m in msgs if m["type"] == "preview_ready"), None)
        test("渲染预览完成", preview_msg is not None,
             f"path: {preview_msg.get('path') if preview_msg else 'N/A'}")
        if preview_msg:
            preview_path = preview_msg["path"]
            test("预览文件存在", os.path.exists(preview_path),
                 f"size: {os.path.getsize(preview_path)} bytes")

asyncio.run(ws_test())

print(f"\n{'='*40}")
print(f"结果: {passed} 通过, {failed} 失败, {passed+failed} 总计")
if failed == 0:
    print("🎉 全部通过!")
else:
    print(f"⚠️ {failed} 项未通过")
