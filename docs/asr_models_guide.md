# ASR 模型选型与路由手册

> 用途：统一对比本项目**现有 4 个平台**的在用模型，以及拟新增的 5 个模型。
> 价格/免费额度采集于 **2026-10**，厂商可能调整，下单前以各平台控制台为准。
> 请求 `auto` / `服务商/auto` / 显式 `服务商/模型` 的语义不变；
> `strategy-cost` / `strategy-speed` / `strategy-accuracy` / `strategy-free`（及 `strategy-free+speed` 等组合）是**独立模型 ID**，见第三部分。

---

## 一、字段说明

| 字段 | 取值含义 |
|---|---|
| **接口类型** | `同步HTTP`：上传完音频、请求内一次返回全文；`流式(WS/SSE)`：边上传边出字，适合实时；`非流式WS`：WebSocket 承载、整句/整段返回（本质是文件转写）；`异步文件`：先提交任务再轮询取结果 |
| **免费额度** | 区分「周期性（每月）」「一次性（开通后 N 天）」「长期免费」 |
| **适合语音长短** | 极短 ≤10s ｜ 短 10s–60s ｜ 中 1–10min ｜ 长 10min–2h ｜ 超长 ≥2h（人工参考与硬限标注，**不是**费用优势档；免费额度不改变此列） |
| **速度等级** | 见下方分级定义，数字越小越快 |
| **参考单价** | 小用量刊例价；按次计费的给出每次价格 |

**速度等级定义（端到端耗时，含网络）：**
- 🥇 **S1 极速**：30min 音频约 10s 返回（≈180 倍速），同步极速版
- 🥈 **S2 很快**：耗时约音频时长 1/10（≈10 倍速）
- 🥉 **S3 较快**：轻量模型，数倍于实时，整段同步返回
- **S4 常规**：按实时或略快于实时，秒级~十几秒返回
- **S5 异步**：分钟级~小时级返回（不保证时效，换低价）

---

## 二、模型总表（现有 + 新增）

> 「现有」= 本项目 `asr_proxy` 已接入；「新增」= 拟接入。
> 调用名可直接用于代理的 `model` 参数（如 `stepfun/stepaudio-2.5-asr`，接入后生效）。

| # | 状态 | 平台 | 模型 / 引擎 ID | 接口类型 | 免费额度 | 适合长短 | 速度等级 | 参考单价 |
|---|---|---|---|---|---|---|---|---|
| 1 | 现有 | 阿里云百炼 | `qwen-audio-3.1-asr-flash-streaming`（当前主力） | 流式 WS（可整段） | 一次性：36000 秒(10h)/开通后90天 | 短–长 | S4 | 按 Token 计费：输入 6 元/百万、输出 4.5 元/百万（2026-10 价页；旧口径"按秒约 1 元/时"已失效，以控制台为准） |
| 2 | 现有 | 阿里云百炼 | `qwen-audio-3.0-asr-flash` | 同步/非流式 | 一次性：36000 秒(10h)/90天 | 短–中 | S3 | 0.00026 元/秒（≈0.94 元/时） |
| 3 | 现有 | 火山引擎·豆包 | `asr-2.0`（SeedASR 2.0，sauc nostream） | 非流式 WS（整段返回） | 一次性：**20 小时** | 中–长 | S3 | 4.5 元/时（流式识别2.0刊例）；按时长计费、毫秒级计量 |
| 4 | 现有 | 硅基流动 | `FunAudioLLM/SenseVoiceSmall` | 同步 HTTP（OpenAI 兼容） | **长期免费** | 短–中（建议≤5min） | S2 | 免费 |
| 5 | 现有 | Groq（海外） | `whisper-large-v3` | 同步 HTTP（OpenAI 兼容） | 免费层：2000 次/日、28800 秒/日 | 短–长（文件≤25MB） | S2 | $0.111/时；turbo $0.04/时 |
| 6 | **新增** | 阶跃星辰 | **`stepaudio-2.5-asr`** ⭐性价比主力 | 同步 HTTP（OpenAI 兼容） | 无长期免费（看新号赠送） | 短–长 | S2–S3 | **0.15 元/时** |
| 7 | **新增** | 腾讯云 | **录音文件识别·大模型2.0**（`16k_zh_en_2.0`） | 异步文件（提交+查询） | 周期性：10 小时/月 | 长–超长 | S5（异步，时效宽松） | **0.8 元/时** |
| 8 | **新增** | 百度智能云 | **短语音识别标准版**（`dev_pid=1537`） | 同步 HTTP | 一次性：约 **200 万次**（长期有效） | **极短–短（硬限 60s）** | S3 | 0.0034 元/次起（≈0.2 元/时） |
| 9 | **新增** | 百度智能云 | **短语音识别极速版**（`dev_pid=80001`） | 同步 HTTP | 一次性：**5 万次** | **极短–短（硬限 60s）** | S2（≈10 倍速） | 按次，略高于标准版 |
| 10 | **新增** | 腾讯云 | **录音文件识别极速版**（`16k_zh_en`）速度兜底 | 同步 HTTP（请求内返回） | 周期性：5 小时/月 | 中–长（≤2h/100MB） | 🥇**S1（30min≈10s）** | 3.1 元/时起（按量阶梯）；按时长计费，最低计费单位 1 秒 |

