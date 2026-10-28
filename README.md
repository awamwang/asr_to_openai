# ASR → OpenAI 兼容代理（闪电说自定义语音识别服务商）

本地代理：把多家云厂商语音识别桥接成闪电说可用的「自定义语音识别服务商」，并兼容 OpenAI Whisper 风格接口。

## 产品定位（先看这个）

| | 说明 |
|---|---|
| **主场景** | 实时语音识别类客户端（如闪电说）按静音/时长**切成短段**后，每次 HTTP 识别一段 |
| **典型单次时长** | 数秒～约 1 分钟（由客户端切段策略决定） |
| **不是什么** | **不是**整篇文章 / 几十分钟录音的离线长文件转写工具；不做异步任务轮询、不拼装长文稿 |
| **交互形态** | 请求一次 → 返回该段全文（形态 A）。上游即便走 WebSocket，代理内也是收齐后再一次返回 |

选型、比价与「厂商硬限能撑多长」见 [docs/asr_models_guide.md](docs/asr_models_guide.md)；协议与对接见 [docs/client_integration.md](docs/client_integration.md)。

- **主入口**：`main.py`
- **默认端口**：`8004`（可用 `ASR_PORT` 或 config 修改）
- **模型命名**：`服务商/模型`，例如 `aliyun/qwen-audio-3.1-asr-flash-streaming`、`tencent/16k_zh`
- **`auto`**：只在服务商之间切换（每家用其优先模型），出错则试下一个；成功后下次优先从该服务商开始
- **`服务商/auto`**（如 `aliyun/auto`、`groq/auto`）：只在该服务商的模型列表内切换；成功后下次优先从该模型开始
- **裸服务商名**（如 `aliyun`、`tencent`）：定点调用该服务商的**优先模型**（上次成功模型 > 配置列表首项），只试这一个、失败即整体失败，**不做**服务商内模型切换——等价于 `服务商/<上次成功模型>` 的简写，不是 `服务商/auto`
- **策略模型 ID**（见「三、路由策略」）：`strategy-cost` / `strategy-speed` / `strategy-accuracy` / `strategy-free` / `strategy-free+speed` / `strategy-free+accuracy`；**不覆盖** `auto` 与显式 `服务商/模型`

**裸服务商名 vs `服务商/auto` 怎么选**：

| 想做什么 | 用哪个 | 行为 |
|---|---|---|
| 固定用某家最优模型（上次成功 / 默认首项），失败不换模型 | **裸名**（`aliyun`） | 只试优先模型 1 个，失败即报错 |
| 在某家内部做模型容灾（首模型失败自动换下一模型） | **`服务商/auto`**（`aliyun/auto`） | 按「上次成功 → 列表顺序」轮转，失败换下一个 |
| 跨服务商容灾（一家挂整体换下一家） | **`auto`** | 按 `auto_order` 换服务商，每家只试其优先模型 |
| 全池按费用/速度/准确度/免费排序选最优 | **`strategy-*`** | 见「三、路由策略」 |

> 一句话记忆：**裸名 = 认死一家不换模型；`服务商/auto` = 一家内部换模型；`auto` = 换服务商；`strategy-*` = 全局按指标挑**。

当前能力：
| 服务商 | 状态 | 启用条件 |
|---|---|---|
| 阿里云百炼 DashScope | ✅ 已实现 | `api_key` 非空 |
| Groq Whisper | ✅ 已实现 | `api_key` 非空；国内通常还需 `proxy` |
| 豆包语音（火山）1.0 / 2.0 | ✅ 已实现 | `api_key` 非空；旧版控制台还需 `app_id` |
| 硅基流动 SenseVoice | ✅ 已实现 | `api_key` 非空（国内直连） |
| 阶跃星辰 StepFun | ✅ 已实现 | `api_key` 非空 |
| 腾讯云 ASR | ✅ 已实现 | `secret_id` + `secret_key`（另需 `app_id`）；Flash（如 `16k_zh`）与实时（如 `realtime-16k_zh`） |
| 百度智能云短语音 | ✅ 已实现 | `api_key` + `secret_key`（另需 `app_id`）；`1537` / `80001`（单次硬限 ≤60s） |

管道能力（见 `config.example.yaml`）：
- **音频预处理**（`audio.preprocess` / `trim_silence`）：识别前统一 16k 单声道，并可裁首尾静音
- **容灾截断与冷却**（`routing.max_failover_candidates` 等）：`auto` / `strategy-*` 限制尝试次数，硬/软失败短时跳过
- **文本出口**（`text.clean_punctuation` / `smart_sentence`）：可选清洗停顿标点；智能断句仅保留阿里 streaming/realtime

