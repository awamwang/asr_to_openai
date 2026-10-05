# ASR → OpenAI 兼容代理（闪电说自定义语音识别服务商）

本地代理：把配置的多家语音识别接口，转化成闪电说可用的「自定义语音识别服务商」格式。

- **主入口**：`main.py`
- **默认端口**：`8004`（可用 `ASR_PORT` 或 config 修改）
- **模型命名**：`服务商/模型`，例如 `aliyun/qwen-audio-3.1-asr-flash-streaming`、`groq/whisper-large-v3`
- **`auto`**：只在服务商之间切换（每家用其优先模型），出错则试下一个；成功后下次优先从该服务商开始
- **`服务商/auto`**（如 `aliyun/auto`、`groq/auto`）：只在该服务商的模型列表内切换；成功后下次优先从该模型开始
- **策略模型 ID**（见「三、路由策略」）：`strategy-cost` / `strategy-speed` / `strategy-accuracy` / `strategy-free` / `strategy-free+speed` / `strategy-free+accuracy`；**不覆盖** `auto` 与显式 `服务商/模型`

当前能力：
| 服务商 | 状态 | 启用条件 |
|---|---|---|
| 阿里云百炼 DashScope | ✅ 已实现 | `api_key` 非空 |
| Groq Whisper | ✅ 已实现 | `api_key` 非空；国内通常还需 `proxy` |
| 豆包语音（火山）1.0 / 2.0 | ✅ 已实现 | `api_key` 非空；旧版控制台还需 `app_id` |
| 硅基流动 SenseVoice | ✅ 已实现 | `api_key` 非空（国内直连） |

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
  doubao:
    # 新版控制台：只填 api_key；旧版再填 app_id
    api_key: "你的X-Api-Key或AccessToken"
    app_id: ""
    # 默认 bigmodel_nostream（准确率优先）
    base_url: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    models:
      - asr-2.0   # 豆包流式语音识别 2.0 → volc.seedasr.sauc.duration
      - asr-1.0   # 豆包流式语音识别 1.0 → volc.bigasr.sauc.duration
  siliconflow:
    api_key: "sk-你的硅基流动Key"
    base_url: "https://api.siliconflow.cn/v1"
    models:
      - SenseVoiceSmall
```

也可用环境变量（优先于 YAML）：

| 服务商 | 环境变量 |
|---|---|
| 阿里云 | `DASHSCOPE_API_KEY` / `ALIYUN_API_KEY`，`DASHSCOPE_WORKSPACE_ID`，`DASHSCOPE_ASR_MODELS`（逗号分隔）或 `DASHSCOPE_ASR_MODEL` |
| Groq | `GROQ_API_KEY`，`GROQ_BASE_URL`，`GROQ_PROXY`，`GROQ_TIMEOUT`，`GROQ_ASR_MODELS` / `GROQ_DEFAULT_MODEL` |
| 豆包 | `DOUBAO_API_KEY` / `VOLC_API_KEY`，`DOUBAO_APP_ID` / `VOLC_APP_ID`，`DOUBAO_WS_URL`，`DOUBAO_ASR_MODELS` |
| 硅基流动 | `SILICONFLOW_API_KEY` / `SF_API_KEY`，`SILICONFLOW_BASE_URL`，`SILICONFLOW_ASR_MODELS` |
| 通用 | `ASR_PORT`，`ASR_LOG`，`FFMPEG_PATH`，`ASR_STRATEGY` |

### 3. 启动

```powershell
python main.py
```

### 4. 闪电说面板填写

| 字段 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8004` |
| API Key | 随便填（本地无鉴权） |
| 模型 | `auto` / `aliyun/auto` / `doubao/asr-2.0` / `siliconflow/SenseVoiceSmall` |

地址带不带 `/v1` 均可。

> 注意：服务商名是 **groq**（不是 grok）。旧配置键 `grok` 会自动按 groq 加载。
> 豆包也可写 `volcengine` / `volc`，会归一为 `doubao`。

阿里云 / 豆包路径识别前用本机 `ffmpeg` 转成 **16kHz 单声道 PCM**；Groq 直接上传原始音频到上游 `/audio/transcriptions`。

