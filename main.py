# -*- coding: utf-8 -*-
"""
ASR → OpenAI / 闪电说兼容代理 — 主入口
=====================================
将配置的语音识别服务商桥接为闪电说「自定义语音识别服务商」可用格式。

用法：
  1. pip install -r requirements.txt
  2. 复制 config.example.yaml → config.yaml，填写 api_key
     （或设置环境变量 DASHSCOPE_API_KEY / XAI_API_KEY 等）
  3. python main.py
  4. 闪电说面板：
       地址：http://127.0.0.1:8004
       模型：auto  （或 strategy-cost / strategy-free / aliyun/qwen-audio-3.1-asr-flash-streaming）
"""
from __future__ import annotations

import os
import sys

# 保证以脚本方式运行时能找到 asr_proxy 包
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn

from asr_proxy.app import create_app
from asr_proxy.config import load_config


def main() -> None:
    cfg = load_config()
    enabled = [p.name for p in cfg.enabled_providers()]
    print(f"[asr_proxy] host={cfg.host}:{cfg.port}")
    print(f"[asr_proxy] enabled providers: {enabled or '(none)'}")
    print(f"[asr_proxy] auto_order: {cfg.auto_order}")
    print(f"[asr_proxy] strategy models: strategy-cost/speed/accuracy/free (request as model ID)")
    print(f"[asr_proxy] text: clean_punctuation={cfg.clean_punctuation} smart_sentence={cfg.smart_sentence}")
    print(f"[asr_proxy] log: {cfg.log_file} level={cfg.log_level} backup_days={cfg.log_backup_days}")
    if not enabled:
        print("[asr_proxy] 警告：未启用任何服务商，请在 config.yaml 或环境变量中填写 api_key")

    app = create_app(cfg)
    if cfg.smart_sentence:
        smart = app.state.router_asr.list_smart_sentence_models()
        if smart:
            print(f"[asr_proxy] smart_sentence 可用模型: {smart}")
        else:
            print(
                "[asr_proxy] 警告：smart_sentence=true 但当前无可用模型"
                "（需启用阿里云 streaming/realtime，如 qwen-audio-3.1-asr-flash-streaming）"
            )
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
