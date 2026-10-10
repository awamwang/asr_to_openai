# -*- coding: utf-8 -*-
"""把 bench/result.json 渲染成一份自包含 HTML 速度对比报告。"""
from __future__ import annotations

import json
import os
import statistics
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C_API = "#d64545"
C_PRE = "#4a7fb5"
C_POST = "#e0a13a"
C_OTHER = "#9aa5b1"


def ms(v: float) -> str:
    return f"{v * 1000:.0f}ms" if v < 1 else f"{v:.2f}s"


def bar_chart(rows: List[tuple], title: str, unit: str = "s") -> str:
    """横向条形图（纯 SVG，无外部依赖）。rows = [(label, value, color)]。"""
    if not rows:
        return ""
    row_h, gap = 30, 8
    label_w, val_w = 300, 74
    width = 900
    plot_w = width - label_w - val_w - 20
    height = len(rows) * (row_h + gap) + 20
    vmax = max(v for _, v, _ in rows) or 1.0
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" role="img" aria-label="{title}">',
        f'<text x="0" y="14" font-size="13" font-weight="600" fill="#1f2933">{title}</text>',
    ]
    for i, (label, val, color) in enumerate(rows):
        y = 26 + i * (row_h + gap)
        w = max(plot_w * val / vmax, 1.5)
        parts.append(
            f'<text x="0" y="{y + 15}" font-size="12" fill="#3e4c59">{label}</text>'
        )
        parts.append(
            f'<rect x="{label_w}" y="{y + 3}" width="{plot_w}" height="{row_h - 8}" '
            f'fill="#eef1f4" rx="3"/>'
        )
        parts.append(
            f'<rect x="{label_w}" y="{y + 3}" width="{w:.1f}" height="{row_h - 8}" '
            f'fill="{color}" rx="3"/>'
        )
        parts.append(
            f'<text x="{label_w + plot_w + 8}" y="{y + 15}" font-size="12" '
            f'fill="#52606d">{ms(val) if unit == "s" else f"{val:.3f}"}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def build(data: Dict[str, Any]) -> str:
    audios = data["audios"]
    runs = data["runs"]
    models: List[str] = []
    for r in runs:
        if r["model"] not in models:
            models.append(r["model"])

    by_model: Dict[str, List[Dict[str, Any]]] = {m: [] for m in models}
    for r in runs:
        by_model[r["model"]].append(r)

    # ---- 每模型汇总（对 5 段取平均）----
    summary = []
    for m in models:
        rs = [r for r in by_model[m] if r["success"] > 0]
        if not rs:
            summary.append({
                "model": m, "ok": 0, "n": len(by_model[m]),
                "api": 0.0, "pre": 0.0, "post": 0.0, "other": 0.0,
                "total": 0.0, "rtf": 0.0, "chars": 0,
            })
            continue
        api = statistics.fmean(r["avg"]["api"] for r in rs)
        pre = statistics.fmean(r["avg"]["pre"] for r in rs)
        post = statistics.fmean(r["avg"]["post"] for r in rs)
        other = statistics.fmean(r["avg"]["other"] for r in rs)
        total = statistics.fmean(r["avg"]["total"] for r in rs)
        secs = statistics.fmean(r["audio_seconds"] for r in rs)
        summary.append({
            "model": m, "ok": len(rs), "n": len(by_model[m]),
            "api": api, "pre": pre, "post": post, "other": other,
            "total": total, "rtf": api / secs if secs else 0.0,
            "chars": statistics.fmean(r["rounds"][0]["chars"] for r in rs
                                      if r["rounds"] and r["rounds"][0]["ok"]),
        })
    summary.sort(key=lambda x: (x["ok"] == 0, x["api"]))

    ok_summary = [s for s in summary if s["ok"] > 0]
    overall_api = statistics.fmean(s["api"] for s in ok_summary) if ok_summary else 0
    overall_total = statistics.fmean(s["total"] for s in ok_summary) if ok_summary else 0

    # ---- 分段明细表 ----
    head = "".join(
        f'<th>{a["name"]}<div class="sub">{a["seconds"]:.1f}s</div></th>'
        for a in audios
    )
    detail_rows = []
    for s in summary:
        m = s["model"]
        cells = []
        for a in audios:
            r = next((x for x in by_model[m] if x["audio"] == a["name"]), None)
            if r is None or r["success"] == 0:
                err = (r["rounds"][0]["error"][:40] if r and r["rounds"] else "—")
                cells.append(f'<td class="fail" title="{err}">失败</td>')
            else:
                av = r["avg"]
                cells.append(
                    f'<td><b>{ms(av["api"])}</b>'
                    f'<div class="sub">共 {ms(av["total"])}</div></td>'
                )
        valid = [x for x in by_model[m] if x["success"] > 0]
        avg_api = statistics.fmean(x["avg"]["api"] for x in valid) if valid else 0
        detail_rows.append(
            f'<tr><td class="mono">{m}</td>{"".join(cells)}'
            f'<td class="avg"><b>{ms(avg_api)}</b></td>'
            f'<td class="avg">{s["rtf"]:.3f}</td></tr>'
        )

    # ---- 前置/后置开销表 ----
    ov_rows = []
    for s in ok_summary:
        ov_rows.append(
            f'<tr><td class="mono">{s["model"]}</td>'
            f'<td>{ms(s["api"])}</td>'
            f'<td>{s["api"] / s["total"] * 100:.1f}%</td>'
            f'<td>{ms(s["pre"])}</td>'
            f'<td>{ms(s["post"])}</td>'
            f'<td>{ms(s["other"])}</td>'
            f'<td>{ms(s["total"])}</td></tr>'
        )

    # ---- 每段音频的跨模型均值 ----
    per_audio = []
    for a in audios:
        vals = [r["avg"]["api"] for r in runs
                if r["audio"] == a["name"] and r["success"] > 0]
        if vals:
            per_audio.append((f'{a["name"]}（{a["seconds"]:.1f}s 音频）',
                              statistics.fmean(vals), C_API))

    audio_info = "".join(
        f'<tr><td class="mono">{a["name"]}.wav</td>'
        f'<td>{a["seconds"]:.2f}s</td>'
        f'<td>{a["bytes"] / 1024:.0f} KB</td></tr>'
        for a in audios
    )

    chart_speed = bar_chart(
        [(s["model"], s["api"], C_API) for s in ok_summary],
        "各模型平均 API 耗时（对 5 段录音取平均，越短越快）",
    )
    chart_rtf = bar_chart(
        [(s["model"], s["rtf"], C_PRE) for s in ok_summary],
        "实时率 RTF = API 耗时 / 音频时长（越小越快，0.1 即 10 倍实时）",
        unit="r",
    )
    chart_audio = bar_chart(
        per_audio, "同一段音频在各模型上的平均 API 耗时",
    )

    failed = [s["model"] for s in summary if s["ok"] == 0]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>ASR 模型速度对比报告</title>
<style>
  :root {{ --bg:#f7f8fa; --card:#fff; --line:#e3e8ee; --text:#1f2933;
          --muted:#52606d; --sub:#7b8794; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 20px 60px; background:var(--bg); color:var(--text);
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  h1 {{ font-size:24px; margin:0 0 6px; }}
  h2 {{ font-size:17px; margin:32px 0 12px; padding-left:10px;
        border-left:3px solid {C_API}; }}
  .meta {{ color:var(--muted); font-size:13px; margin-bottom:20px; }}
  .card {{ background:var(--card); border:1px solid var(--line);
           border-radius:10px; padding:18px 20px; margin-bottom:16px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:7px 8px; border-bottom:1px solid var(--line); text-align:right; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ color:var(--muted); font-weight:600; background:#fafbfc; }}
  td.mono {{ font-family:ui-monospace,Consolas,monospace; font-size:12px; }}
  td.avg {{ background:#fafbfc; font-weight:600; }}
  td.fail {{ color:#b03030; background:#fdf3f3; }}
  .sub {{ color:var(--sub); font-size:11px; font-weight:400; }}
  .kpi {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .kpi div {{ flex:1; min-width:150px; background:var(--card);
              border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kpi span {{ display:block; color:var(--muted); font-size:12px; }}
  .kpi b {{ font-size:22px; }}
  .note {{ color:var(--muted); font-size:12.5px; }}
  .legend span {{ display:inline-block; margin-right:16px; font-size:12px;
                  color:var(--muted); }}
  .sw {{ display:inline-block; width:10px; height:10px; border-radius:2px;
         margin-right:5px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>ASR 模型速度对比报告</h1>
  <div class="meta">
    生成时间 {data["generated_at"]} · {len(models)} 个模型 × {len(audios)} 段录音
    · 每组合 {data["rounds"]} 轮（另预热 {data["warmup"]} 次）取平均<br>
    音频预处理 audio.preprocess={data["audio_preprocess"]}
    / trim_silence={data["audio_trim_silence"]}
    / clean_punctuation={data["clean_punctuation"]}
  </div>

  <div class="kpi">
    <div><span>全模型平均 API 耗时</span><b>{ms(overall_api)}</b></div>
    <div><span>全模型平均端到端</span><b>{ms(overall_total)}</b></div>
    <div><span>最快模型</span><b style="font-size:15px">{ok_summary[0]["model"] if ok_summary else "—"}</b>
      <span class="sub">{ms(ok_summary[0]["api"]) if ok_summary else ""}</span></div>
    <div><span>成功 / 总模型</span><b>{len(ok_summary)}/{len(models)}</b></div>
  </div>

  <h2>一、测试素材</h2>
  <div class="card">
    <table><thead><tr><th>文件</th><th>时长</th><th>大小</th></tr></thead>
    <tbody>{audio_info}</tbody></table>
    <p class="note">5 段均由同一中文音色合成，字数 15 / 24 / 40 / 70 / 90 以内，
       16kHz 单声道 PCM WAV。</p>
  </div>

  <h2>二、速度排行（API 耗时）</h2>
  <div class="card">{chart_speed}</div>
  <div class="card">{chart_rtf}</div>
  <div class="card">{chart_audio}</div>

  <h2>三、分段明细</h2>
  <div class="card">
    <table>
      <thead><tr><th>模型</th>{head}<th>本行平均</th><th>RTF</th></tr></thead>
      <tbody>{"".join(detail_rows)}</tbody>
    </table>
    <p class="note">单元格上排为 <b>API 耗时</b>，下排为该次请求端到端总耗时。
       RTF = API 耗时 ÷ 音频时长。</p>
  </div>

  <h2>四、耗时构成：API vs 代理前置/后置</h2>
  <div class="card">
    <div class="legend">
      <span><i class="sw" style="background:{C_API}"></i>API（上游）</span>
      <span><i class="sw" style="background:{C_PRE}"></i>前置（音频预处理）</span>
      <span><i class="sw" style="background:{C_POST}"></i>后置（文本清洗/保活）</span>
      <span><i class="sw" style="background:{C_OTHER}"></i>其他（路由解析等）</span>
    </div>
    <table>
      <thead><tr><th>模型</th><th>API</th><th>占比</th><th>前置</th>
        <th>后置</th><th>其他</th><th>端到端</th></tr></thead>
      <tbody>{"".join(ov_rows)}</tbody>
    </table>
  </div>

  <h2>五、结论</h2>
  <div class="card">
    <ol class="note" style="font-size:13px">
      <li>代理自身开销（前置 + 后置 + 路由）几乎可以忽略：输入已是 16kHz 单声道
          WAV 时走免 ffmpeg 快路径，前置/后置均为毫秒级，
          <b>端到端耗时 ≈ 上游 API 耗时</b>。</li>
      <li>模型速度差距主要来自上游：最快与最慢相差
          <b>{(ok_summary[-1]["api"] / ok_summary[0]["api"]):.1f} 倍</b>
          （{ok_summary[0]["model"]} {ms(ok_summary[0]["api"])} vs
          {ok_summary[-1]["model"]} {ms(ok_summary[-1]["api"])}）。</li>
      <li>音频越长绝对耗时越高，但多数模型 RTF 远低于 1，说明处理快于实时。</li>
      {"<li>以下模型本次全部调用失败：" + "、".join(failed) + "，需检查密钥/网络。</li>" if failed else ""}
    </ol>
  </div>
</div>
</body>
</html>
"""


def main() -> int:
    src = os.path.join(ROOT, "bench", "result.json")
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    dst = os.path.join(ROOT, "bench", "report.html")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(build(data))
    print(f"报告已生成 {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
