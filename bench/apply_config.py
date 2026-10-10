# -*- coding: utf-8 -*-
r"""按 2026-10-10 实测结果更新 config.yaml（按行精确替换，不触碰密钥行内容）。

改动：
  1. baidu.models 去掉 80001（未开通权限，调用必失败）
  2. siliconflow 整块注释（SenseVoiceSmall 实测 13.8s / RTF>1，慢于实时）
  3. auto_order / free_order 移除 siliconflow
  4. speed_order 由空改为按实测速度排序的服务商底序

执行前自动备份到 config.yaml.bak-<时间戳>。
"""
from __future__ import annotations

import io
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config.yaml")


def main() -> int:
    shutil.copyfile(PATH, PATH + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
    with io.open(PATH, encoding="utf-8") as f:
        lines = f.read().split("\n")  # 0-based

    def get(ln: int) -> str:
        return lines[ln - 1]

    def expect(ln: int, needle: str) -> None:
        if needle not in get(ln):
            raise SystemExit(f"行 {ln} 不匹配期望 {needle!r}，实际 {get(ln)!r}")

    # ---- 1) baidu/80001 移出默认模型 ----
    expect(305, '"1537"')
    expect(306, '"80001"')
    lines[304] = '      - "1537"    # 短语音标准版；实测 0.70s / RTF 0.069'
    lines[305] = (
        '      # - "80001"   # 短语音极速版：控制台未开通权限，实测必失败'
        '（[permission] No permission to access data），已移出默认模型'
    )

    # ---- 2) siliconflow 整块注释（203-210）----
    expect(203, "siliconflow:")
    expect(210, "SenseVoiceSmall")
    note = [
        "  # ⚠️ 已停用（2026-10-10 实测）：SenseVoiceSmall 平均 API 耗时 13.77s、"
        "RTF 1.35（慢于实时），",
        "  #    不适合闪电说短切段场景。该服务商当前仅此一个模型，故整块停用；",
        "  #    恢复时把下面 siliconflow: 整段取消注释即可（密钥保留在注释里）。",
    ]
    lines[202:202] = note
    # 插入 3 行后，原 203-210 → 206-213
    for i in range(205, 213):
        raw = lines[i]
        if not raw.strip():
            continue
        body = raw[2:] if raw.startswith("  ") else raw
        lines[i] = "  #" + body if body.startswith(" ") else "  # " + body

    # ---- 3) auto_order / free_order 移除 siliconflow ----
    idxs = [i for i, l in enumerate(lines) if l.strip() == "- siliconflow"]
    if len(idxs) != 2:
        raise SystemExit(f"期望 2 处 '- siliconflow'，实际 {len(idxs)}")
    for i in sorted(idxs, reverse=True):
        del lines[i]

    # ---- 4) speed_order 填实测顺序 ----
    so = next(i for i, l in enumerate(lines) if l.strip().startswith("speed_order:"))
    lines[so] = "\n".join([
        "speed_order:",
        "  # 按 2026-10-10 实测排序（5 段中文录音 × 2 轮；各家最快模型的平均 API 耗时）：",
        "  #   tencent 0.35s < stepfun 0.47s < baidu 0.70s < doubao 1.52s",
        "  #   < groq 1.91s < aliyun 2.19s",
        "  - tencent",
        "  - stepfun",
        "  - baidu",
        "  - doubao",
        "  - groq",
        "  - aliyun",
    ])

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("config.yaml 已更新（已备份 .bak-*）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
