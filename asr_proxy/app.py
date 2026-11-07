# -*- coding: utf-8 -*-
"""FastAPI 应用：将多厂商 ASR 桥接为闪电说可用的 /chat/completions 格式。"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import re
import uuid
from typing import Any, List, Optional, Tuple

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from asr_proxy.config import AppConfig, load_config
from asr_proxy.logutil import setup_logging
from asr_proxy.providers import ProviderRouter
from asr_proxy.strategy import STRATEGY_PUBLIC_IDS

# 音频文件头：用于剔除「长字符串但不是音频」的假候选。
# 不靠提高长度下限来过滤，避免误伤极短音频。
_AUDIO_MAGIC = (
    b"RIFF",               # WAV
    b"RF64",               # WAV64
    b"ID3",                # MP3（带标签）
    b"OggS",               # Ogg / Opus
    b"fLaC",               # FLAC
    b"#!AMR",              # AMR
    b"\x1a\x45\xdf\xa3",  # WebM / Matroska
)
_MIN_AUDIO_BYTES = 100  # 沿用原有下限，不再调高


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


def _safe_filename(name: str) -> str:
    """只保留 basename 与安全字符，防日志注入与临时文件名污染。"""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:128]
    return base or "audio.wav"


def looks_like_audio(data: bytes) -> bool:
    """按文件头判断是否为可识别音频；裸 PCM 由明确的 input_audio.data 字段兜底。"""
    if not data or len(data) < 12:
        return False
    head = data[:16]
    if head.startswith(_AUDIO_MAGIC):
        return data[:4] != b"RIFF" or data[8:12] == b"WAVE"
    if head[4:8] == b"ftyp":                            # M4A / MP4
        return True
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:   # MP3 帧同步
        return True
    return False


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


def _find_explicit_audio_data(data: Any, out: Optional[List[str]] = None) -> List[str]:
    """只收集语义明确的 input_audio.data（即使无音频头也可信）。"""
    if out is None:
        out = []
    if isinstance(data, dict):
        ia = data.get("input_audio")
        if isinstance(ia, dict) and isinstance(ia.get("data"), str):
            out.append(ia["data"])
        for v in data.values():
            _find_explicit_audio_data(v, out)
    elif isinstance(data, list):
        for v in data:
            _find_explicit_audio_data(v, out)
    return out


class _BadRequest(Exception):
    """请求体解析/校验失败，统一转 400。"""


def _client_error(raw: str, cfg: AppConfig, request_id: str) -> str:
    """默认脱敏：对外只回摘要 + request_id；完整错误只进日志。

    设 ASR_EXPOSE_ERRORS=1 或 security.expose_errors=true 可临时恢复明文排障。
    """
    if cfg.expose_errors:
        return raw
    return (
        f"识别失败，完整错误已写入日志（request_id={request_id}；"
        "排障可临时设置 ASR_EXPOSE_ERRORS=1）"
    )


def _prepare_chat_request(
    raw: bytes,
    cfg: AppConfig,
) -> Tuple[dict, str, Optional[str], List[bytes]]:
    """同步解析：JSON / 候选查找 / base64 解码 / 音频类型检测。

    整体放进线程池，避免大 body 的解析与解码阻塞 event loop。
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise _BadRequest(f"invalid JSON body: {type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise _BadRequest("JSON body must be an object")

    model = str(data.get("model") or "auto")
    language = _extract_language(data)
    candidates = _find_audio_b64(data)
    if not candidates:
        raise _BadRequest("no audio base64 found in request body")

    # 明确字段优先信任；兜底扫描到的长字符串必须通过音频头检测，
    # 否则纯文本（如超长 prompt）会被当成音频送去上游，放大费用。
    explicit = set(_find_explicit_audio_data(data))
    cap = int(cfg.max_audio_candidates or 0)
    audios: List[bytes] = []
    for s in candidates:
        if cap > 0 and len(audios) >= cap:
            break
        try:
            decoded = _try_decode_b64(s)
        except (binascii.Error, ValueError):
            continue
        if len(decoded) < _MIN_AUDIO_BYTES:
            continue
        if not looks_like_audio(decoded) and s not in explicit:
            continue
        audios.append(decoded)
    return data, model, language, audios


def create_app(cfg: Optional[AppConfig] = None) -> FastAPI:
    """创建 FastAPI 应用。"""
    cfg = cfg or load_config()
    log_file = _ensure_log_path(cfg.log_file)
    logger = setup_logging(
        log_file,
        level=cfg.log_level,
        backup_days=cfg.log_backup_days,
    )
    router = ProviderRouter(cfg, log_fn=logger)
    logger.info(
        "text: clean_punctuation=%s smart_sentence=%s",
        cfg.clean_punctuation, cfg.smart_sentence,
    )
    if "tencent" in cfg.providers:
        logger.info("text→tencent.filter_punc=%s", cfg.providers["tencent"].filter_punc)
    if "doubao" in cfg.providers:
        logger.info("text→doubao.enable_punc=%s", cfg.providers["doubao"].enable_punc)
    if cfg.smart_sentence:
        smart = router.list_smart_sentence_models()
        if smart:
            logger.info("smart_sentence 可用模型: %s", smart)
        else:
            logger.warning(
                "smart_sentence=true 但当前无可用模型"
                "（需阿里云 streaming/realtime，如 qwen-audio-3.1-asr-flash-streaming）"
            )

    app = FastAPI(
        title="ASR to OpenAI Proxy",
        version="1.0.0",
        docs_url="/docs" if cfg.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if cfg.enable_docs else None,
    )
    app.state.cfg = cfg
    app.state.router_asr = router
    app.state.log_file = log_file
    app.state.log_level = cfg.log_level

    @app.middleware("http")
    async def guard_requests(request: Request, call_next):
        # 请求体上限（Content-Length 预检，覆盖绝大多数客户端）
        max_bytes = int(cfg.max_body_bytes or 0)
        if max_bytes > 0:
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                return _error_response(
                    413, f"request body too large (limit={max_bytes} bytes)"
                )
        # 可选鉴权：只有配置了 token 才校验，默认保持本地零改动；
        # /health 与 / 放行，便于健康检查。
        token = (cfg.auth_token or "").strip()
        if token and request.url.path not in ("/", "/health"):
            auth = request.headers.get("authorization") or ""
            if not hmac.compare_digest(auth, "Bearer " + token):
                return _error_response(401, "unauthorized")
        try:
            logger.debug(
                "[HTTP] %s %s content-type=%s content-length=%s",
                request.method, request.url.path,
                request.headers.get("content-type"),
                request.headers.get("content-length"),
            )
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
        return {
            "status": "ok",
            "strategy": "auto",
            "strategy_models": list(STRATEGY_PUBLIC_IDS),
            "config_strategy": cfg.strategy,
            "enabled_providers": enabled,
            "auto_order": [p.name for p in router.enabled_list()],
            "free_order": list(cfg.free_order),
            "cost_order": list(cfg.cost_order),
            "speed_order": list(cfg.speed_order),
            "accuracy_order": list(cfg.accuracy_order),
            "log_level": cfg.log_level,
            "log_backup_days": cfg.log_backup_days,
            "models": cfg.usable_model_ids(),
            "last_success": {
                "provider": router._last_provider,
                "models": dict(router._last_model_by_provider),
            },
        }

    @app.get("/v1/models")
    @app.get("/models")
    async def list_models():
        models = []
        seen = set()
        for mid in cfg.usable_model_ids():
            if mid in seen:
                continue
            owned = (
                "asr-proxy"
                if mid == "auto" or mid.startswith("strategy-")
                else mid.split("/", 1)[0]
            )
            models.append({"id": mid, "object": "model", "owned_by": owned})
            seen.add(mid)
        return {"object": "list", "data": models}

    # ---- 标准 multipart 转写（兼容 OpenAI /v1/audio/transcriptions）----

    @app.post("/v1/audio/transcriptions")
    @app.post("/audio/transcriptions")
    async def transcribe(
        file: UploadFile,
        model: str = Form("auto"),
        language: Optional[str] = Form(None),
    ):
        rid = uuid.uuid4().hex[:12]
        audio_bytes = await file.read()
        if not audio_bytes:
            return _error_response(400, "empty audio")
        max_bytes = int(cfg.max_body_bytes or 0)
        if max_bytes > 0 and len(audio_bytes) > max_bytes:
            return _error_response(413, f"request body too large (limit={max_bytes} bytes)")
        filename = _safe_filename(file.filename or "audio.wav")
        logger.info(
            "[transcribe] file=%r bytes=%d 请求模型=%r",
            filename, len(audio_bytes), model,
        )
        try:
            result, errors = await run_in_threadpool(
                router.recognize,
                audio_bytes,
                model=model,
                filename=filename,
                language=language,
            )
            logger.info(
                "[transcribe] OK request_id=%s len(text)=%d text=%r",
                rid, len(result.text),
                result.text if cfg.log_asr_text else "<hidden>",
            )
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
            raw = f"{type(e).__name__}: {e}"
            logger.error("[transcribe] EXCEPTION request_id=%s %s", rid, raw)
            return _error_response(502, _client_error(raw, cfg, rid), request_id=rid)

    # ---- 闪电说自定义 ASR：/chat/completions + JSON base64 ----

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat(request: Request):
        rid = uuid.uuid4().hex[:12]
        body = await request.body()
        max_bytes = int(cfg.max_body_bytes or 0)
        if max_bytes > 0 and len(body) > max_bytes:
            return _error_response(413, f"request body too large (limit={max_bytes} bytes)")
        try:
            data, model, language, audios = await run_in_threadpool(
                _prepare_chat_request, body, cfg,
            )
        except _BadRequest as e:
            logger.error("[chat] bad request request_id=%s %s", rid, e)
            return _error_response(400, str(e))

        # 结构描述只在 debug 开启时计算，避免每个请求都遍历整棵 JSON
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[chat] BODY_STRUCTURE:\n%s",
                "\n".join(_describe_structure(data)),
            )

        if not audios:
            logger.warning("[chat] no valid audio found request_id=%s", rid)
            return _error_response(400, "no valid audio found in request body")

        last_error = ""
        for i, audio_bytes in enumerate(audios):
            try:
                logger.info(
                    "[chat] candidate[%d] decoded bytes=%d 请求模型=%r language=%r",
                    i, len(audio_bytes), model, language,
                )
                result, errors = await run_in_threadpool(
                    router.recognize,
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
                    logger.info(
                        "[chat] OK request_id=%s len(text)=%d text=%r",
                        rid, len(result.text),
                        result.text if cfg.log_asr_text else "<hidden>",
                    )
                    return {
                        "choices": [{"message": {"content": result.text}}],
                        "model": used,
                        "provider": result.provider,
                    }
                last_error = f"candidate[{i}] empty text; failover={errors}"
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "[chat] candidate[%d] error request_id=%s: %s", i, rid, last_error,
                )
                continue

        logger.error("[chat] recognition failed request_id=%s: %s", rid, last_error)
        return _error_response(
            502,
            _client_error(last_error or "recognition produced no text", cfg, rid),
            request_id=rid,
            hint="检查模型名（groq/... / aliyun/... / stepfun/... / tencent/... / baidu/... / auto / strategy-*）、密钥，以及 groq.proxy 是否可达",
        )

    return app