豆包模型与资源 ID 对应（[官方文档](https://docs.volcengine.com/docs/6561/1354869)）：

| 对外模型 | Resource ID |
|---|---|
| `asr-1.0` | `volc.bigasr.sauc.duration` |
| `asr-2.0` | `volc.seedasr.sauc.duration` |
| `asr-1.0-concurrent` | `volc.bigasr.sauc.concurrent` |
| `asr-2.0-concurrent` | `volc.seedasr.sauc.concurrent` |

---

## 三、路由策略

策略以**请求模型 ID**选择，不改写 `auto`。详见 `docs/asr_models_guide.md`。

| model ID | 含义 | 行为 |
|---|---|---|
| `auto`（默认） | 服务商容灾切换 | 按 `auto_order` + 上次成功优先 |
| `strategy-cost` | 费用优先 | 只比较刊例付费价（按次 vs 秒×时长）；免费额度不占优 |
| `strategy-speed` | 速度优先 | 按模型速度等级 S1→S5（静态等级，未实测） |
| `strategy-accuracy` | 准确度优先 | 按静态准确度等级（未实测） |
| `strategy-free` | 免费策略 | 只在长期免费/带额度的模型中，保持配置顺序 |
| `strategy-free+speed` / `strategy-free+accuracy` | 组合 | 先限定免费候选，再按速度或准确度 |

切换示例（闪电说 / OpenAI 兼容请求的 `model` 字段）：

```yaml
# 不要用 config.strategy 去劫持 auto
# 请求里填：
#   model: strategy-cost
#   model: strategy-free
#   model: strategy-free+speed
```

### 费用优先（cost）规则

1. 识别前先探测音频时长（WAV 解析头部；非 WAV 用 ffprobe；失败则不做时长过滤）；
2. 无刊例价的长期免费模型（如硅基 SenseVoice）**不进入**本策略；
3. 付费模型按本次时长 t 预估刊例费用：`按次价 P` vs `秒单价 S × max(t, 最短计费时长)`，取低者；免费额度不算成本 0；
4. 硬上限排除：如百度短语音 60s；
5. 非人民币刊例（Groq USD）不混比；未知计费排最后。

> 免费走独立模型 ID `strategy-free`。额度余量能查则查，查不到则调用失败后试下一个；
> 无法感知则视为仍免费。

### 模型档案与扩展

策略依赖的「费用 / 速度等级 / 准确度等级 / 硬上限」都登记在 `asr_proxy/strategy/metadata.py`
（数据源自 `docs/asr_models_guide.md`，2026-10）。已收录现有 4 家平台全部在用模型，
以及文档中拟新增的阶跃 / 腾讯 / 百度模型——对应服务商接入并启用后自动参与路由。

新增一个策略：继承 `asr_proxy/strategy/base.py` 的 `RoutingStrategy` 实现 `rank()`，
并在 `asr_proxy/strategy/__init__.py` 的 `_REGISTRY` 注册策略名即可。
`recommended` 即预留了这样的口子。

---

## 四、接口清单

- `GET /health`：查看已启用服务商、模型列表、上次成功记录
- `GET /v1/models`：列出 `auto`、策略 ID、`服务商/auto` 及全部 `服务商/模型`
- `POST /chat/completions`（`/v1/...`）：闪电说主路径，JSON base64 音频 → `choices[0].message.content`
- `POST /audio/transcriptions`（`/v1/...`）：标准 multipart 兼容，返回 verbose_json

---

## 五、测试

```powershell
# 基础单测（mock，不需要真实 Key）
pytest tests/test_basic.py tests/test_strategy.py -q

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

## 六、旧脚本

`dashscope_asr_proxy.py` 为早期单文件阿里云代理，仍可单独运行；新功能请用 `main.py`。

---

## 七、踩坑摘要

1. 闪电走 `/chat/completions` + JSON base64，不是 Whisper 的 multipart。
2. 必须把文本放进 `choices[0].message.content`，否则报「未返回有效文本」。
3. DashScope 的 `format`/`sample_rate` 须与音频一致，统一转 16k mono wav。
4. `Recognition.call().get_sentence()` 可能返回 list，需遍历拼文本。
5. Groq 在国内通常要配 `proxy`（与 `groq_proxy` 的 `GROQ_PROXY` 相同）。
6. 豆包新版控制台只填 `api_key`；旧版需 `app_id` + Access Token。并发版资源包请用 `asr-*-concurrent`。
7. 识别失败会返回 502，不再返回 200 + 空 `content`。
