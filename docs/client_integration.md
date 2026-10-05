# 客户端对接说明（闪电说与其他调用方）

> 用途：说明本代理对外协议、与「实时语音识别 WebSocket」的关系，以及给其他客户端选型时的建议。  
> 面向：本机/内网部署后，把本工具提供给闪电说或其它翻译/会议类软件使用。  
> 相关：接口一览见根目录 [README.md](../README.md)；上游模型与计费见 [asr_models_guide.md](asr_models_guide.md)。

---

## 一、结论（先看这个）

1. **本代理给客户端的是「请求一次 → 返回全文」**，不是「麦克风直连上游实时 WS」。
2. **闪电说**走自定义服务商协议：`POST /chat/completions` + JSON base64 音频，成功时必须把识别结果放在 `choices[0].message.content`。
3. 上游即便用阿里/豆包/腾讯的 **WebSocket**，在代理内也多是「整段切块上传 → 收齐全文 → HTTP 一次返回」。对闪电说这类客户端，这就是正确形态。
4. **腾讯「实时语音识别」**（`wss://asr.cloud.tencent.com/asr/v2/<appid>`）**不能**当现成通道直接给闪电说接；密钥可复用，但协议是另一套，且与极速版 Flash 的每月免费额度**不共用**。做「切段文件转写」时 Flash 通常更快；实时 WS 更适合真正边说边出字的客户端。
5. 若以后把本工具提供给**其他客户端**：先确认对方是「整段/切段收齐」还是「流式增量出字」，再决定是否要新增对外流式接口（当前未提供客户端侧 SSE/WS 出字）。
6. **没有**像闪电说那样普及、又能填「自定义流式 ASR 地址」接到本代理的大众客户端。FunASR Runtime / WhisperLive 是本地流式**引擎服务**，不是闪电说的平替（见第八节）。

---

## 二、两种延迟模型（不要混）

| 形态 | 谁在切音频 | 客户端看到什么 | 典型场景 |
|---|---|---|---|
| **A. 切段请求-响应**（本代理现状） | 客户端（如闪电说按静音/时长切段） | 每次 HTTP 拿到该段**全文** | 实时翻译软件、自定义 ASR 服务商 |
| **B. 真·流式推字** | 客户端持续推 PCM，服务端增量回字 | WS/SSE 上不断出现 partial/final | 麦克风字幕、会议同传原生 SDK |

闪电说属于 **A**。上游「实时引擎」若只在代理内部包一层、仍一次性返回全文，对客户端仍是 A，不会自动变成 B。

```
形态 A（本仓库对闪电说）：

  [麦克风] → 闪电说切段 → POST /chat/completions
                              ↓
                         本代理（选上游）
                              ↓
                    Flash / 非流式WS / 流式WS收齐
                              ↓
                    { choices:[{ message:{ content } }] }
```

```
形态 B（本仓库尚未对客户端暴露）：

  [麦克风] → 客户端 ⇄ wss/SSE ⇄ 代理 ⇄ 上游实时 ASR
                    （边说边出字）
```

---

## 三、闪电说对接（已支持）

### 3.1 面板填写

| 字段 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8004`（带不带 `/v1` 均可） |
| API Key | 任意（本地默认无鉴权；勿把服务裸暴露到公网） |
| 模型 | `auto` / `strategy-speed` / `tencent/16k_zh` / `aliyun/...` 等，见 `GET /v1/models` |

### 3.2 真实协议

面板**不走** OpenAI Whisper 的 multipart，而是：

```http
POST /chat/completions
Content-Type: application/json

{
  "model": "auto",
  "messages": [{
    "role": "user",
    "content": [{
      "type": "input_audio",
      "input_audio": { "data": "<base64 或 data:audio/...;base64,...>" }
    }]
  }]
}
```

成功响应（结构必须满足，否则客户端报「未返回有效文本」）：

```json
{
  "choices": [
    { "message": { "content": "识别出的文本" } }
  ]
}
```

失败应返回真实 HTTP 错误码（如 400/502）及 `error` 字段，而不是 `200` + 空文本。

### 3.3 兼容路径（给非闪电说客户端）

| 路径 | 用途 |
|---|---|
| `POST /v1/chat/completions`（别名 `/chat/completions`） | 闪电说主路径 |
| `POST /v1/audio/transcriptions`（别名 `/audio/transcriptions`） | OpenAI 风格 multipart，返回 verbose_json |
| `GET /v1/models`、`GET /health` | 模型列表与健康检查 |

其他客户端若已支持 OpenAI Whisper API，优先对接 `/v1/audio/transcriptions`；若协议与闪电说相同，则复用 `/chat/completions`。

---

## 四、上游「实时 WS」与客户端协议的关系

### 4.1 腾讯云（示例）

| 子产品 | 接口形态 | 本代理角色 | 与闪电说 |
|---|---|---|---|
| 录音文件识别极速版 Flash | HTTP | 直接转写，速度快（约 S1） | **推荐**用于切段转写 |
| 实时语音识别 | `wss://asr.cloud.tencent.com/asr/v2/<appid>`，约 1:1 实时率切 ~200ms 包，间隔 ≤6s，结束发 `{"type":"end"}` | 可包成「整段文件切块 → 收齐全文」，需独立握手/分片/收包逻辑 | 能用，但通常**更慢**，且消耗**另一份**每月约 5 小时额度（与 Flash 包不通用） |
| 大模型实时引擎 | 实时 WS | 同上；**无**上述实时月免费包 | 同左 |