失败时接口返回 **真实 HTTP 错误码**（如 400/502），不再用 200 + 空文本糊弄客户端。

---

> 客户端对接（闪电说协议、切段短语音 vs 真·流式、FunASR/WhisperLive 定位、分发检查清单）见 [docs/client_integration.md](docs/client_integration.md)。

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
      - qwen-audio-3.0-asr-flash
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
  # tencent / baidu / stepfun 等见 config.example.yaml
```

常用管道项（默认适合短切段；完整注释见 `config.example.yaml`）：

```yaml
routing:
  max_failover_candidates: 4      # auto / strategy-* 最多尝试几个候选
  failover_cooldown_sec: 300
  failover_soft_cooldown_sec: 60
audio:
  preprocess: true                # 识别前统一 16k 单声道
  trim_silence: true              # 裁首尾静音
text:
  clean_punctuation: false        # 主开关：false 保留上游标点（含腾讯不过滤）
  smart_sentence: false           # true 时只保留阿里 streaming/realtime
```

也可用环境变量（优先于 YAML）：

| 服务商 | 环境变量 |
|---|---|
| 阿里云 | `DASHSCOPE_API_KEY` / `ALIYUN_API_KEY`，`DASHSCOPE_WORKSPACE_ID`，`DASHSCOPE_ASR_MODELS`（逗号分隔）或 `DASHSCOPE_ASR_MODEL` |
| Groq | `GROQ_API_KEY`，`GROQ_BASE_URL`，`GROQ_PROXY`，`GROQ_TIMEOUT`，`GROQ_ASR_MODELS` / `GROQ_DEFAULT_MODEL` |
| 豆包 | `DOUBAO_API_KEY` / `VOLC_API_KEY`，`DOUBAO_APP_ID` / `VOLC_APP_ID`，`DOUBAO_WS_URL`，`DOUBAO_ASR_MODELS`，`DOUBAO_SEGMENT_MS` |
| 硅基流动 | `SILICONFLOW_API_KEY` / `SF_API_KEY`，`SILICONFLOW_BASE_URL`，`SILICONFLOW_ASR_MODELS` |
| 腾讯云 | `TENCENT_SECRET_ID`，`TENCENT_SECRET_KEY`，`TENCENT_APP_ID` |
| 百度 | `BAIDU_API_KEY`，`BAIDU_SECRET_KEY`，`BAIDU_APP_ID` |
| 阶跃 | `STEPFUN_API_KEY` |
| 通用 | `ASR_PORT`，`ASR_LOG`，`ASR_LOG_LEVEL`，`FFMPEG_PATH`，`ASR_STRATEGY` |

### 3. 启动

```powershell
python main.py
```

### 4. 闪电说面板填写

| 字段 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8004` |
| API Key | 随便填（本地无鉴权） |
| 模型 | `auto` / `strategy-speed` / `aliyun/auto` / `tencent/16k_zh` / `doubao/asr-2.0` |

地址带不带 `/v1` 均可。客户端会按静音等把麦克风流切成短段再请求；本代理按段返回文本，由客户端拼进实时翻译链路。

> 注意：服务商名是 **groq**（不是 grok）。旧配置键 `grok` 会自动按 groq 加载。
> 豆包也可写 `volcengine` / `volc`，会归一为 `doubao`。

开启 `audio.preprocess` 时，识别前会统一转 **16kHz 单声道**（失败回退原音频）；阿里云 / 豆包等路径本身也依赖本机 `ffmpeg`。

