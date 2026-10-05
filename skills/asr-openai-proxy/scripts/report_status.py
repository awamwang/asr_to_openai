#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告 ASR→OpenAI 代理的真实运行配置与接口信息（确定性输出，不访问网络）。

用法（在本项目根目录或任意目录均可）:
    python skills/asr-openai-proxy/scripts/report_status.py
    python skills/asr-openai-proxy/scripts/report_status.py --json   # JSON 输出

输出内容:
  1. 配置来源（config.yaml 或回退到 config.example.yaml）
  2. 监听地址（host:port，可用环境变量覆盖）
  3. 已启用服务商及其配置的模型列表（密钥完备才算启用）
  4. 全部可用模型 ID（auto / strategy-* / 服务商/模型 / 服务商/auto）
  5. 本地服务启动后的接口路径
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 项目根 = 本脚本的上级三级目录（skills/<skill>/scripts → 项目根）
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asr_proxy.config import DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG_PATH, load_config  # noqa: E402
from asr_proxy.strategy import STRATEGY_PUBLIC_IDS  # noqa: E402


def collect() -> dict:
    cfg = load_config()
    src = DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else EXAMPLE_CONFIG_PATH
    enabled = cfg.enabled_providers()

    providers = []
    for p in enabled:
        providers.append({
            "name": p.name,
            "models": list(p.models),
            "default_model_id": f"{p.name}/{p.model}",
        })

    model_ids = ["auto"] + list(STRATEGY_PUBLIC_IDS)
    for p in enabled:
        if p.name not in model_ids:
            model_ids.append(p.name)  # 裸服务商名 → 该服务商默认模型
        for m in p.models:
            mid = f"{p.name}/{m}"
            if mid not in model_ids:
                model_ids.append(mid)
        pauto = f"{p.name}/auto"
        if pauto not in model_ids:
            model_ids.append(pauto)

    base = f"http://{cfg.host}:{cfg.port}"
    return {
        "config_file": str(src),
        "listen": f"{cfg.host}:{cfg.port}",
        "base_url": base,
        "enabled_providers": providers,
        "model_ids": model_ids,
        "endpoints": [
            {"method": "GET",  "path": "/health",                 "desc": "服务商/模型/上次成功记录"},
            {"method": "GET",  "path": "/v1/models",              "desc": "OpenAI 风格模型列表（另 /models 别名）"},
            {"method": "POST", "path": "/v1/audio/transcriptions","desc": "标准 multipart 转写（另 /audio/transcriptions 别名）"},
            {"method": "POST", "path": "/v1/chat/completions",    "desc": "闪电说主路径：JSON base64 音频（另 /chat/completions 别名）"},
        ],
    }


def main() -> None:
    as_json = "--json" in sys.argv[1:]
    info = collect()
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    print(f"配置来源 : {info['config_file']}")
    print(f"监听地址 : {info['listen']}   (闪电说填 {info['base_url']})")
    print()
    print("已启用服务商（密钥完备即启用）:")
    for p in info["enabled_providers"]:
        print(f"  - {p['name']:<12} 默认 {p['default_model_id']}")
        for m in p["models"]:
            print(f"      · {p['name']}/{m}")
    print()
    print("可用模型 ID（请求 model 字段）:")
    for mid in info["model_ids"]:
        print(f"  {mid}")
    print()
    print("本地启动后的接口路径:")
    for e in info["endpoints"]:
        print(f"  {e['method']:<4} {info['base_url']}{e['path']}   — {e['desc']}")
    print()
    print("接口含义速查:")
    print("  POST /chat/completions 请求体示例:")
    print('    {"model":"auto","messages":[{"role":"user","content":[{"type":"input_audio",')
    print('      "input_audio":{"data":"data:audio/wav;base64,<BASE64>"}}]}]}')
    print("  成功响应: {\"choices\":[{\"message\":{\"content\":\"识别文本\"}}]}")


if __name__ == "__main__":
    main()
