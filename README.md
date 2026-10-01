# ASR → OpenAI 兼容代理（闪电说自定义语音识别服务商）

本地代理：把配置的多家语音识别接口，转化成闪电说可用的「自定义语音识别服务商」格式。

- **主入口**：`main.py`
- **默认端口**：`8004`（可用 `ASR_PORT` 或 config 修改）
- **模型命名**：`服务商/模型`，例如 `aliyun/qwen-audio-3.1-asr-flash-streaming`、`groq/whisper-large-v3`
- **`auto`**：只在服务商之间切换（每家用其优先模型），出错则试下一个；成功后下次优先从该服务商开始
- **`服务商/auto`**（如 `aliyun/auto`、`groq/auto`）：只在该服务商的模型列表内切换；成功后下次优先从该模型开始

当前能力：
| 服务商 | 状态 | 启用条件 |
|---|---|---|
| 阿里云百炼 DashScope | ✅ 已实现 | `api_key` 非空 |
| Groq Whisper | ✅ 已实现 | `api_key` 非空；国内通常还需 `proxy` |

失败时接口返回 **真实 HTTP 错误码**（如 400/502），不再用 200 + 空文本糊弄客户端。

---

## 一、闪电说真实协议（最重要）

面板**不走** OpenAI 的 `/v1/audio/transcriptions`，而是：

```
POST /chat/completions
Content-Type: application/json
body: { model, messages:[{ role, content:[{ type, input_audio:{ data: "<base64音频>" } }] }] }
```

成功响应必须是：

```json
{ "choices": [ { "message": { "content": "识别出的文本" } } ] }
```

失败响应示例（HTTP 502）：

```json
{ "error": { "message": "...", "status_code": 502 } }
```

---

## 二、快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 配置 API Key

复制示例配置：

```powershell
copy config.example.yaml config.yaml
```

编辑 `config.yaml`，填写服务商 `api_key`（**有 Key = 启用**）：

```yaml
providers:
  aliyun:
    api_key: "sk-你的Key"
    workspace_id: "你的工作空间ID"
    models:
      - qwen-audio-3.1-asr-flash-streaming
      - qwen3-asr-flash-realtime
  groq:
    api_key: "gsk_你的Key"
    base_url: "https://api.groq.com/openai/v1"
    proxy: "http://127.0.0.1:7881"   # 国内访问 Groq 通常需要
    models:
      - whisper-large-v3
      - whisper-large-v3-turbo
```

也可用环境变量（优先于 YAML）：

| 服务商 | 环境变量 |
|---|---|
| 阿里云 | `DASHSCOPE_API_KEY` / `ALIYUN_API_KEY`，`DASHSCOPE_WORKSPACE_ID`，`DASHSCOPE_ASR_MODELS`（逗号分隔）或 `DASHSCOPE_ASR_MODEL` |
| Groq | `GROQ_API_KEY`，`GROQ_BASE_URL`，`GROQ_PROXY`，`GROQ_TIMEOUT`，`GROQ_ASR_MODELS` / `GROQ_DEFAULT_MODEL` |
| 通用 | `ASR_PORT`，`ASR_LOG`，`FFMPEG_PATH` |

### 3. 启动

```powershell
python main.py
```

### 4. 闪电说面板填写

| 字段 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8004` |
| API Key | 随便填（本地无鉴权） |
| 模型 | `auto` / `aliyun/auto` / `groq/auto` / `groq/whisper-large-v3` |

地址带不带 `/v1` 均可。

> 注意：服务商名是 **groq**（不是 grok）。旧配置键 `grok` 会自动按 groq 加载。

---

## 三、接口清单

- `GET /health`：查看已启用服务商、模型列表、上次成功记录
- `GET /v1/models`：列出 `auto`、`服务商/auto` 及全部 `服务商/模型`
- `POST /chat/completions`（`/v1/...`）：闪电说主路径，JSON base64 音频 → `choices[0].message.content`
- `POST /audio/transcriptions`（`/v1/...`）：标准 multipart 兼容，返回 verbose_json

阿里云路径识别前用本机 `ffmpeg` 转成 **16kHz 单声道 PCM WAV**；Groq 直接上传原始音频到上游 `/audio/transcriptions`。

---

## 四、测试

```powershell
# 基础单测（mock，不需要真实 Key）
pytest tests/test_basic.py -q

# 含真实阿里云识别（需已配置 DASHSCOPE_API_KEY）
pytest tests/test_basic.py -q -m integration
```

测试音频：`test_asr_16k.wav`。

模拟闪电说请求：

```powershell
python -c "import requests,base64; b=base64.b64encode(open(r'test_asr_16k.wav','rb').read()).decode(); \
body={'model':'auto','messages':[{'role':'user','content':[{'type':'input_audio','input_audio':{'data':'data:audio/wav;base64,'+b}}]}]};\
print(requests.post('http://127.0.0.1:8004/chat/completions',json=body,timeout=90).json())"
```

---

## 五、旧脚本

`dashscope_asr_proxy.py` 为早期单文件阿里云代理，仍可单独运行；新功能请用 `main.py`。

---

## 六、踩坑摘要

1. 闪电走 `/chat/completions` + JSON base64，不是 Whisper 的 multipart。
2. 必须把文本放进 `choices[0].message.content`，否则报「未返回有效文本」。
3. DashScope 的 `format`/`sample_rate` 须与音频一致，统一转 16k mono wav。
4. `Recognition.call().get_sentence()` 可能返回 list，需遍历拼文本。
5. Groq 在国内通常要配 `proxy`（与 `groq_proxy` 的 `GROQ_PROXY` 相同）。
6. 识别失败会返回 502，不再返回 200 + 空 `content`。
