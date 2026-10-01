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
       模型：auto  （或 qwen-audio-3.1-asr-flash-streaming / aliyun）
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
    print(f"[asr_proxy] log: {cfg.log_file}")
    if not enabled:
        print("[asr_proxy] 警告：未启用任何服务商，请在 config.yaml 或环境变量中填写 api_key")

    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