def _try_decode_b64(s: str) -> bytes:
    """解码 base64，兼容 data:audio/...;base64, 前缀；严格校验非法字符。"""
    if "," in s[:40]:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        # 兼容带折行的 base64；清理空白后仍严格校验字符
        return base64.b64decode(re.sub(r"\s+", "", s), validate=True)


def _find_audio_b64(
    data: Any,
    out: Optional[List[str]] = None,
    _seen: Optional[set] = None,
) -> List[str]:
    """递归查找可能是 base64 音频的长字符串（保持宽松兼容）。

    用 (长度, hash) 去重，避免对超长字符串反复做 O(n) 的 `in` 比较。
    """
    if out is None:
        out = []
    if _seen is None:
        _seen = set()
        for existing in out:
            _seen.add((len(existing), hash(existing)))
    if isinstance(data, dict):
        ia = data.get("input_audio")
        if isinstance(ia, dict):
            d = ia.get("data")
            if isinstance(d, str) and len(d) > 100:
                key = (len(d), hash(d))
                if key not in _seen:
                    _seen.add(key)
                    out.append(d)
        for v in data.values():
            _find_audio_b64(v, out, _seen)
    elif isinstance(data, list):
        for v in data:
            _find_audio_b64(v, out, _seen)
    elif isinstance(data, str) and len(data) > 2000:
        key = (len(data), hash(data))
        if key not in _seen:
            _seen.add(key)
            out.append(data)
    return out


def _describe_structure(
    data: Any,
    prefix: str = "",
    depth: int = 0,
    out: Optional[List[str]] = None,
) -> List[str]:
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