> 备注：
> - 序号 1（阿里 3.1 流式）2026-10 价页已改**按 Token 计费**（输入 6 元/百万、输出 4.5 元/百万），旧「千问 ASR 系列按秒计费」口径仅适用于 3.0 及更早版本；精确价格以百炼控制台为准。
> - 豆包 `asr-2.0` 走的是 `bigmodel_nostream`（流式引擎、非流式返回），按流式语音识别计费；若要更便宜的整文件方案，可另接豆包「录音文件识别 AUC 2.0」（0.8 元/时，异步）。
> - 百度两款为**按次**计费且单次 ≤60 秒，做长音频需先切片，或改用腾讯/阶跃。

**计费粒度速查（2026-10 按厂商文档核实，流式/非流式均适用）：**

| 模型 / 产品 | 计费方式 | 最小计费单位 |
|---|---|---|
| 阿里 `qwen-audio-3.1-asr-flash-streaming`（当前主力） | 按 Token（输入 6 元/百万、输出 4.5 元/百万） | token 级，未标注最小门槛 |
| 阿里 `qwen3-asr-flash-realtime` / `fun-asr-realtime`（老款流式） | 按音频时长 | 到秒（0.00033 元/秒） |
| 豆包 `asr-2.0`（流式/非流式） | 按时长 | **毫秒级**（精确至毫秒、去尾折算小时） |
| 阶跃 `stepaudio-2.5-asr(-stream)` | 按时长（0.15 / 1.2 元/时） | 未见最小门槛说明 |
| 腾讯 实时 / 录音文件极速版 / 录音文件大模型2.0 | 按时长 | **最低 1 秒，不足 1 秒按 1 秒计** |
| 百度 短语音标准版 / 极速版 | 按次 | 按次（无时长下限，硬限 ≤60s） |
| Groq whisper | 按音频秒 | 最短按 10s 计费 |
| 硅基 SenseVoiceSmall | 长期免费 | — |

**统一按秒换算（2026-10，按时长/按 Token 模型折算为「元/秒」横向比价）：**

| 模型 | 计费口径 | 折算元/秒 | 折算元/时 |
|---|---|---|---|
| 硅基 SenseVoiceSmall | 长期免费 | 0 | 0 |
| 阶跃 `stepaudio-2.5-asr` | 0.15 元/时 | 0.000042 | 0.15 |
| 阿里 `qwen-audio-3.1-asr-flash-streaming`（按 Token） | 6 / 4.5 元/百万 | **0.00026**（40 tok/s）／ **0.00017**（25 tok/s） | ≈0.94 ／ ≈0.61 |
| 阿里 `qwen3-asr-flash` | 0.00026 元/秒 | 0.00026 | 0.94 |
| 阿里 `qwen-audio-3.0-asr-flash-streaming` | 0.00033 元/秒 | 0.00033 | 1.19 |
| 阶跃 `stepaudio-2.5-asr-stream` | 1.2 元/时 | 0.000333 | 1.2 |
| 腾讯 录音文件大模型2.0 | 0.8 元/时 | 0.000222 | 0.8 |
| 腾讯 录音文件极速版 | 3.1 元/时 | 0.000861 | 3.1 |
| 豆包 `asr-2.0` | 4.5 元/时 | 0.00125 | 4.5 |
| 百度 短语音标准版 | 按次 0.0034 元/次 | 短音频等效 0.0034/t 元/秒（t≤60s） | — |
| Groq whisper-large-v3 / turbo | $0.111 / $0.04 每时（×7 → **0.777 / 0.28 元/时**） | **0.000217 / 0.000077** | 0.777 / 0.28 |