密钥（`secret_id` / `secret_key` / `app_id`）可与 Flash 复用，**接口与计费账本不复用**。

### 4.2 阿里 / 豆包（现状共性）

- 代理侧常见做法：整段（或切块）走 WebSocket，**收齐全文再**通过 `/chat/completions` 返回。
- 对闪电说：这与 Flash HTTP 一样，都是形态 A；差别主要在速度、价格、断句与额度。

### 4.3 选型建议（给「实时翻译」类软件）

| 目标 | 建议 |
|---|---|
| 闪电说等「切段 → 全文」 | 优先快模型：`strategy-speed`、腾讯 Flash、阶跃、阿里 streaming（代理内收齐）等；**不必**为了「实时」强上腾讯实时 WS |
| 降低体感延迟 | 客户端缩短单次切段 / 静音阈值；代理侧选 S1–S3 模型；用 `auto` 做容灾 |
| 花掉腾讯「实时」月免费包 | 仅在代理已实现 realtime 包装、且可接受约 1:1 耗时，或客户端本身是形态 B 时再考虑 |
| 其他客户端要边说边出字 | 需**新增**对外流式 API（见第五节、第八节）；仅改 config 无法把现有 HTTP 接口变成 B；市面上也几乎没有「填本代理地址就能真实时」的现成 App |

---

## 五、提供给其他客户端时的检查清单

在分发本工具前，向对方确认：

1. **协议**
   - [ ] 支持闪电说同款 `/chat/completions` + base64？
   - [ ] 或支持 OpenAI `/audio/transcriptions` multipart？
   - [ ] 是否要求流式增量（SSE/WS）？——当前**不支持**对外推字，需要新开发。
2. **部署**
   - [ ] 默认监听 `127.0.0.1`，仅本机；局域网访问需改 `server.host` 并自行做鉴权/防火墙。
   - [ ] 依赖：Python、ffmpeg（部分上游转 16k mono PCM）、各厂商密钥见 `config.example.yaml`。
3. **模型 ID**
   - [ ] 使用 `GET /v1/models` 或 `skills/asr-openai-proxy/scripts/report_status.py` 列出可用 ID。
   - [ ] `auto`：服务商容灾；`strategy-*`：费用/速度/免费等策略（不覆盖显式 `服务商/模型`）。
4. **预期延迟**
   - [ ] 端到端 ≈「客户端切段时长 + 上游转写耗时 + 网络」，不是上游实时引擎的出字延迟。
   - [ ] 勿向用户承诺「接了实时 WS 就会边说边出字」，除非已实现形态 B。

### 5.1 若要做形态 B（对外流式）——尚未实现

大致需要单独设计，与现有闪电说路径并行，例如：

- 对外：`wss://<host>/v1/audio/realtime`（或 SSE），定义音频帧与 partial/final 文本事件；
- 对内：映射到腾讯实时 / 阿里 realtime / 豆包流式等；
- 鉴权、心跳、断线重连、采样率与编码约定。

在未做上述接口前，**不要**把上游实时 URL 或密钥协议直接交给第三方客户端当「本代理能力」宣传。

若以后做对外实时，协议对齐哪边会影响能接上哪些客户端：

| 对外协议 | 更容易接上的一侧 | 代价 |
|---|---|---|
| OpenAI Realtime transcription | 海外/开发者生态（自写前端、部分 Realtime Demo） | 国内翻译 App 几乎不认 |
| 腾讯 / 阿里原生实时 WS | 官方 Demo、国内 SDK | 绑死一家，本代理路由优势变弱 |
| FunASR Runtime WS | OBS Auto Subtitle 等已接 FunASR 的工具 | 与本仓库云厂商路由无关 |
| 自研事件协议 | 完全可控 | 必须自写客户端 |

---

## 六、与文档其它章节的边界

