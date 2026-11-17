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

输出当前配置下真实可用的模型 ID 列表（不启动服务）：
  python main.py --models
    排序：最上边为策略模型（auto + strategy-*），
         随后按 auto_order 的服务商顺序，每家先 <服务商>/auto，再列具体模型。
"""
from __future__ import annotations

import argparse
import os
import sys

# 保证以脚本方式运行时能找到 asr_proxy 包
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn

from asr_proxy.app import create_app
from asr_proxy.config import load_config
from asr_proxy.dnskeepalive import start_dns_keepalive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASR → OpenAI / 闪电说兼容代理",
    )
    parser.add_argument(
        "--models", "--list-models", action="store_true",
        help="仅输出当前配置下真实可用的模型 ID 列表后退出（不启动服务）",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.models:
        for mid in cfg.usable_model_ids():
            print(mid)
        return

    enabled = [p.name for p in cfg.enabled_providers()]
    print(f"[asr_proxy] host={cfg.host}:{cfg.port}")
    print(f"[asr_proxy] enabled providers: {enabled or '(none)'}")
    print(f"[asr_proxy] auto_order: {cfg.auto_order}")
    print(f"[asr_proxy] strategy models: strategy-cost/speed/accuracy/free (request as model ID)")
    print(f"[asr_proxy] text: clean_punctuation={cfg.clean_punctuation} smart_sentence={cfg.smart_sentence}")
    mapped = []
    if "tencent" in cfg.providers:
        mapped.append(f"tencent.filter_punc={cfg.providers['tencent'].filter_punc}")
    if "doubao" in cfg.providers:
        mapped.append(f"doubao.enable_punc={cfg.providers['doubao'].enable_punc}")
    if mapped:
        print(f"[asr_proxy] text→providers: {', '.join(mapped)}")
    print(f"[asr_proxy] log: {cfg.log_file} level={cfg.log_level} backup_days={cfg.log_backup_days}")
    print(
        f"[asr_proxy] security: auth={'on' if cfg.auth_token else 'off'} "
        f"max_body={cfg.max_body_bytes} expose_errors={cfg.expose_errors} "
        f"log_text={cfg.log_asr_text} docs={cfg.enable_docs}"
    )
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
    if cfg.host not in ("127.0.0.1", "localhost", "::1") and not cfg.auth_token:
        print(
            f"[asr_proxy] 警告：host={cfg.host} 非本机地址且未配置 ASR_AUTH_TOKEN，"
            "任何能访问该端口的人都能调用付费 ASR；建议设置 token 或改回 127.0.0.1"
        )
    app = create_app(cfg)
    stop_dns_keepalive = start_dns_keepalive(cfg)
    try:
        uvicorn.run(
            app,
            host=cfg.host,
            port=cfg.port,
            log_level="info",
            limit_concurrency=cfg.limit_concurrency,
            timeout_keep_alive=cfg.timeout_keep_alive,
        )
    finally:
        if stop_dns_keepalive:
            stop_dns_keepalive()


if __name__ == "__main__":
    main()