**按 Token 模型的秒级折算规则（输入 + 输出合计）：**
- 输入（音频）：按 **40 token/秒** 折算（保守口径 25 token/秒）；
- 输出（转写文本）：按普通话口语 **≈4.5 字/秒 ≈ 4.5 token/秒** 折算（≈270 字/分钟）；
- 公式：`元/秒 = (输入token/秒 × 输入单价 + 输出token/秒 × 输出单价) / 1,000,000`；
- 例：`qwen-audio-3.1-asr-flash-streaming`（6/4.5 元/百万）→ 40 档：(40×6 + 4.5×4.5)/100万 ≈ **0.00026 元/秒 ≈ 0.94 元/时**；25 档 ≈ **0.00017 元/秒 ≈ 0.61 元/时**。输出 token 约占总费用 8%，输入（音频）是大头。
- 注意：折算依赖 token 速率假设（40/25），正式接入后建议用实际用量回验。
- 美元刊例（Groq）按固定汇率 **1 USD = 7 CNY** 折合后再与人民币刊例横比（粗略，非实时牌价）。$0.111/时 → 0.777 元/时（≈0.000217 元/秒）；turbo $0.04/时 → 0.28 元/时（≈0.000077 元/秒）。档案里仍存美元单价，比价时再乘 7。

---

## 三、路由策略（模型 ID，不覆盖 auto）

总表 10 个模型都进入算法候选（服务商未启用则不会被实际调用）。
先探测时长 `t`（WAV 读头，否则 ffprobe），用于**硬限排除**和**刊例比价**；失败则不做时长过滤。
策略失败后仍按排序后的名单依次尝试（与容灾相同）。

| 请求 model | 行为 |
|---|---|
| `auto` / `服务商/auto` / `服务商/模型` | **不变**：容灾或定点调用，不被策略改写 |
| `strategy-cost` | 费用优先：只比刊例付费价 |
| `strategy-speed` | 速度优先：S1→S5 |
| `strategy-accuracy` | 准确度优先：静态等级 |
| `strategy-free` | 免费策略：只在长期免费 / 带额度的模型中，**保持配置顺序** |
| `strategy-free+speed` / `strategy-free+accuracy` | 先限定免费候选，再按速度或准确度排序 |

`strategy-free` 可与速度/准确度组合；不与费用优先组合成同一个 ID（免费不进入费用比价）。

**免费与费用拆开：**
- 长期免费、免费额度**不是** `strategy-cost` 的优势项；硅基不会因为「免费」赢过阶跃的刊例价。
- 硅基 SenseVoice 没有刊例价，只出现在 `strategy-free`（及其组合），不进入 `strategy-cost`。
- 既有额度又有刊例价的（阿里、豆包、腾讯、百度、Groq）可同时出现在 `strategy-free` 与 `strategy-cost`：前者把它们当免费候选，后者只用刊例价。
- 额度尽量查询；查不到则看调用异常再切下一个；无法感知则视为仍免费。

### 1. 硬限（只排除，不构成优选档）

没有厂商硬限、也没有落在双方可用区间内的临界时长，就不切档（因此不存在「30min 改走腾讯」这类经验线）。

| 条件 | 排除 |
|---|---|
| t > 60s | 百度 `1537` / `80001` |
| t > 2h 或文件 >100MB | 腾讯极速版 `16k_zh_en` |
| 文件 >25MB | Groq Whisper |
| 代理未实现该通道 | 阿里 filetrans（`supported=False`） |

