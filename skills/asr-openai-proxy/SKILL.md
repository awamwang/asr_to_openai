---
name: asr-openai-proxy
description: 配置、启动与使用本项目的 ASR→OpenAI 兼容语音识别代理（asr_to_openai，主入口 main.py）。当用户需要（1）配置/生成 config.yaml 或某家语音服务商密钥（阿里云百炼、Groq、豆包/火山、硅基流动、阶跃星辰、腾讯云、百度智能云）；（2）校验当前配置并给出可用的模型 ID 列表（auto / strategy-* / 服务商/模型）；（3）启动本地服务或给出开机自启动（Windows 任务计划/nssm/启动文件夹）方案；（4）查询本地启动后的接口路径（/health、/v1/models、/audio/transcriptions、/chat/completions）并接入闪电说时使用。
---

# ASR → OpenAI 代理：配置 / 启动 / 自启动 / 模型与接口

把多厂商语音识别桥接为闪电说「自定义语音识别服务商」（`POST /chat/completions` + JSON base64）。
**主场景**：实时翻译类客户端的短切段识别（数秒～约 1 分钟），不是几十分钟整篇录音的离线长转写。
项目根：本仓库根目录（本技能位于 `<项目根>\skills\asr-openai-proxy\`）。

## 工作流

### 1. 判断任务类型

| 用户意图 | 动作 |
|---|---|
| 配置 / 生成 config.yaml / 填某家密钥 / 解释配置项 | → 步骤 2（读 `references/configuration.md`） |
| 启动服务 / 设开机自启 / 后台运行 | → 步骤 3（读 `references/startup.md`） |
| 当前有哪些可用模型 ID / 接口路径 / 哪些服务商已启用 | → 步骤 4（直接跑脚本，读真实配置） |

### 2. 配置（读 references/configuration.md）

- 生成：`Copy-Item config.example.yaml config.yaml`，然后编辑 `config.yaml` 填密钥。
- 启用规则：阿里/Groq/豆包/硅基/阶跃看 `api_key`；腾讯看 `secret_id`+`secret_key`（另需 `app_id`）；百度看 `api_key`+`secret_key`（另需 `app_id`）。**密钥完备 = 启用**。
- 服务商别名归一：`grok`→`groq`，`volcengine/volc`→`doubao`，`silicon/sf`→`siliconflow`，`step`→`stepfun`，`tencentcloud/qcloud`→`tencent`。
- 环境变量可覆盖 YAML（如 `DASHSCOPE_API_KEY`、`GROQ_API_KEY`、`DOUBAO_API_KEY`、`SILICONFLOW_API_KEY`、`STEPFUN_API_KEY`、`TENCENT_SECRET_ID/KEY`、`BAIDU_API_KEY/SECRET_KEY`；通用 `ASR_PORT`/`ASR_LOG`/`ASR_LOG_LEVEL`/`FFMPEG_PATH`）。
- 各家控制台入口、models 字段、流式断句等细节全部在 `references/configuration.md`，按需读取对应小节。

### 3. 启动 / 自启动（读 references/startup.md）

```powershell
cd <项目根>
python main.py          # 前台启动；验证 curl.exe http://127.0.0.1:8004/health
```

自启动建议（按推荐顺序）：
1. **任务计划程序 AtLogOn**（推荐）：`Register-ScheduledTask`，`-WorkingDirectory` 必须指向项目根；要隐藏窗口把 `Execute` 换 `pythonw`。
2. 启动文件夹快捷方式（最简单，登录即起）。
3. **nssm 注册为系统服务**（最稳：开机即服务、无需登录、崩溃自动重启）——命令见 `references/startup.md`。
4. 注意：本地服务无鉴权，`server.host` 默认 `127.0.0.1`，不要随意暴露到局域网。

### 4. 输出可用模型 ID 与接口路径（跑脚本 / CLI，读真实配置）

```powershell
python <项目根>\skills\asr-openai-proxy\scripts\report_status.py
# 或 --json
```

也可用主入口 CLI（输出可用模型 ID 列表后退出，不启动服务）：
```powershell
cd <项目根>
python main.py --models        # 别名 --list-models
```

脚本/CLI 读取实际 `config.yaml`（含环境变量覆盖），输出：
- 配置来源、监听地址（`http://host:port`）；
- **已启用服务商**及其模型列表（密钥完备才算启用）；
- **可用模型 ID**（排序：最上边为 `auto`、`strategy-cost/speed/accuracy/free/free+speed/free+accuracy`，随后按 `auto_order` 的服务商顺序，每家依次列裸服务商名、`服务商/auto`、全部 `服务商/模型`）；
- **接口路径**：`GET /health`、`GET /v1/models`（别名 `/models`）、`POST /v1/audio/transcriptions`（别名 `/audio/transcriptions`，multipart）、`POST /v1/chat/completions`（别名 `/chat/completions`，闪电说主路径）。

模型 ID 语义（用于请求 `model` 字段）：
- `auto`：服务商容灾切换（按 `auto_order` + 上次成功优先）
- **裸服务商名**（如 `aliyun`）：定点调用该服务商「优先模型」（上次成功 > 配置首项），只试一个、失败即整体失败，不做服务商内切换；等价于 `服务商/<优先模型>` 的简写，**不是** `服务商/auto`
- `服务商/auto`：只在该服务商 models 列表内切换（上次成功模型开始轮转）
- `服务商/模型`：定点调用（如 `aliyun/qwen-audio-3.1-asr-flash-streaming`）
- `strategy-cost`：费用优先（刊例价）；`strategy-speed`/`strategy-accuracy`：速度/准确度；`strategy-free(/+speed/+accuracy)`：免费/额度候选。策略**不覆盖** auto 与显式 ID。

**裸服务商名 vs `服务商/auto` 怎么选**：
- 认死一家不换模型 → **裸名**（`aliyun`）；失败即报错，不换模型
- 一家内部做模型容灾（首模型失败自动换下一个）→ **`服务商/auto`**（`aliyun/auto`）
- 跨服务商容灾 → **`auto`**
- 全局按指标挑最优 → **`strategy-cost/speed/accuracy/free(/+speed/+accuracy)`**

> 一句话记忆：裸名 = 认死一家不换模型；`服务商/auto` = 一家内部换模型；`auto` = 换服务商；`strategy-*` = 全局按指标挑。

闪电说面板填写：地址 `http://127.0.0.1:8004`（带不带 `/v1` 均可），API Key 随便填，模型用上面脚本输出的 ID。

## 资源

- `references/configuration.md` — 生成 config.yaml、7 家服务商配置细则、环境变量、校验方法、常见问题
- `references/startup.md` — 前置条件、前台/后台启动、自启动四种方案与验证清单、闪电说对接
- `scripts/report_status.py` — 确定性输出：按真实配置给出启用服务商、可用模型 ID、接口路径（`--json` 可给结构化输出）

## 注意事项

- 服务商启用判定发生在**配置加载时**：改了 config.yaml 后必须重启服务才生效。
- 阿里云 / 豆包识别前用 `ffmpeg` 转 16kHz 单声道 PCM（`ffmpeg_path` 可配）。
- Groq 海外访问通常需 `proxy`（`http://127.0.0.1:7881` 或 `socks5h://...`，socks5:// 常 TLS 失败）。
- 失败响应使用真实 HTTP 错误码（如 502），不是 200+空文本。
- 密钥属敏感信息：默认不落盘可改用环境变量；日志级别 `debug` 会输出逐步尝试细节，排查完建议回 `info`。
