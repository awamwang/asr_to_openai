# -*- coding: utf-8 -*-
r"""ASR 多模型速度基准：分别记录 API 耗时与代理前置/后置耗时。

用法：
    <pyenv312>\python.exe bench/run_bench.py [--rounds 2] [--warmup 1]

耗时切分（单次 router.recognize 调用内）：
    pre   = 代理前置：音频预处理（16k 单声道 / 裁静音）
    api   = 上游 API：provider.recognize（真正网络往返 + 厂商处理）
    post  = 代理后置：出口文本清洗 + keep_warm
    other = 路由解析 / 冷却判定 / 候选过滤等（total - pre - api - post）

结果写入 bench/result.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asr_proxy.config import load_config  # noqa: E402
from asr_proxy.providers import ProviderRouter  # noqa: E402
import asr_proxy.providers as PV  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "bench", "audio")
AUDIO_ORDER = ["s1_short", "s2", "s3", "s4", "s5_long"]

# 单次调用的耗时累计槽
CUR: Dict[str, float] = {"pre": 0.0, "api": 0.0, "post": 0.0}


class _NullLog:
    """吞掉路由器日志，避免污染 asr_proxy.log。"""

    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warn(self, *a, **k): pass


def install_probes(router: ProviderRouter) -> None:
    """给「前置 / API / 后置」三个阶段装计时探针。"""
    orig_pre = PV.preprocess_audio_bytes
    orig_norm = PV.normalize_asr_text

    def timed_pre(*a, **kw):
        t = time.perf_counter()
        try:
            return orig_pre(*a, **kw)
        finally:
            CUR["pre"] += time.perf_counter() - t

    def timed_norm(*a, **kw):
        t = time.perf_counter()
        try:
            return orig_norm(*a, **kw)
        finally:
            CUR["post"] += time.perf_counter() - t

    PV.preprocess_audio_bytes = timed_pre
    PV.normalize_asr_text = timed_norm

    for p in router.providers.values():
        orig_rec = p.recognize
        orig_warm = p.keep_warm

        def timed_rec(*a, _f=orig_rec, **kw):
            t = time.perf_counter()
            try:
                return _f(*a, **kw)
            finally:
                CUR["api"] += time.perf_counter() - t

        def timed_warm(*a, _f=orig_warm, **kw):
            t = time.perf_counter()
            try:
                return _f(*a, **kw)
            finally:
                CUR["post"] += time.perf_counter() - t

        p.recognize = timed_rec
        p.keep_warm = timed_warm


def wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def concrete_models(cfg) -> List[str]:
    """可用模型里筛出「服务商/具体模型」，排除 auto / strategy-* / 服务商裸名。"""
    out: List[str] = []
    for mid in cfg.usable_model_ids():
        if "/" not in mid:
            continue
        provider, _, rest = mid.partition("/")
        if not rest or rest.lower() == "auto":
            continue
        if mid.startswith("strategy-"):
            continue
        out.append(mid)
    return out


def once(router: ProviderRouter, model: str, data: bytes) -> Dict[str, Any]:
    """跑一次识别，返回耗时切分与文本。"""
    CUR.update({"pre": 0.0, "api": 0.0, "post": 0.0})
    t0 = time.perf_counter()
    err = ""
    text = ""
    try:
        result, _errors = router.recognize(data, model=model, filename="audio.wav")
        text = result.text or ""
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
    total = time.perf_counter() - t0
    pre, api, post = CUR["pre"], CUR["api"], CUR["post"]
    return {
        "ok": bool(text) and not err,
        "error": err,
        "chars": len(text),
        "text": text[:60],
        "total": round(total, 4),
        "pre": round(pre, 4),
        "api": round(api, 4),
        "post": round(post, 4),
        "other": round(max(total - pre - api - post, 0.0), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2, help="正式测量轮数")
    ap.add_argument("--warmup", type=int, default=1, help="每模型预热次数（不计入结果）")
    ap.add_argument("--only", default="", help="只跑包含该子串的模型，逗号分隔")
    args = ap.parse_args()

    cfg = load_config()
    router = ProviderRouter(cfg, log_fn=_NullLog())
    install_probes(router)

    models = concrete_models(cfg)
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        models = [m for m in models if any(k in m for k in keys)]

    audios = []
    for name in AUDIO_ORDER:
        path = os.path.join(AUDIO_DIR, name + ".wav")
        if os.path.exists(path):
            with open(path, "rb") as f:
                audios.append({
                    "name": name,
                    "path": os.path.relpath(path, ROOT).replace(os.sep, "/"),
                    "bytes": os.path.getsize(path),
                    "seconds": round(wav_seconds(path), 3),
                    "data": f.read(),
                })
    if not audios:
        print("没有找到测试音频，请先运行 bench/gen_audio.py")
        return 1

    print(f"模型 {len(models)} 个 × 音频 {len(audios)} 段 × {args.rounds} 轮 "
          f"（预热 {args.warmup} 次）", flush=True)

    results: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": args.rounds,
        "warmup": args.warmup,
        "audio_preprocess": bool(cfg.audio_preprocess),
        "audio_trim_silence": bool(cfg.audio_trim_silence),
        "clean_punctuation": bool(cfg.clean_punctuation),
        "audios": [{k: v for k, v in a.items() if k != "data"} for a in audios],
        "models": models,
        "runs": [],
    }

    for mi, model in enumerate(models, 1):
        for _ in range(args.warmup):
            once(router, model, audios[0]["data"])
        for a in audios:
            rounds = [once(router, model, a["data"]) for _ in range(args.rounds)]
            ok_rounds = [r for r in rounds if r["ok"]]
            if ok_rounds:
                avg = {k: round(sum(r[k] for r in ok_rounds) / len(ok_rounds), 4)
                       for k in ("total", "pre", "api", "post", "other")}
            else:
                avg = {k: 0.0 for k in ("total", "pre", "api", "post", "other")}
            results["runs"].append({
                "model": model,
                "audio": a["name"],
                "audio_seconds": a["seconds"],
                "audio_bytes": a["bytes"],
                "success": len(ok_rounds),
                "rounds": rounds,
                "avg": avg,
            })
            flag = "OK " if ok_rounds else "ERR"
            print(f"  [{mi}/{len(models)}] {flag} {model:42s} {a['name']:9s} "
                  f"api={avg['api']:.3f}s total={avg['total']:.3f}s", flush=True)

    out = os.path.join(ROOT, "bench", "result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