硅基「建议 ≤5min」、 Groq 最短按 10s **计费**，都不是硬拒，不单开时长档。
<1s 音频识别不稳定，建议垫静音或合并片段，同样不是厂商硬限。

### 2. 费用优先 `strategy-cost`（刊例价）

给定时长 t：按次费用 = P，按时长费用 = S × max(t, 最短计费秒)；**取低者**。无刊例价（`billing=free`）的模型跳过。美元刊例（Groq）按固定汇率 **1 USD = 7 CNY** 折合人民币后再比价（粗略）。

**刊例从低到高（人民币，与时长无关的单价序；美元已按 1:7 折合）：**

| 序 | 模型 | 刊例 |
|---|---|---|
| 1 | 阶跃 `stepaudio-2.5-asr` | 0.15 元/时 |
| 2 | Groq `whisper-large-v3-turbo` | $0.04/时 ×7 ≈ **0.28 元/时** |
| 3 | Groq `whisper-large-v3` | $0.111/时 ×7 ≈ **0.777 元/时** |
| 4 | 腾讯录音文件·大模型2.0 `16k_zh_en_2.0` | 0.8 元/时 |
| 5 | 阿里 3.1 流式（40 tok/s 折算）/ 3.0 flash | ≈0.94 元/时 |
| 6 | 阿里老款流式 | 1.19 元/时 |
| 7 | 阶跃 `stepaudio-2.5-asr-stream` | 1.2 元/时 |
| 8 | 腾讯极速版 `16k_zh_en` | 3.1 元/时起 |
| 9 | 豆包 `asr-2.0` | 4.5 元/时 |
| 10 | 百度短语音 | 0.0034 元/次（须 t≤60s） |

候选集含阶跃时，刊例第一名始终是阶跃：它比表内所有按时长模型便宜（含 Groq 折合价），且与百度的临界时长见下节，落在百度硬限之外。

### 3. 临界时长（仅按次 vs 按秒，且 t\* 落在双方硬限内才切档）

t\* = P / S。按次基准 P = 百度标准版 0.0034 元/次（极速版刊例未单列，比价暂同此值）。**t < t\* 按时长更省，t > t\* 按次更省。**

| 对比的按时长模型 | S（元/秒） | t\* | 是否成档 |
|---|---|---|---|
| 阶跃 `stepaudio-2.5-asr` | 0.000042 | ≈82s | **不成档**（>60s，百度已硬排除）→ 双方都能用时阶跃恒更省 |
| Groq turbo（$0.04×7） | 0.000077 | ≈44s | 仅当候选不含更便宜的按时长模型（无阶跃）时：t<44s Groq turbo 更省，44s–60s 百度更省（Groq 最短按 10s 计，短于 10s 仍按 10s） |
| Groq `whisper-large-v3`（$0.111×7） | 0.000217 | ≈16s | 同上 |
| 腾讯大模型2.0 | 0.000222 | ≈15s | 仅当候选**不含**更便宜的按时长模型（无阶跃）时：t<15s 腾讯异步更省，15s–60s 百度更省 |
| 阿里 3.1（40 tok/s） | 0.00026 | ≈13s | 同上，无阶跃时：t<13s 阿里更省，13s–60s 百度更省 |
| 阶跃流式 | 0.000333 | ≈10s | 同上 |
| 腾讯极速版 | 0.000861 | ≈4s | 同上 |
| 豆包 `asr-2.0` | 0.00125 | ≈3s | 同上 |

实现：对当前候选做 `min(P, S×t)`，不必预存档表。上表只供人工核对。

10 秒刊例（元，Groq 已按 1:7 折合且适用 10s 最低计费）：阶跃 0.00042 ＜ Groq turbo 0.00077 ＜ Groq v3 0.00217 ＜ 阿里 3.1 0.0026 ＜ 百度按次 0.0034 ＜ 腾讯极速 0.0086 ＜ 豆包 0.0125。

### 4. 免费策略 `strategy-free`

