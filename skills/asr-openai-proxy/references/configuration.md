# 配置参考（如何生成 config.yaml）

> 数据来源：项目 `config.py`、`config.example.yaml`、`docs/asr_models_guide.md`、`README.md`。
> 价格 / 免费额度可能随厂商调整，以各平台控制台为准。
> 产品主场景：实时客户端的**短切段**识别（数秒～约 1 分钟），不是几十分钟整篇录音的离线长转写。

## 0. 生成配置文件（标准流程）

```powershell
# 1) 在项目根目录复制示例配置
Copy-Item config.example.yaml config.yaml

# 2) 编辑 config.yaml，填入各服务商密钥
#    密钥完备 = 该服务商启用（见「启用规则」），缺密钥的服务商自动跳过

# 3) 校验配置并查看可用模型 ID / 接口路径
python skills/asr-openai-proxy/scripts/report_status.py
```

密钥**也可以不写进 YAML**，改用环境变量（优先级高于 YAML），适合不落盘密钥的场景。

## 1. 启用规则（密钥完备即启用）

| 服务商 | 启用条件 | 字段位置 |
|---|---|---|
| 阿里云百炼 | `api_key` 非空 | `providers.aliyun.api_key` |
| Groq | `api_key` 非空（国内通常还需 `proxy`） | `providers.groq.api_key` / `.proxy` |
| 豆包（火山） | `api_key` 非空（旧版控制台再加 `app_id`） | `providers.doubao.api_key` / `.app_id` |
| 硅基流动 | `api_key` 非空 | `providers.siliconflow.api_key` |
| 阶跃星辰 | `api_key` 非空 | `providers.stepfun.api_key` |
| 腾讯云 | `secret_id` + `secret_key` 同时非空（另需 `app_id`） | `providers.tencent.secret_id` / `.secret_key` / `.app_id` |
| 百度智能云 | `api_key` + `secret_key` 同时非空（另需 `app_id`） | `providers.baidu.api_key` / `.secret_key` / `.app_id` |

服务商名归一：`grok`→`groq`；`volcengine`/`volc`/`bytedance`→`doubao`；`silicon`/`sf`→`siliconflow`；`step`→`stepfun`；`tencentcloud`/`qcloud`→`tencent`。

## 2. 各服务商配置细则

### 2.1 阿里云百炼 DashScope（aliyun）
- 控制台：https://bailian.console.aliyun.com/ ；模型定价：https://help.aliyun.com/zh/model-studio/model-pricing
- 开通：阿里云账号 + 实名 → 开通百炼 → 创建 API Key
- 鉴权：`Authorization: Bearer <DASHSCOPE_API_KEY>`
- YAML 字段：
  ```yaml
  aliyun:
    api_key: "sk-xxx"
    workspace_id: ""          # 可选，部分调用需要
    models:
      - qwen-audio-3.1-asr-flash-streaming   # 当前主力，流式 WS，速度 S4
      - qwen-audio-3.0-asr-flash-streaming
      - qwen-audio-3.0-asr-flash             # 同步/非流式，S3
    semantic_punctuation_enabled: false      # 流式断句：false=VAD（默认）true=语义断句
    max_sentence_silence: 2500               # VAD 静音阈值 ms，范围 200–6000
  ```
- 环境变量：`DASHSCOPE_API_KEY`/`ALIYUN_API_KEY`、`DASHSCOPE_WORKSPACE_ID`、`DASHSCOPE_ASR_MODELS`（逗号分隔）或 `DASHSCOPE_ASR_MODEL`、`DASHSCOPE_SEMANTIC_PUNCTUATION`、`DASHSCOPE_MAX_SENTENCE_SILENCE`、`ALIYUN_TIMEOUT`

### 2.2 火山引擎·豆包（doubao）
- 控制台：https://console.volcengine.com/speech ；模型列表：https://docs.volcengine.com/docs/6561/2499930
- 开通：火山账号 + 实名 → 开通语音识别 → 取 `api_key`（新版仅 api_key；旧版再加 app_id + access_token）
- YAML 字段：
  ```yaml
  doubao:
    api_key: "xxx"            # 新版 X-Api-Key / AccessToken
    app_id: ""                # 旧版控制台才填
    base_url: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    timeout: 90
    models:
      - asr-2.0               # → volc.seedasr.sauc.duration（SeedASR 2.0）
      - asr-1.0               # → volc.bigasr.sauc.duration
      # 也可直接填资源 ID：volc.seedasr.sauc.concurrent
  ```
