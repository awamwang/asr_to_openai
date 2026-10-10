# -*- coding: utf-8 -*-
"""生成 5 段长短不等的中文测试录音（edge-tts → ffmpeg 16kHz 单声道 WAV）。

每段文本均 <= 100 字，长度由短到长递增，用于 ASR 多模型速度对比。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import edge_tts

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio")
FFMPEG = r"S:\Libs\ffmpeg-win64-static\bin\ffmpeg.EXE"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+0%"

# (文件名, 文本)：字数由少到多
SAMPLES = [
    ("s1_short", "今天天气不错，我们出去走走吧。"),
    ("s2", "帮我查一下明天上午从北京到上海的高铁票还有没有。"),
    ("s3", "最近公司在推进一个新项目，需要各部门把进度同步一下，周五之前提交到共享文档里面。"),
    ("s4", "这份报告的数据我昨天晚上已经核对过了，整体增长趋势没有问题，"
           "但是华南地区的样本量偏小，建议你再补充两周的原始数据，"
           "这样结论会更有说服力一些。"),
    ("s5_long", "大家好，今天同步三件事。第一，服务端接口改造已完成，"
                "预计下周二灰度发布；第二，客户端兼容性测试还差安卓低端机型，"
                "需要补测；第三，综合考虑识别准确率和响应速度，建议继续沿用当前方案。"),
]


async def tts(text: str, mp3_path: str) -> None:
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    await comm.save(mp3_path)


def to_wav(mp3_path: str, wav_path: str) -> None:
    cmd = [
        FFMPEG, "-y", "-i", mp3_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        wav_path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(p.stderr or '')[-500:]}")


def wav_seconds(path: str) -> float:
    import wave
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


async def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, text in SAMPLES:
        n = len(text)
        if n > 100:
            print(f"[FAIL] {name}: {n} 字，超过 100 字上限")
            return 1
        mp3 = os.path.join(OUT_DIR, name + ".mp3")
        wav = os.path.join(OUT_DIR, name + ".wav")
        await tts(text, mp3)
        to_wav(mp3, wav)
        os.remove(mp3)
        print(f"[OK] {name}.wav  {n} 字  {wav_seconds(wav):.2f}s  "
              f"{os.path.getsize(wav)} bytes  :: {text[:24]}...")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
