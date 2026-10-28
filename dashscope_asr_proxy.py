# -*- coding: utf-8 -*-
"""
阿里云百炼 DashScope ASR 本地代理（qwen-audio-3.1-asr-flash-streaming）
=====================================================================
把闪电说【自定义语音识别服务商】发出的标准 OpenAI /v1/audio/transcriptions 请求，
桥接到阿里云百炼 DashScope 的流式 ASR 模型（使用 SDK 的非流式 call() 识别本地文件，
同步返回完整结果，适配闪电说自定义面板的非流式文件上传）。

识别前会用本机 ffmpeg 把任意上传音频统一转成 16kHz 单声道 PCM WAV，
确保 format / sample_rate 参数与音频真实属性严格匹配（这是识别成功的关键）。

用法：
  1. 安装依赖：pip install dashscope
  2. 配置环境变量：
       DASHSCOPE_API_KEY      = 阿里云百炼 API Key（sk- 开头）
       DASHSCOPE_WORKSPACE_ID = 百炼工作空间 ID（北京区域，形如 ws-xxxx）
  3. 启动：    python dashscope_asr_proxy.py
  4. 闪电说面板填写：
       - 地址：  http://127.0.0.1:8004/v1
       - API Key：随便填任意字符串（本地服务无鉴权，真实鉴权走 DASHSCOPE_API_KEY）
       - 模型：  qwen-audio-3.1-asr-flash-streaming
"""
import os
import json
import tempfile
import subprocess
import base64
import uvicorn
from fastapi import FastAPI, UploadFile, Form, Request

app = FastAPI()


@app.middleware("http")
async def log_requests(request, call_next):
    """记录每个请求的 path / content-type / body 长度，用于抓闪电说的真实请求格式。"""
    try:
        ct = request.headers.get("content-type")
        cl = request.headers.get("content-length")
        _log(f"[HTTP] {request.method} {request.url.path} content-type={ct} content-length={cl}")
    except Exception:
        pass
    return await call_next(request)

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
WORKSPACE_ID = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
MODEL = os.environ.get("DASHSCOPE_ASR_MODEL", "qwen-audio-3.1-asr-flash-streaming")
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
LOG_FILE = os.environ.get("DASHSCOPE_LOG",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "dashscope_proxy.log"))


