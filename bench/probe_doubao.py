# -*- coding: utf-8 -*-
r"""给 doubao（bigmodel_nostream）做精确分段计时。

用法：
    python bench/probe_doubao.py [请求间隔秒数] [音频名 ...]
    例：python bench/probe_doubao.py 1.0 s1_short s3

一次识别的时间轴（代理视角）：
    |-- connect --|-- upload --|-- server --|-- parse --|
    connect = TCP+TLS+WS 握手（命中预热池则为 0）
    upload  = 首片发出 → 末片发完（gzip + 网络写）
    server  = 末片发完 → 收到最终帧（豆包服务端处理 + 回传）
    parse   = 其余（解帧、构造结果）
"""
from __future__ import annotations

import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from asr_proxy.config import load_config  # noqa: E402
from asr_proxy.providers import build_providers  # noqa: E402

import websocket  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "bench", "audio")
ST = {}


def reset():
    ST.update(connect=0.0, upload_start=0.0, upload_end=0.0,
              server_end=0.0, n_send=0, n_recv=0, warm=False)


def install(p):
    """在 websocket.create_connection 层统一打点（只包装一次）。"""
    orig_cc = websocket.create_connection

    def cc(*a, **k):
        t = time.perf_counter()
        ws = orig_cc(*a, **k)
        ST["connect"] += time.perf_counter() - t
        o_send, o_recv = ws.send, ws.recv

        def s(*a, **k):
            if ST["n_send"] == 0:
                ST["upload_start"] = time.perf_counter()
            r = o_send(*a, **k)
            ST["upload_end"] = time.perf_counter()
            ST["n_send"] += 1
            return r

        def r(*a, **k):
            v = o_recv(*a, **k)
            ST["server_end"] = time.perf_counter()
            ST["n_recv"] += 1
            return v

        ws.send, ws.recv = s, r
        return ws

    websocket.create_connection = cc

    orig_take = p._take_warm_ws

    def take(rid):
        ws = orig_take(rid)
        ST["warm"] = ws is not None
        return ws

    p._take_warm_ws = take


def once(p, data, model="asr-2.0"):
    reset()
    t0 = time.perf_counter()
    res = p.recognize(data, filename="audio.wav", model=model)
    total = time.perf_counter() - t0
    upload = max(ST["upload_end"] - ST["upload_start"], 0.0)
    server = max(ST["server_end"] - ST["upload_end"], 0.0)
    return {
        "total": total, "connect": ST["connect"], "upload": upload,
        "server": server, "warm": ST["warm"],
        "n_send": ST["n_send"], "n_recv": ST["n_recv"],
        "chars": len(res.text),
    }


def main() -> int:
    gap = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    seg = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    names = sys.argv[3:] or ["s1_short", "s2", "s3"]
    secs_map = {"s1_short": 3.26, "s2": 5.26, "s3": 8.28, "s4": 14.09,
                "s5_long": 20.04}

    cfg = load_config()
    p = build_providers(cfg)["doubao"]
    if seg > 0:
        p.segment_ms = seg
    install(p)
    print(f"ws_url={p.ws_url}")
    print(f"warm={p.warm_enabled} ttl={p.warm_ttl}s segment_ms={p.segment_ms}")
    print(f"模拟请求间隔={gap}s（识别后 keep_warm，再间隔后下一次）\n")

    for name in names:
        path = os.path.join(AUDIO_DIR, name + ".wav")
        with open(path, "rb") as f:
            data = f.read()
        once(p, data)          # 预热，不计入
        p.keep_warm()
        time.sleep(gap + 0.5)
        rows = []
        for _ in range(3):
            rows.append(once(p, data))
            p.keep_warm()
            time.sleep(gap)
        m = {k: statistics.fmean(r[k] for r in rows)
             for k in ("total", "connect", "upload", "server")}
        hits = sum(1 for r in rows if r["warm"])
        parse = max(m["total"] - m["connect"] - m["upload"] - m["server"], 0.0)
        print(f"=== {name} ({secs_map.get(name, '?')}s)  预热命中 {hits}/3 "
              f"分片 {rows[0]['n_send']} 响应帧 {rows[0]['n_recv']} ===")
        print(f"  端到端       {m['total']*1000:7.0f} ms")
        print(f"  ├ 建连      {m['connect']*1000:7.0f} ms")
        print(f"  ├ 上传      {m['upload']*1000:7.0f} ms")
        print(f"  ├ 服务端    {m['server']*1000:7.0f} ms   ← 豆包处理+回传")
        print(f"  └ 其余      {parse*1000:7.0f} ms")
        print(f"  服务端占比   {m['server']/m['total']*100:6.1f}%\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