豆包模型与资源 ID 对应（[官方文档](https://docs.volcengine.com/docs/6561/1354869)）：

| 对外模型 | Resource ID |
|---|---|
| `asr-1.0` | `volc.bigasr.sauc.duration` |
| `asr-2.0` | `volc.seedasr.sauc.duration` |
| `asr-1.0-concurrent` | `volc.bigasr.sauc.concurrent` |
| `asr-2.0-concurrent` | `volc.seedasr.sauc.concurrent` |

豆包连接预热与分片大小（`config.yaml` → `providers.doubao`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `warm` | `true` | 识别成功后后台补建 1 个已连接未使用的 WS，把建连（~0.3s）移出下次请求的关键路径 |
| `warm_ttl` | `43200`（12h） | 预热连接空闲超过此时长视为失效（秒）。仅表示「客户端愿意复用多久」；服务端仍可能提前断开，**取用时若已断开会自动重建一次**，不会导致请求失败 |
| `segment_ms` | `1000` | 音频分片大小（毫秒）。分片数 ≈ 网络往返次数，**直接决定识别耗时** |
| `segment_ms_by_model` | 见下 | 按模型覆盖分片大小；键可用模型名或资源 ID，大小写不敏感 |

实测（2026-10-10，8.28s 音频，预热命中，端到端）：分片 `200ms`(44 片)=1161ms、
`500`=1076ms、**`1000`(11 片)=823ms**、`2000`=840ms、`5000`=1066ms；
5 段音频平均 `200ms→1495ms`、`1000ms→1010ms`（**-32%**），识别文本逐字一致。

---

## 三、路由策略

策略以**请求模型 ID**选择，不改写 `auto`。详见 `docs/asr_models_guide.md`。
硬限（如百度 ≤60s、Groq ≤25MB）用于排除过长/过大候选；本产品仍应按**短切段**调用，不要依赖「厂商能撑 2h」去整段转长音频。

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
4. 硬上限排除：如百度短语音 60s、Groq 25MB、腾讯极速版 2h/100MB；
5. 美元刊例（Groq）按固定汇率 **1 USD = 7 CNY** 折合后参与比价（粗略，非实时牌价）；未知计费排最后。

> 免费走独立模型 ID `strategy-free`。额度余量能查则查，查不到则调用失败后试下一个；
> 无法感知则视为仍免费。

### 模型档案与扩展

策略依赖的「费用 / 速度等级 / 准确度等级 / 硬上限」都登记在 `asr_proxy/strategy/metadata.py`
（数据源自 `docs/asr_models_guide.md`，2026-10）。已收录阿里 / 豆包 / 硅基 / Groq /
阶跃 / 腾讯极速版 / 百度等已接入模型；对应服务商密钥完备后自动参与路由。

新增一个策略：继承 `asr_proxy/strategy/base.py` 的 `RoutingStrategy` 实现 `rank()`，
并在 `asr_proxy/strategy/__init__.py` 的 `_REGISTRY` 注册策略名即可。
`recommended` 即预留了这样的口子。

---

## 四、接口清单

- `GET /health`：查看已启用服务商、模型列表、上次成功记录
- `GET /v1/models`：列出 `auto`、策略 ID、各服务商裸名、`服务商/auto` 及全部 `服务商/模型`（排序：策略最上边 → 按 auto_order 每家先裸名再 `/auto` 再具体模型）
- `POST /chat/completions`（`/v1/...`）：闪电说主路径，JSON base64 音频 → `choices[0].message.content`
- `POST /audio/transcriptions`（`/v1/...`）：标准 multipart 兼容，返回 verbose_json

### 查看可用模型 ID（CLI）

不启动服务，直接输出当前配置下**真实可用**的模型 ID 列表（读取实际 config.yaml + 环境变量，密钥完备才算启用）：

```powershell
python main.py --models        # 或 --list-models
```

输出顺序：最上边为策略模型（`auto` + `strategy-cost/speed/accuracy/free/free+speed/free+accuracy`），随后按 `auto_order` 的服务商顺序，每家依次列**裸服务商名**（定点优先模型）→ `服务商/auto` → 具体模型 `服务商/模型`。

---

## 五、测试

```powershell
# 基础单测（mock，不需要真实 Key）
pytest tests/test_basic.py tests/test_strategy.py -q

# 含真实阿里云识别（需已配置 DASHSCOPE_API_KEY）
pytest tests/test_basic.py -q -m integration
```

测试音频：`bench/audio/s1_short.wav`（由 `bench/gen_audio.py` 生成）。

模拟闪电说请求：

```powershell
python -c "import requests,base64; b=base64.b64encode(open(r'bench/audio/s1_short.wav','rb').read()).decode(); \
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
3. 本产品面向**短切段**；不要把几十分钟整文件一次丢进来当长转写用（体感延迟、费用、硬限都不合适）。
4. DashScope 的 `format`/`sample_rate` 须与音频一致；开预处理或走阿里/豆包时依赖 ffmpeg 转 16k mono。
5. `Recognition.call().get_sentence()` 可能返回 list，需遍历拼文本。
6. Groq 在国内通常要配 `proxy`（`GROQ_PROXY`）。
7. 豆包新版控制台只填 `api_key`；旧版需 `app_id` + Access Token。并发版资源包请用 `asr-*-concurrent`。
8. `text.smart_sentence=true` 会过滤掉腾讯/豆包/硅基等，只留阿里 streaming/realtime。
9. 识别失败会返回 502，不再返回 200 + 空 `content`。