| 文档 | 管什么 |
|---|---|
| 本文 | 客户端协议、形态 A/B、实时 WS 是否值得接、FunASR/WhisperLive 与闪电说差异、分发检查清单 |
| [README.md](../README.md) | 安装、配置、闪电说面板字段、策略模型 ID、接口清单 |
| [asr_models_guide.md](asr_models_guide.md) | 各上游模型价格、额度、速度等级、是否已接入 |
| [adr/0001-strategy-as-model-id.md](adr/0001-strategy-as-model-id.md) | 策略以 model ID 暴露的设计决策 |

---

## 七、有没有适合「真实时」接入的现成客户端？

**基本没有能填本代理地址就边说边出字的大众软件。** 能做形态 B 的，多半直连云厂商或本地引擎，协议与本仓库现状对不上。

| 类型 | 典型 | 能否接本代理做真实时 |
|---|---|---|
| 自定义 ASR 服务商（HTTP 切段） | 闪电说 | ❌ 形态 A；这是本代理**已支持**的路径 |
| OpenAI Whisper HTTP | 部分录音/字幕工具 | ⚠️ 可走 `/v1/audio/transcriptions`，仍是切段/整段，不是推字 |
| 云厂商控制台 / 官方 Demo / SDK | 腾讯、阿里、讯飞、百度实时 WS | ✅ 真实时，但是**直连上游**，绕过本代理 |
| 开源流式服务 + 配套客户端 | FunASR Runtime、WhisperLive、OBS Auto Subtitle | ✅ 有 WS，但是**各自协议**；不是本代理的客户端 |
| OpenAI Realtime 系 | Realtime transcription Demo | ✅ 要 `wss://.../realtime` 一类事件；本代理**未实现** |

桌面「实时翻译」里，常见可换引擎的仍是切段 HTTP。没有像闪电说那样普及、又支持「自定义流式 ASR 地址」的产品可直接当本代理的形态 B 客户端。

真要「边说边出字」，现实路径只有：

1. **客户端直连云厂商实时 WS**（腾讯/阿里等）——能用上实时月额度，但没有多厂商路由。
2. **接本地流式引擎**（FunASR Runtime / WhisperLive）——和本项目无关的另一条栈。
3. **本代理新增对外流式接口**，再找/写对应客户端——当前未做。

---

## 八、闪电说 vs FunASR Runtime vs WhisperLive

三者**定位不同**：闪电说是面向用户的翻译 App；后两者是本地实时转写**服务端**，角色更接近云厂商 ASR 或本代理的上游，而不是闪电说的替代品。

| | 闪电说 | FunASR Runtime | WhisperLive |
|---|---|---|---|
| **是什么** | 桌面实时翻译 / 同传 **客户端** | 开源 ASR **推理服务**（达摩院 FunASR 系） | 近实时 Whisper **流式服务** |
| **你装它干什么** | 听/说 → 出译文 | 本机起 WS，给别的程序推音频、收回文字 | 同上，偏 Whisper 流式转写 |
| **识别引擎** | 调外部 API（可接本代理） | 本地模型（Paraformer 等，中文强） | 本地 / 自建 Whisper |
| **交互形态** | 切段 HTTP + 客户端自己翻译 | 真·流式 WS（可 2pass 等） | 真·流式 WS，增量出字 |
| **和本代理** | **已对接的客户端** | 并行的本地上游方案，协议不兼容本仓库 HTTP | 同上 |

关系可以记成：

```
形态 A：  [麦克风] → 闪电说 → 本代理 → 云厂商 ASR → 闪电说再翻译

形态 B（不经本代理）：  [麦克风] → OBS/自写前端 → FunASR 或 WhisperLive
形态 B（直连云）：      [麦克风] → 官方 SDK/Demo → 腾讯/阿里实时 WS
```

FunASR / WhisperLive **不能**替代闪电说；要用真实时，通常是「字幕/自写前端 → 这些引擎」，而不是再找一个「另一个闪电说」。OBS Auto Subtitle 等已能连 FunASR，那是引擎生态，不是本代理生态。

参考（上游文档，非本仓库能力）：

- FunASR Runtime：<https://github.com/modelscope/FunASR/blob/main/runtime/docs/SDK_tutorial_online.md>
- WhisperLive：<https://github.com/collabora/WhisperLive>

---

## 九、修订记录

| 日期 | 说明 |
|---|---|
| 2026-10 | 初版：整理闪电说协议、腾讯实时 WS 与 Flash 差异、形态 A/B 及对他客户端分发注意点 |
| 2026-10 | 补充：市面几乎无「自定义流式地址」可接本代理的客户端；对比闪电说 / FunASR Runtime / WhisperLive |
