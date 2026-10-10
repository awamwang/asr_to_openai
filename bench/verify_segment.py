# -*- coding: utf-8 -*-
r"""对照不同 segment_ms 下的豆包识别：耗时 + 文本一致性。

用法：python bench/verify_segment.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asr_proxy.config import load_config  # noqa: E402
from asr_proxy.providers import build_providers  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "bench", "audio")
NAMES = ["s1_short", "s2", "s3", "s4", "s5_long"]
SEGS = [200, 1000, 2000]


def main() -> int:
    cfg = load_config()
    p = build_providers(cfg)["doubao"]
    data = {}
    for n in NAMES:
        with open(os.path.join(AUDIO_DIR, n + ".wav"), "rb") as f:
            data[n] = f.read()

    results = {}
    for seg in SEGS:
        p.segment_ms = seg
        for n in NAMES:
            p.recognize(data[n], filename="audio.wav", model="asr-2.0")  # 预热
            p.keep_warm()
            time.sleep(1.5)
            t = time.perf_counter()
            r = p.recognize(data[n], filename="audio.wav", model="asr-2.0")
            el = time.perf_counter() - t
            p.keep_warm()
            time.sleep(1.0)
            results[(seg, n)] = (el, r.text)
            print(f"seg={seg:5d} {n:9s} {el*1000:7.0f} ms  {r.text[:34]}")

    print("\n=== 文本一致性（以 seg=200 为基准）===")
    same = True
    for n in NAMES:
        base = results[(200, n)][1]
        for seg in SEGS[1:]:
            if results[(seg, n)][1] != base:
                same = False
                print(f"  [差异] {n} seg={seg}: {results[(seg,n)][1][:40]}")
    print("  全部一致" if same else "  存在差异，见上")

    print("\n=== 耗时对比（ms）===")
    print(f"{'音频':10s}" + "".join(f"{s:>10d}" for s in SEGS))
    for n in NAMES:
        print(f"{n:10s}" + "".join(f"{results[(s,n)][0]*1000:10.0f}" for s in SEGS))
    tot = {s: sum(results[(s, n)][0] for n in NAMES) / len(NAMES) * 1000
           for s in SEGS}
    print(f"{'平均':10s}" + "".join(f"{tot[s]:10.0f}" for s in SEGS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
