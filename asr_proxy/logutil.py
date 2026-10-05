# -*- coding: utf-8 -*-
"""基于标准库 logging 的分级日志与按天滚动。

- 等级：debug / info / warning / error
- 按天切割（午夜），最多保留 backup_days 天备份
- 当前写入文件名不变（如 asr_proxy.log），历史为 asr_proxy.log.YYYY-MM-DD
"""
from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import Callable, Optional, Union

LOGGER_NAME = "asr_proxy"

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# 默认保留天数（不含当天正在写入的文件）
DEFAULT_BACKUP_DAYS = 7

_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def normalize_log_level(raw: Optional[str], default: str = "info") -> str:
    key = (raw or default).strip().lower()
    if key in _LEVEL_MAP:
        return "warning" if key == "warn" else key
    return default


def level_to_int(level: str) -> int:
    return _LEVEL_MAP.get(normalize_log_level(level), logging.INFO)


class _CallbackHandler(logging.Handler):
    """把格式化后的整行交给回调（测试用 list.append）。"""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:
            self.handleError(record)


def setup_logging(
    log_file: str,
    level: str = "info",
    backup_days: int = DEFAULT_BACKUP_DAYS,
    *,
    also_console: bool = False,
) -> logging.Logger:
    """配置 asr_proxy 根日志：按天滚动文件，最多保留 backup_days 天。

    可重复调用（会先清空同名 logger 的 handlers），便于测试/热重载。
    """
    path = (log_file or "asr_proxy.log").strip() or "asr_proxy.log"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    lv = level_to_int(level)
    days = max(1, int(backup_days or DEFAULT_BACKUP_DAYS))

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(lv)
    logger.propagate = False

    formatter = logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT)

    file_handler = TimedRotatingFileHandler(
        filename=path,
        when="midnight",
        interval=1,
        backupCount=days,
        encoding="utf-8",
        delay=True,
    )
    # 滚动后后缀：asr_proxy.log.2026-10-05
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(lv)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if also_console:
        console = logging.StreamHandler()
        console.setLevel(lv)
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger


def get_logger() -> logging.Logger:
    """获取 asr_proxy logger；若尚未 setup，仍可返回（无 handler 时不输出）。"""
    return logging.getLogger(LOGGER_NAME)


def as_logger(
    log_fn: Optional[object] = None,
    level: str = "info",
) -> logging.Logger:
    """把 logging.Logger / 裸回调 / None 统一成 logging.Logger。

    - None → 使用全局 asr_proxy logger
    - logging.Logger → 原样返回
    - callable → 临时 logger + CallbackHandler（测试捕获，不过滤等级）
    """
    if isinstance(log_fn, logging.Logger):
        return log_fn
    if callable(log_fn):
        # 独立名字，避免污染全局 handlers
        name = f"{LOGGER_NAME}.capture.{id(log_fn)}"
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = _CallbackHandler(log_fn)
        handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT))
        logger.addHandler(handler)
        return logger
    return get_logger()


LogLike = Union[logging.Logger, Callable[[str], None], None]