def _log(msg: str):
    """把请求与识别情况写入日志文件，便于定位闪电说到底发了什么。"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _configure_dashscope():
    """初始化 dashscope：设置 API Key，并按 WorkspaceId 配置北京区域 WebSocket 地址。"""
    import dashscope
    dashscope.api_key = API_KEY
    if WORKSPACE_ID:
        dashscope.base_websocket_api_url = (
            f"wss://{WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        )
    return dashscope


def _to_wav_16k_mono(src_path: str) -> str:
    """用 ffmpeg 把任意音频转成 16kHz 单声道 PCM WAV，返回新临时文件路径。"""
    dst_path = src_path + "_16k.wav"
    cmd = [FFMPEG, "-y", "-i", src_path,
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {proc.stderr[-500:]}")
    return dst_path


# 真实转写接口：闪电说正式音频转写调用这里（兼容带/不带 /v1 两种地址写法）
@app.post("/v1/audio/transcriptions")
@app.post("/audio/transcriptions")
async def transcribe(file: UploadFile, model: str = Form("qwen-audio-3.1-asr-flash-streaming")):
    if not API_KEY:
        return {"error": {"message": "DASHSCOPE_API_KEY 未设置", "status_code": 500}}

    tmp_path = None
    try:
        _log(f"[transcribe] REQUEST file={file.filename} size={0} model={model}")
        audio_bytes = await file.read()
        if not audio_bytes:
            _log("[transcribe] empty audio")
            return {"error": {"message": "empty audio", "status_code": 400}}
        _log(f"[transcribe] audio bytes={len(audio_bytes)} suffix="
             f"{os.path.splitext(file.filename or '')[1] or '.wav'}")

        suffix = os.path.splitext(file.filename or "")[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)

        # 统一转成 16k 单声道 wav，保证 format/sample_rate 匹配
        wav_path = _to_wav_16k_mono(tmp_path)
        _log(f"[transcribe] ffmpeg converted to {wav_path}")

        from http import HTTPStatus
        from dashscope.audio.asr import Recognition
        _configure_dashscope()

        recognition = Recognition(model=MODEL, format="wav",
                                   sample_rate=16000, callback=None)
        result = recognition.call(wav_path)

        if result.status_code == HTTPStatus.OK:
            raw = result.get_sentence()
            # get_sentence() 可能返回单个 dict 或句子 list，统一成句子列表
            if isinstance(raw, dict):
                sentences = [raw]
            elif isinstance(raw, list):
                sentences = [s for s in raw if isinstance(s, dict)]
            else:
                sentences = []

            text = "".join((s.get("text", "") or "") for s in sentences)
            segments = []
            duration_ms = 0
            for i, s in enumerate(sentences):
                b = s.get("begin_time", 0) or 0
                e = s.get("end_time", 0) or 0
                duration_ms = max(duration_ms, e)
                segments.append({
                    "id": i,
                    "start": b / 1000.0,
                    "end": e / 1000.0,
                    "text": s.get("text", "") or "",
                })
            _log(f"[transcribe] OK text_len={len(text)} text={text!r} segments={len(segments)}")
            # 返回 OpenAI 标准 verbose_json 结构，兼容闪电说对字段的严格校验
            return {
                "task": "transcribe",
                "language": "chinese",
                "duration": duration_ms / 1000.0,
                "text": text,
                "segments": segments,
            }
        else:
            _log(f"[transcribe] DashScope error: {result.message}")
            return {"error": {"message": f"DashScope error: {result.message}",
                              "status_code": 502}}
    except Exception as e:
        # 透传真实异常，方便排查
        _log(f"[transcribe] EXCEPTION {type(e).__name__}: {e}")
        return {"error": {"message": f"{type(e).__name__}: {e}", "status_code": 500}}
    finally:
        for p in (tmp_path,):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# ---- 闪电说自定义 ASR：走 /chat/completions，JSON body 内含 base64 音频 ----

def _try_decode_b64(s: str) -> bytes:
    """解码 base64，兼容 data:audio/...;base64, 前缀。"""
    if "," in s[:40]:
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _describe_structure(data, prefix="", depth=0, out=None):
    """递归描述 JSON 结构，长字符串（可能 base64）只记录长度。"""
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


def _find_audio_b64(data, out=None):
    """递归查找可能是 base64 音频的长字符串。"""
    if out is None:
        out = []
    if isinstance(data, dict):
        for v in data.values():
            _find_audio_b64(v, out)
    elif isinstance(data, list):
        for v in data:
            _find_audio_b64(v, out)
    elif isinstance(data, str) and len(data) > 2000:
        out.append(data)
    return out


def _recognize_bytes(audio_bytes: bytes) -> str:
    """把原始音频 bytes 转 16k wav 并调 DashScope 识别，返回文本。"""
    from http import HTTPStatus
    from dashscope.audio.asr import Recognition
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(audio_bytes)
    wav = _to_wav_16k_mono(tmp)
    try:
        _configure_dashscope()
        recognition = Recognition(model=MODEL, format="wav",
                                   sample_rate=16000, callback=None)
        result = recognition.call(wav)
        if result.status_code == HTTPStatus.OK:
            raw = result.get_sentence()
            sentences = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
            return "".join((s.get("text", "") or "") for s in sentences if isinstance(s, dict))
        return ""
    finally:
        for p in (tmp, wav):
            try:
                os.remove(p)
            except OSError:
                pass


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(request: Request):
    body = await request.body()
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as e:
        _log(f"[chat] body parse error: {type(e).__name__}: {e}")
        return {"choices": [{"message": {"content": ""}}]}

    # 记录 body 结构到日志，便于核对闪电说发的字段
    structure = _describe_structure(data)
    _log("[chat] BODY_STRUCTURE:\n" + "\n".join(structure))

    if not API_KEY:
        _log("[chat] DASHSCOPE_API_KEY not set")
        return {"choices": [{"message": {"content": ""}}]}

    # 提取音频 base64 候选
    candidates = _find_audio_b64(data)
    if not candidates:
        _log("[chat] no audio base64 found in body")
        return {"choices": [{"message": {"content": ""}}]}

    # 逐候选尝试解码识别，取第一个能出文本的
    for i, b64str in enumerate(candidates):
        try:
            audio_bytes = _try_decode_b64(b64str)
            if len(audio_bytes) < 100:
                continue
            _log(f"[chat] candidate[{i}] decoded bytes={len(audio_bytes)}")
            text = _recognize_bytes(audio_bytes)
            if text:
                _log(f"[chat] OK text={text!r}")
                return {"choices": [{"message": {"content": text}}]}
        except Exception as e:
            _log(f"[chat] candidate[{i}] error: {type(e).__name__}: {e}")
            continue

    _log("[chat] recognition produced no text")
    return {"choices": [{"message": {"content": ""}}]}


if __name__ == "__main__":
    uvicorn.run("dashscope_asr_proxy:app", host="127.0.0.1",
                port=int(os.environ.get("DASHSCOPE_PORT", "8004")))