- 环境变量：`DOUBAO_API_KEY`/`VOLC_API_KEY`、`DOUBAO_APP_ID`/`VOLC_APP_ID`、`DOUBAO_WS_URL`/`DOUBAO_BASE_URL`、`DOUBAO_ASR_MODELS`

### 2.3 硅基流动 SiliconFlow（siliconflow）
- 密钥：https://cloud.siliconflow.cn/account/ak ；接口：https://docs.siliconflow.cn/cn/api-reference/audio/create-audio-transcriptions
- 免费：`FunAudioLLM/SenseVoiceSmall`、`TeleAI/TeleSpeechASR` 长期免费（只进 `strategy-free`，不进 `strategy-cost`）
- YAML 字段：
  ```yaml
  siliconflow:
    api_key: "sk-xxx"
    base_url: "https://api.siliconflow.cn/v1"
    proxy: ""
    timeout: 120
    models:
      - SenseVoiceSmall       # 短名映射 FunAudioLLM/SenseVoiceSmall
  ```
- 环境变量：`SILICONFLOW_API_KEY`/`SF_API_KEY`、`SILICONFLOW_BASE_URL`、`SILICONFLOW_PROXY`、`SILICONFLOW_ASR_MODELS`

### 2.4 Groq（groq，海外）
- 控制台：https://console.groq.com ；定价：https://groq.com/pricing
- 国内访问通常需要代理；`socks5://` 本地解析 DNS 常 TLS 失败，请用 `socks5h://`
- YAML 字段：
  ```yaml
  groq:
    api_key: "gsk_xxx"
    base_url: "https://api.groq.com/openai/v1"
    proxy: "http://127.0.0.1:7881"      # 或 socks5h://127.0.0.1:1080
    timeout: 300
    models:
      - whisper-large-v3
      - whisper-large-v3-turbo
  ```
- 环境变量：`GROQ_API_KEY`、`GROQ_BASE_URL`、`GROQ_PROXY`、`GROQ_ASR_MODELS`/`GROQ_DEFAULT_MODEL`、`GROQ_TIMEOUT`

### 2.5 阶跃星辰 StepFun（stepfun）
- 开放平台：https://platform.stepfun.com/ ；定价：https://platform.stepfun.com/docs/zh/guides/pricing/details
- 接口 `POST /v1/audio/transcriptions`（OpenAI 风格），Bearer 单一密钥；`stepaudio-2.5-asr` 刊例 0.15 元/时，常为 `strategy-cost` 首选
- YAML 字段：
  ```yaml
  stepfun:
    api_key: "xxx"
    base_url: "https://api.stepfun.com/v1"
    models:
      - stepaudio-2.5-asr
  ```
- 环境变量：`STEPFUN_API_KEY`/`STEP_API_KEY`、`STEPFUN_BASE_URL`、`STEPFUN_ASR_MODELS`

### 2.6 腾讯云 ASR（tencent，极速版）
- 控制台：https://console.cloud.tencent.com/asr ；CAM 密钥：https://console.cloud.tencent.com/cam/capi
- 鉴权：Flash HMAC-SHA1（SecretId / SecretKey / AppID）；硬限 t>2h 或 >100MB 被策略排除
- YAML 字段：
  ```yaml
  tencent:
    secret_id: "AKIDxxx"
    secret_key: "xxx"
    app_id: "125xxxxxxx"
    models:
      - 16k_zh               # Flash 中文通用（月 5h 极速包，S1）
      - realtime-16k_zh      # 实时 WebSocket（另计实时 5h 包）
      - "16k_zh-PY"          # Flash 中英粤（与 16k_zh 同一 5h）
      - 16k_en
      - 16k_yue
      # - 16k_zh_en          # Flash 大模型1.0，无月免费
  ```
- 环境变量：`TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`、`TENCENT_APP_ID`、`TENCENT_ASR_MODELS`

