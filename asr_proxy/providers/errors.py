# -*- coding: utf-8 -*-
"""ASR 服务商统一错误类型。"""
from __future__ import annotations

from typing import Optional

# auth: 鉴权失败；permission: 无权限；not_activated: 产品/模型未开通
# quota: 额度/欠费；upstream: 上游业务/网络错误；empty: 空文本
ERROR_CODES = (
    "auth",
    "permission",
    "not_activated",
    "quota",
    "upstream",
    "empty",
)


class AsrProviderError(RuntimeError):
    """服务商识别失败，带可分类错误码，供路由汇总与 failover。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "upstream",
        provider: str = "",
        model: str = "",
    ):
        self.code = code if code in ERROR_CODES else "upstream"
        self.provider = provider or ""
        self.model = model or ""
        self.message = (message or "").strip() or "unknown error"
        super().__init__(self.message)

    def __str__(self) -> str:
        who = self.provider
        if self.model:
            who = f"{who}/{self.model}" if who else self.model
        prefix = f"[{self.code}]"
        if who:
            return f"{prefix} {who}: {self.message}"
        return f"{prefix} {self.message}"


def raise_provider_error(
    message: str,
    *,
    code: str = "upstream",
    provider: str = "",
    model: Optional[str] = None,
) -> None:
    """抛出 AsrProviderError（永不返回）。"""
    raise AsrProviderError(
        message, code=code, provider=provider, model=model or "",
    )