候选：总表中长期免费或带免费额度者——硅基 SenseVoice；阿里（90 天额度）；豆包 20h；Groq 免费层；腾讯月额度；百度次数包。阶跃无长期免费，不进本策略。

单独 `strategy-free`：排除硬限后**保持配置顺序**（`auto_order` × 各服务商 `models` 列表）。
`strategy-free+speed` / `strategy-free+accuracy`：同一免费集合上再排速度或准确度。

额度：能查就查；查不到则调用失败（含额度耗尽类错误）后试下一个；无法感知则当作仍免费。

### 5. 速度 `strategy-speed` / 准确度 `strategy-accuracy`

在硬限过滤后的全集上排序（含付费与免费）。要「只在免费里选最快/最准」用组合 ID。
Groq 为海外高质量备选（常需代理 `127.0.0.1:7881`），可进 `strategy-speed` / `strategy-accuracy` / `strategy-free` / `strategy-cost`（美元按 1:7 折合）。

---

## 四、平台信息（单独列出）

### 1. 阿里云百炼 DashScope（现有）
- 产品/控制台：https://bailian.console.aliyun.com/
- 模型定价：https://help.aliyun.com/zh/model-studio/model-pricing
- 鉴权：`Authorization: Bearer <DASHSCOPE_API_KEY>`
- 免费额度：ASR 各模型多为 **36000 秒(10h)，开通后 90 天有效**；新用户另有 10 元免费试用（3 个月）
- 计费：`qwen-audio-3.1-asr-flash-streaming` **按 Token 计费**（输入 6 元/百万、输出 4.5 元/百万，2026-10 价页；旧"按秒"口径已失效，以控制台为准）；老款流式/实时按音频秒计费：realtime 0.00033 元/秒，`qwen3-asr-flash` 0.00026 元/秒，filetrans 0.00022 元/秒
- 开通准备：阿里云账号 + 实名认证 → 开通百炼 → 建 API Key
- 备注：项目用环境变量 `DASHSCOPE_API_KEY` / `DASHSCOPE_ASR_MODELS`

### 2. 火山引擎·豆包（现有）
- 产品页：https://www.volcengine.com/product/asr
- 控制台：https://console.volcengine.com/speech
- 模型列表：https://docs.volcengine.com/docs/6561/2499930
- 鉴权：Bearer Token（新版仅 api_key；旧版需 app_id + access_token）
- 免费额度：流式语音识别 **20 小时**、录音文件识别 **20 小时**（一次性）
- 计费：流式识别2.0 **4.5 元/时**；录音文件 AUC 2.0 **0.8 元/时**；极速版 30h 新客包 92.4 元（按时长计费，毫秒级计量，累加每次调用音频时长、去尾折算小时）
- 开通准备：火山账号 + 实名认证 → 开通语音识别 → 取 api_key（旧版再加 app_id）
- 备注：本项目接 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`

### 3. 硅基流动 SiliconFlow（现有）
- 官网/定价：https://siliconflow.cn/pricing
- 控制台（密钥）：https://cloud.siliconflow.cn/account/ak
- 接口文档：https://docs.siliconflow.cn/cn/api-reference/audio/create-audio-transcriptions
- 鉴权：`Authorization: Bearer <SILICONFLOW_API_KEY>`
- 免费额度：`FunAudioLLM/SenseVoiceSmall`、`TeleAI/TeleSpeechASR` **长期免费**
- 计费：上述模型免费；其他多模态模型按 token
- 开通准备：注册 + 实名 → 建 API Key（国内直连，无需代理）

### 4. Groq（现有，海外）
- 官网/定价：https://groq.com/pricing
- 控制台：https://console.groq.com
- 鉴权：`Authorization: Bearer <GROQ_API_KEY>`，OpenAI 兼容
- 免费额度：Free 层 Whisper **2000 次/日、7200 秒/时、28800 秒/日**，单次文件 ≤25MB，最短按 10s 计
- 计费：whisper-large-v3 **$0.111/时**，turbo **$0.04/时**；费用策略按 **1 USD = 7 CNY** 粗算（≈0.777 / 0.28 元/时）
- 开通准备：注册 Groq（海外）→ API Key；**国内访问需代理**（项目配 `http://127.0.0.1:7881`）