### 2.7 百度智能云 短语音（baidu）
- 控制台：https://console.bce.baidu.com/ai-engine/speech/overview/index ；短语音文档：https://cloud.baidu.com/doc/SPEECH/s/Jlbxdezuf
- 鉴权：AppID / API Key / Secret Key → OAuth 换 access_token（约 30 天自动刷新）；**单次硬限 ≤60s**
- 注意：`80001`（极速版）须在控制台开通权限，否则上游报无权限并 failover
- YAML 字段：
  ```yaml
  baidu:
    app_id: "xxxx"
    api_key: "xxx"
    secret_key: "xxx"
    models:
      - "1537"              # 短语音标准版
      - "80001"             # 短语音极速版
  ```
- 环境变量：`BAIDU_API_KEY`/`BAIDU_AK`、`BAIDU_SECRET_KEY`/`BAIDU_SK`、`BAIDU_APP_ID`、`BAIDU_ASR_MODELS`

## 3. 服务器与通用配置

```yaml
server:
  host: "127.0.0.1"          # 环境变量：ASR_PORT 覆盖 port
  port: 8004
  log_file: "asr_proxy.log"  # 环境变量：ASR_LOG
  log_level: "info"          # debug|info|warning|error；debug 含逐步尝试链；ASR_LOG_LEVEL
  log_backup_days: 7         # ASR_LOG_BACKUP_DAYS

ffmpeg_path: "ffmpeg"        # FFMPEG_PATH；识别前转 16kHz 单声道 PCM（配合 audio.preprocess）
strategy: auto               # 兼容字段，不再劫持 auto；真正策略用请求 model ID

# 服务商容灾顺序（auto 用）；未列入的已启用服务商自动追加末尾
auto_order: [aliyun, doubao, siliconflow, groq, stepfun, tencent, baidu]

# 各 strategy-* 白名单/顺序（可选；空列表=不限制）
free_order: []
cost_order: []
speed_order: []
accuracy_order: []

# 容灾截断与冷却（auto / strategy-*；显式定点不受限）
routing:
  max_failover_candidates: 4
  failover_cooldown_sec: 300
  failover_soft_cooldown_sec: 60

# 识别前统一：16k 单声道 + 裁首尾静音（失败回退原音频）
audio:
  preprocess: true
  trim_silence: true

# 文本出口
text:
  clean_punctuation: false   # 主开关；false 时腾讯不过滤标点、豆包打标点
  smart_sentence: false      # true 时只保留阿里 streaming/realtime
```

## 4. 校验配置（不启动服务）

```powershell
# 方式一：技能自带脚本（推荐，输出启用服务商 / 模型 ID / 接口路径）
python skills/asr-openai-proxy/scripts/report_status.py
python skills/asr-openai-proxy/scripts/report_status.py --json

# 方式二：直接加载配置对象
python -c "from asr_proxy.config import load_config; c=load_config(); print(c.host, c.port, [p.name for p in c.enabled_providers()])"
```

配置加载逻辑：`config.yaml` 不存在时回退 `config.example.yaml`；YAML 值被环境变量覆盖；密钥完备判定见上文「启用规则」表。

## 5. 常见问题

- **服务商名写错**：`grok`（错误）→ `groq`（正确）；豆包可用 `volcengine`/`volc` 别名。
- **服务商没启用**：先跑 `report_status.py` 确认密钥是否完备（腾讯/百度为双密钥）。
- **Groq 识别失败**：国内需 `proxy`；SOCKS 用 `socks5h://`（本地解析 DNS 的 socks5:// 常 TLS 失败）。
- **阿里流式断句太碎**：调大 `max_sentence_silence`（200–6000ms），或 `semantic_punctuation_enabled: true` / `text.smart_sentence: true` 走语义断句（后者会过滤非阿里 streaming/realtime）。
- **长音频一次识别很慢**：本产品面向短切段；几十分钟整文件请改由客户端切片，或使用厂商异步长转写（本仓库不接）。
- **模型 ID 语义**：`auto`=服务商容灾；`服务商/auto`=该服务商模型列表内切换；`服务商/模型`=定点调用；`strategy-cost/speed/accuracy/free(/+speed/+accuracy)`=策略路由。策略不覆盖 `auto` 与显式 ID。
