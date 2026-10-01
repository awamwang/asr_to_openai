# -*- coding: utf-8 -*-
"""FastAPI 应用：将多厂商 ASR 桥接为闪电说可用的 /chat/completions 格式。"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, List, Optional

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from asr_proxy.config import AppConfig, load_config
from asr_proxy.providers import ProviderRouter


def _ensure_log_path(path: str) -> str:
    """相对路径则落到项目根目录。"""
    if os.path.isabs(path):
        return path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, path)


def _error_response(status_code: int, message: str, **extra: Any) -> JSONResponse:
    """统一错误响应：真实 HTTP 状态码，绝不伪装成 200。"""
    body: dict = {"error": {"message": message, "status_code": status_code}}
    if extra:
        body["error"].update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _extract_language(data: dict) -> Optional[str]:
    """从闪电说请求体提取 asr_options.language。"""
    opts = data.get("asr_options")
    if isinstance(opts, dict):
        lang = opts.get("language")
        if isinstance(lang, str) and lang.strip():
            return lang.strip()
    lang = data.get("language")
    if isinstance(lang, str) and lang.strip():
        return lang.strip()
    return None


def create_app(cfg: Optional[AppConfig] = None) -> FastAPI:
    """创建 FastAPI 应用。"""
    cfg = cfg or load_config()
    log_file = _ensure_log_path(cfg.log_file)

    def _log(msg: str) -> None:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    # 公共路由层统一打印：使用模型 / 失败跳过（覆盖所有服务商）
    router = ProviderRouter(cfg, log_fn=_log)

    app = FastAPI(title="ASR to OpenAI Proxy", version="1.0.0")
    app.state.cfg = cfg
    app.state.router_asr = router
    app.state.log_file = log_file

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        try:
            ct = request.headers.get("content-type")
            cl = request.headers.get("content-length")
            _log(f"[HTTP] {request.method} {request.url.path} content-type={ct} content-length={cl}")
        except Exception:
            pass
        return await call_next(request)

    @app.get("/")
    @app.get("/health")
    async def health():
        enabled = [
            {
                "name": p.name,
                "models": p.model_names,
                "default_model": p.model_id,
                "enabled": True,
            }
            for p in router.enabled_list()
        ]
        model_ids = ["auto"]
        for p in router.enabled_list():
            model_ids.extend(p.public_model_ids())
        return {
            "status": "ok",
            "enabled_providers": enabled,
            "auto_order": [p.name for p in router.enabled_list()],
            "models": model_ids,
            "last_success": {
                "provider": router._last_provider,
                "models": dict(router._last_model_by_provider),
            },
        }

    @app.get("/v1/models")
    @app.get("/models")
    async def list_models():
        models = [{"id": "auto", "object": "model", "owned_by": "asr-proxy"}]
        seen = {"auto"}
        for p in router.enabled_list():
            for mid in p.public_model_ids():
                if mid not in seen:
                    models.append({
                        "id": mid,
                        "object": "model",
                        "owned_by": p.name,
                    })
                    seen.add(mid)
            if p.name not in seen:
                models.append({
                    "id": p.name,
                    "object": "model",
                    "owned_by": p.name,
                })
                seen.add(p.name)
        return {"object": "list", "data": models}

    # ---- 标准 multipart 转写（兼容 OpenAI /v1/audio/transcriptions）----

    @app.post("/v1/audio/transcriptions")
    @app.post("/audio/transcriptions")
    async def transcribe(
        file: UploadFile,
        model: str = Form("auto"),
        language: Optional[str] = Form(None),
    ):
        audio_bytes = await file.read()
        if not audio_bytes:
            return _error_response(400, "empty audio")
        _log(f"[transcribe] file={file.filename} bytes={len(audio_bytes)} 请求模型={model!r}")
        try:
            result, errors = router.recognize(
                audio_bytes,
                model=model,
                filename=file.filename or "audio.wav",
                language=language,
            )
            _log(f"[transcribe] OK text={result.text!r}")
            return {
                "task": "transcribe",
                "language": result.language,
                "duration": result.duration,
                "text": result.text,
                "segments": result.segments or [],
                "provider": result.provider,
                "model": f"{result.provider}/{result.model}" if result.model else result.provider,
            }
        except Exception as e:
            _log(f"[transcribe] EXCEPTION {type(e).__name__}: {e}")
            return _error_response(502, f"{type(e).__name__}: {e}")

    # ---- 闪电说自定义 ASR：/chat/completions + JSON base64 ----

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat(request: Request):
        body = await request.body()
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            _log(f"[chat] body parse error: {type(e).__name__}: {e}")
            return _error_response(400, f"invalid JSON body: {type(e).__name__}: {e}")

        if not isinstance(data, dict):
            return _error_response(400, "JSON body must be an object")

        structure = _describe_structure(data)
        _log("[chat] BODY_STRUCTURE:\n" + "\n".join(structure))

        model = str(data.get("model") or "auto")
        language = _extract_language(data)
        candidates = _find_audio_b64(data)
        if not candidates:
            _log("[chat] no audio base64 found in body")
            return _error_response(400, "no audio base64 found in request body")

        last_error = ""
        for i, b64str in enumerate(candidates):
            try:
                audio_bytes = _try_decode_b64(b64str)
                if len(audio_bytes) < 100:
                    last_error = f"candidate[{i}] audio too short ({len(audio_bytes)} bytes)"
                    continue
                _log(
                    f"[chat] candidate[{i}] decoded bytes={len(audio_bytes)} "
                    f"请求模型={model!r} language={language!r}"
                )
                result, errors = router.recognize(
                    audio_bytes,
                    model=model,
                    filename="audio.wav",
                    language=language,
                )
                if result.text:
                    used = (
                        f"{result.provider}/{result.model}"
                        if result.model else result.provider
                    )
                    _log(f"[chat] OK text={result.text!r}")
                    # 与阿里云成功路径一致：闪电说只读 choices[0].message.content
                    return {
                        "choices": [{"message": {"content": result.text}}],
                        "model": used,
                        "provider": result.provider,
                    }
                last_error = f"candidate[{i}] empty text; failover={errors}"
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                _log(f"[chat] candidate[{i}] error: {last_error}")
                continue

        _log(f"[chat] recognition failed: {last_error}")
        return _error_response(
            502,
            last_error or "recognition produced no text",
            hint="检查模型名（groq/... / aliyun/... / doubao/... / siliconflow/... / auto）、api_key，以及 groq.proxy 是否可达",
        )

    return app


def _try_decode_b64(s: str) -> bytes:
    """解码 base64，兼容 data:audio/...;base64, 前缀。"""
    if "," in s[:40]:
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _describe_structure(data: Any, prefix: str = "", depth: int = 0, out: Optional[List[str]] = None) -> List[str]:
    """递归描述 JSON 结构，长字符串只记录长度。"""
    if out is None:
        out = []
    indent = "  " * depth
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                out.append(f"{indent}{prefix}{k}: {type(v).__name__}")
                _describe_structure(v, prefix + k + ".", depth + 1, out)
            elif isinstance(v, str) and len(v) > 2000:
                out.append(f"{indent}{prefix}{k}: str(len={len(v)}) <可能为 base64 音频>")
            else:
                out.append(f"{indent}{prefix}{k}: {type(v).__name__}")
    elif isinstance(data, list):
        for i, v in enumerate(data[:5]):
            if isinstance(v, (dict, list)):
                out.append(f"{indent}{prefix}[{i}]: {type(v).__name__}")
                _describe_structure(v, prefix + f"[{i}]" + ".", depth + 1, out)
            elif isinstance(v, str) and len(v) > 2000:
                out.append(f"{indent}{prefix}[{i}]: str(len={len(v)}) <可能为 base64 音频>")
            else:
                out.append(f"{indent}{prefix}[{i}]: {type(v).__name__}")
    return out


def _find_audio_b64(data: Any, out: Optional[List[str]] = None) -> List[str]:
    """递归查找可能是 base64 音频的长字符串。"""
    if out is None:
        out = []
    if isinstance(data, dict):
        # 优先识别明确的 input_audio.data 字段（即使较短）
        if "input_audio" in data and isinstance(data["input_audio"], dict):
            d = data["input_audio"].get("data")
            if isinstance(d, str) and len(d) > 100:
                out.append(d)
        for v in data.values():
            _find_audio_b64(v, out)
    elif isinstance(data, list):
        for v in data:
            _find_audio_b64(v, out)
    elif isinstance(data, str) and len(data) > 2000:
        if data not in out:
            out.append(data)
    return out
