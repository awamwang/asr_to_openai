# -*- coding: utf-8 -*-
"""后台 DNS 保活线程。

背景（2026-11-17 在 Windows 26200/25H2 上实测诊断）：
- 系统解析器（Dnscache）的冷路径非常慢：DNS 缓存未命中且解析器
  「热状态」过期时，getaddrinfo 需 ~7.5s（逐服务器/逐网卡 1s 超时
  重试后才落到可用 DNS；直接问 223.5.5.5 只要 10ms）。
- 上游记录 TTL 仅 14s~5min，请求间隔大于 TTL 时几乎必然进入慢路径，
  表现为所有服务商请求统一 +~7.5s（asr_proxy.log 2026-11-16 22:04 起）。
- 解析成功后的短窗口内，后续未命中的解析只要 ~300ms（解析器会记住
  可用路径）。周期性提前解析各服务商域名，可同时保住「DNS 缓存条目」
  与「解析器热状态」，把 7.5s 慢路径完全移出请求关键路径。

配置：ASR_DNS_KEEPALIVE_SEC（或 config.yaml server.dns_keepalive_sec），
默认 10 秒（须低于域名链中最短 TTL ~14-26s，否则隔轮过期重新进入慢路径）；
设为 0 关闭。
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from asr_proxy.config import AppConfig

# 实测（2026-11-17）：openspeech.bytedance.com 链中最短 TTL ~26s
# （stepfun 链中出现过 14s）；20s 间隔会隔轮踩过期，10s 全程缓存命中。
DEFAULT_INTERVAL_SEC = 10.0
# 单次解析超过该毫秒数告警（缓存命中 <10ms，慢路径 ~7500ms）
SLOW_WARN_MS = 2000.0


def host_from_url(url: str) -> str:
    """从 http/https/wss URL 提取小写主机名；无主机名返回空串。"""
    value = (url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return ""
    return (host or "").strip().lower()


def _add_host(hosts: List[str], seen: set, host: str) -> None:
    h = (host or "").strip().lower()
    # 跳过 IP 字面量与空值：无需解析
    if not h or h in seen:
        return
    try:
        socket.inet_pton(socket.AF_INET, h)
        return
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, h)
        return
    except OSError:
        pass
    seen.add(h)
    hosts.append(h)


def collect_warm_hosts(cfg: AppConfig) -> List[str]:
    """收集需要保活的域名：已启用服务商的全部接入点。

    - base_url 型（groq/doubao/siliconflow/stepfun 等）取 URL 主机名；
    - 阿里 dashscope SDK 默认接入点 + workspace 专属接入点；
    - 腾讯 Flash/实时共用 asr.cloud.tencent.com；
    - 百度 token 与识别两个接入点。
    """
    hosts: List[str] = []
    seen: set = set()

    providers = cfg.providers or {}
    for p in providers.values():
        if not p.enabled:
            continue
        _add_host(hosts, seen, host_from_url(p.base_url))

    aliyun = providers.get("aliyun")
    if aliyun is not None and aliyun.enabled:
        # dashscope SDK 默认接入点（未配置 workspace_id 时使用）
        _add_host(hosts, seen, "dashscope.aliyuncs.com")
        ws = (aliyun.workspace_id or "").strip()
        if ws:
            _add_host(
                hosts, seen,
                f"{ws.lower()}.cn-beijing.maas.aliyuncs.com",
            )

    tencent = providers.get("tencent")
    if tencent is not None and tencent.enabled:
        from asr_proxy.providers.tencent import FLASH_HOST, REALTIME_HOST
        _add_host(hosts, seen, FLASH_HOST)
        _add_host(hosts, seen, REALTIME_HOST)

    baidu = providers.get("baidu")
    if baidu is not None and baidu.enabled:
        from asr_proxy.providers.baidu import ASR_URL, TOKEN_URL
        _add_host(hosts, seen, host_from_url(TOKEN_URL))
        _add_host(hosts, seen, host_from_url(ASR_URL))

    return hosts


def resolve_host(host: str) -> float:
    """解析一次主机名，返回耗时（毫秒）；失败抛 OSError/gaierror。"""
    t0 = time.perf_counter()
    socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return (time.perf_counter() - t0) * 1000.0


def warm_round(hosts: List[str], logger: logging.Logger) -> None:
    """对全部域名各解析一次：失败/超阈值告警，其余 debug 汇总。"""
    ok = 0
    max_ms = 0.0
    for host in hosts:
        try:
            ms = resolve_host(host)
        except Exception as exc:  # noqa: BLE001 保活线程绝不向上抛
            logger.warning(
                "[dns-keepalive] resolve failed host=%s err=%s", host, exc,
            )
            continue
        ok += 1
        max_ms = max(max_ms, ms)
        if ms >= SLOW_WARN_MS:
            logger.warning(
                "[dns-keepalive] slow resolve host=%s took_ms=%.0f", host, ms,
            )
    logger.debug(
        "[dns-keepalive] round ok=%d/%d max_ms=%.1f", ok, len(hosts), max_ms,
    )


def start_dns_keepalive(
    cfg: AppConfig,
    logger: Optional[logging.Logger] = None,
) -> Optional[Callable[[], None]]:
    """启动后台 DNS 保活线程；返回 stop() 回调。

    interval<=0 或无域名可保活时返回 None（不启动）。
    """
    log = logger or logging.getLogger("asr_proxy")
    interval = float(cfg.dns_keepalive_sec or 0.0)
    if interval <= 0:
        return None
    hosts = collect_warm_hosts(cfg)
    if not hosts:
        return None

    stop_event = threading.Event()

    def _run() -> None:
        # 启动立即先跑一轮：把冷路径代价移到进程启动时，而非首个用户请求
        while not stop_event.is_set():
            warm_round(hosts, log)
            stop_event.wait(interval)

    thread = threading.Thread(
        target=_run, name="asr-dns-keepalive", daemon=True,
    )
    thread.start()
    log.info(
        "[dns-keepalive] started interval=%.0fs hosts=%s", interval, hosts,
    )

    def _stop() -> None:
        stop_event.set()

    return _stop