### 5. 阶跃星辰 StepFun（新增）
- 开放平台：https://platform.stepfun.com/
- 定价页：https://platform.stepfun.com/docs/zh/guides/pricing/details
- 转写接口：`POST /v1/audio/transcriptions`（OpenAI 风格）
- 鉴权：`Authorization: Bearer <API_KEY>`（单一密钥，最简单）
- 免费额度：无长期免费，新号以控制台赠送为准
- 计费：**`stepaudio-2.5-asr` 0.15 元/时**；流式 `stepaudio-2.5-asr-stream` 1.2 元/时；2-asr-pro 2 元/时；3-asr-max 2.8 元/时（均按时长计费，官方未标注最小计费单位）
- 限速：个人 V0（充值<100 元）5 并发 / 100 RPM；充 100 元升 20 并发 / 400 RPM
- 开通准备：注册 + 实名 → 建 API Key → 充值（不充也能用 V0）

### 6. 腾讯云（新增）
- 产品页：https://cloud.tencent.com/product/asr
- 控制台：https://console.cloud.tencent.com/asr
- 计费文档：https://cloud.tencent.com/document/product/1093/35686
- 鉴权：TC3-HMAC-SHA256 签名，需 **SecretId / SecretKey / AppID**
- 免费额度（每月）：录音文件极速版 5h、录音文件 10h、实时 5h、一句话 5000 次
- 计费：
  - 录音文件极速版（同步）按识别时长计费，**3.1 元/时起**，当日用量越大越便宜（最低 1.3 元/时），日结
  - 录音文件·大模型2.0（异步）**0.8 元/时**
  - 实时 3.2 元/时起（大模型 1.0 元/时）
  - 按时长计费的产品**最低计费单位为 1 秒**（不足 1 秒按 1 秒计），实时 / 录音文件 / 极速版通用
- 开通准备：注册 + 实名 → 控制台一键开通五子产品（自动发额度）→ [CAM 建密钥](https://console.cloud.tencent.com/cam/capi) → 免费用完**手动开后付费**或买资源包

### 7. 百度智能云（新增）
- 语音技术首页：https://cloud.baidu.com/product/speech.html
- 控制台：https://console.bce.baidu.com/ai-engine/speech/overview/index
- 短语音文档：https://cloud.baidu.com/doc/SPEECH/s/Jlbxdezuf
- 鉴权：应用三件套 **AppID / API Key / Secret Key** → OAuth 换 **access_token**（约 30 天）
- 免费额度：短语音标准版（1537）约 **200 万次**；极速版（80001）**5 万次**；均长期有效
- 计费：标准版 0.0034 元/次起（月量大可低至 0.0011 元/次）；次数包 100 万次 2400 元；并发扩容 80 元/月或 800 元/年
- 开通准备：注册百度智能云 + 实名 → 语音控制台开通并领免费额度 → 建应用取三件套
- 硬限制：短语音接口单次 **≤60 秒**；长文件用「音频文件转写」（异步）

---

## 五、数据来源
- 阿里云百炼模型价格：https://help.aliyun.com/zh/model-studio/model-pricing ；`qwen-audio-3.1-asr-flash-streaming` 模型页（按 Token 计费）：https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-asr-flash-streaming
- 阶跃定价：https://platform.stepfun.com/docs/zh/guides/pricing/details
- 腾讯云 ASR 计费（含"按时长产品最低计费单位 1 秒"）：https://cloud.tencent.com/document/product/1093/35686
- 豆包语音模型/定价：https://docs.volcengine.com/docs/6561/2499930 、https://www.volcengine.com/product/asr ；豆包语音计费说明（毫秒级计量）：https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh
- 硅基流动定价：https://siliconflow.cn/pricing
- Groq 定价：https://groq.com/pricing
- 百度 SPEECH 文档中心：https://cloud.baidu.com/doc/SPEECH/index.html ；短语音标准版 API：https://cloud.baidu.com/doc/SPEECH/s/Jlbxdezuf ；计费：https://cloud.baidu.com/doc/SPEECH/s/Tldjm0i4c
