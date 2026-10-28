# 阿里云百炼（DashScope）语音识别 ASR 模型清单

> 数据来源：通过你的 API Key 实时查询百炼模型列表接口（`GET https://dashscope.aliyuncs.com/api/v1/models`），
> 全量 518 个模型中筛选出**真正可用于语音识别（ASR / Realtime-ASR）**的模型。
> 原始全量数据：本地导出的 `bailian_all_models.json`（518 条），未随仓库提供。

## 说明
- **Realtime-ASR（流式）**：实时识别，适合麦克风实时字幕（闪电说实时语音输入）。
- **ASR（非流式 / flash）**：录音文件 / 整段音频识别，适合我们代理的 `/audio/transcriptions` 文件转写。
- capabilities 字段含 `ASR` 或 `Realtime-ASR` 才算纯语音识别；TTS / Chatting / Translate / Omni / 图像 / LLM 已剔除。

---

## 一、Realtime-ASR（流式实时识别）—— 适合闪电说麦克风

| 模型 ID | 名称 | 备注 |
|---|---|---|
| qwen-audio-3.1-asr-flash-streaming | Qwen-Audio-3.1-ASR-Flash-Streaming | **当前代理在用**，效果最佳，支持多语种/方言/标点 |
| qwen-audio-3.0-asr-flash-streaming | Qwen-Audio-3.0-ASR-Flash-Streaming | 上代版本 |
| qwen3-asr-flash-realtime | Qwen3-ASR-Flash-Realtime | **闪电说原生阿里云服务商用的就是这个**（日志确认连 `.../realtime?model=qwen3-asr-flash-realtime`）|
| qwen3-asr-flash-realtime-2025-10-27 | Qwen3-ASR-Flash-Realtime-2025-10-27 | 快照 |
| qwen3-asr-flash-realtime-2026-02-10 | Qwen3-ASR-Flash-Realtime-2026-02-10 | 快照 |
| fun-asr-realtime | Fun-ASR 实时语音识别 | |
| fun-asr-realtime-2025-09-15 | Fun-ASR 实时-2025-09-15 | |
| fun-asr-realtime-2025-11-07 | Fun-ASR 实时-2025-11-07 | |
| fun-asr-realtime-2026-02-28 | Fun-ASR 实时-2026-02-28 | |
| fun-asr-flash-8k-realtime | Fun-ASR-Flash-8k 实时 | 8kHz 电话场景 |
| fun-asr-flash-8k-realtime-2026-01-28 | Fun-ASR-Flash-8k 实时-2026-01-28 | |
| paraformer-realtime-v1 | Paraformer 实时-v1 | |
| paraformer-realtime-v2 | Paraformer 实时-v2 | |
| paraformer-realtime-8k-v1 | Paraformer 实时-8k-v1 | |
| paraformer-realtime-8k-v2 | Paraformer 实时-8k-v2 | |

## 二、ASR（非流式 / flash）—— 适合录音文件转写

| 模型 ID | 名称 | 备注 |
|---|---|---|
| qwen-audio-3.1-asr-flash | Qwen-Audio-3.1-ASR-Flash | 新一代 flash |
| qwen-audio-3.1-asr-flash-filetrans | Qwen-Audio-3.1-ASR-Flash-Filetrans | 录音文件识别 |
| qwen-audio-3.1-asr-flash-message | Qwen-Audio-3.1-ASR-Flash-Message | |
| qwen-audio-3.0-asr-flash | Qwen-Audio-3.0-ASR-Flash | |
| qwen-audio-3.0-asr-flash-filetrans | Qwen-Audio-3.0-ASR-Flash-Filetrans | |
| qwen3-asr-flash | Qwen3-ASR-Flash | |
| qwen3-asr-flash-2025-09-08 | Qwen3-ASR-Flash-2025-09-08 | |
| qwen3-asr-flash-2026-02-10 | 通义千问3-ASR-Flash-2026-02-10 | |
| qwen3-asr-flash-filetrans | Qwen3-ASR-Flash-Filetrans | 录音文件识别 |
| qwen3-asr-flash-filetrans-2025-11-17 | Qwen3-ASR-Flash-Filetrans-2025-11-17 | |
| fun-asr | Fun-ASR 语音识别 | |
| fun-asr-2025-08-25 | Fun-ASR-2025-08-25 | |
| fun-asr-2025-11-07 | Fun-ASR-2025-11-07 | |
| fun-asr-flash-2026-06-15 | Fun-ASR-Flash-2026-06-15 | |
| fun-asr-mtl | Fun-ASR-MTL | 多语言 |
| fun-asr-mtl-2025-08-25 | Fun-ASR 多语言-2025-08-25 | |
| paraformer-v1 | Paraformer 语音识别-v1 | |
| paraformer-v2 | Paraformer 语音识别-v2 | |
| paraformer-8k-v1 | Paraformer 语音识别-8k-v1 | |
| paraformer-8k-v2 | Paraformer 语音识别-8k-v2 | |
| paraformer-mtl-v1 | Paraformer 语音识别-mtl-v1 | 多语言 |

## 三、语音相关但非纯转写（供参考，一般不用于闪电说纯 ASR）

| 模型 ID | 名称 | 能力 |
|---|---|---|
| gummy-realtime-v1 | 实时语音识别及翻译 V1.0 | Realtime-Audio-Translate |
| qwen3-livetranslate-flash / -realtime | Qwen3 实时翻译 | 语音翻译 |
| qwen3.5/3.8-livetranslate-flash-realtime | 实时翻译 | 语音翻译 |
| qwen-audio-3.x-realtime-plus/flash | Realtime-Chatting | 实时语音对话（非转写） |
| qwen-omni-turbo-realtime | Omni 实时 | 全模态对话 |
| cosyvoice-* / qwen*-tts-* | 语音合成 | TTS，不识别 |

---

## 关键结论
1. **闪电说原生阿里云** 服务商用的模型是 `qwen3-asr-flash-realtime`（流式）。
2. **当前自定义代理** 用 `qwen-audio-3.1-asr-flash-streaming`（流式），识别效果最强。
3. **录音文件转写**（走代理 `/audio/transcriptions`）建议用 `qwen3-asr-flash-filetrans` 或 `qwen-audio-3.1-asr-flash-filetrans`。
4. 换模型只需改代理环境变量 `DASHSCOPE_ASR_MODEL`，无需改代码。
