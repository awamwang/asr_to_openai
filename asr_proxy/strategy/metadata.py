# -*- coding: utf-8 -*-
"""模型路由元数据：费用 / 速度 / 准确度等级。

数据来源：docs/asr_models_guide.md（2026-10 采集），字段口径与该文档一致：
  - speed_tier：S1~S5 → 1~5，数字越小越快（采用文档静态等级，未实测）
  - accuracy_tier：1=最高。当前为静态近似（大模型级 ASR > 轻量/蒸馏模型），
    未做真实准确率实测，后续可用实测数据替换
  - cost.billing：free=免费；per_second=按时长；per_call=按次；
    unknown=未收录（token 计费模型按文档折算为元/秒后归入 per_second）
  - cost.free_tier：longterm=长期免费；quota=周期性/一次性免费额度；空=无免费
    （免费策略用；费用优先忽略此字段，只用刊例价）
  - cost.max_duration：单次硬上限（秒），0=无；仅 t 已知且 t>上限时排除
  - cost.max_bytes：单次文件硬上限（字节），0=无；仅体积已知且超出时排除
  - supported=False：代理当前尚未实现该调用通道（如阿里异步 filetrans），
    策略路由会跳过，避免选到必然失败的模型

价格/免费额度厂商可能调整，下单前以各平台控制台为准。
Groq 价格为美元（currency=USD），`strategy-cost` 按固定汇率 1 USD = 7 CNY
折合后参与人民币刊例比价（粗略，非实时牌价）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# 美元刊例折合人民币的固定粗略汇率（仅用于费用优先比价）
USD_TO_CNY = 7.0

# (服务商, 模型) → 配置档案；键已小写化
_ProfileKey = Tuple[str, str]


@dataclass
class CostSpec:
    """模型计费规格。"""
    billing: str = "unknown"      # free | per_second | per_call | unknown
    price_per_sec: float = 0.0    # 元/秒（per_second / token 折算；USD 见 currency）
    price_per_call: float = 0.0   # 元/次（per_call）
    min_bill_sec: float = 0.0     # 最短计费时长（秒），0=无
    max_duration: float = 0.0     # 单次硬上限（秒），0=无
    max_bytes: int = 0            # 单次文件硬上限（字节），0=无
    free_tier: str = ""           # "" | longterm | quota
    currency: str = "CNY"         # CNY | USD


@dataclass
class ModelProfile:
    """单个模型的路由档案。"""
    provider: str
    model: str
    speed_tier: int = 5           # S1~S5 → 1~5，越小越快
    accuracy_tier: int = 5        # 1 最高，越大越差
    cost: CostSpec = field(default_factory=CostSpec)
    supported: bool = True        # 代理当前是否真正支持该模型通道


def _p(
    provider: str,
    model: str,
    speed: int,
    accuracy: int,
    cost: CostSpec,
    supported: bool = True,
) -> ModelProfile:
    return ModelProfile(
        provider=provider,
        model=model,
        speed_tier=speed,
        accuracy_tier=accuracy,
        cost=cost,
        supported=supported,
    )


# ---- 计费规格速记 ----

def _free(tier: str) -> CostSpec:
    return CostSpec(billing="free", free_tier=tier)


def _per_sec(
    per_sec: float,
    free_tier: str = "",
    min_bill_sec: float = 0.0,
    currency: str = "CNY",
    max_duration: float = 0.0,
    max_bytes: int = 0,
) -> CostSpec:
    return CostSpec(
        billing="per_second",
        price_per_sec=per_sec,
        min_bill_sec=min_bill_sec,
        max_duration=max_duration,
        max_bytes=max_bytes,
        free_tier=free_tier,
        currency=currency,
    )


def _per_call(
    per_call: float,
    free_tier: str = "",
    max_duration: float = 0.0,
    max_bytes: int = 0,
) -> CostSpec:
    return CostSpec(
        billing="per_call",
        price_per_call=per_call,
        max_duration=max_duration,
        max_bytes=max_bytes,
        free_tier=free_tier,
    )


# ---- 档案表（2026-10，docs/asr_models_guide.md）----
#
# 速度等级（文档定义）：
#   S1 极速（30min≈10s）｜S2 很快（≈10 倍速）｜S3 较快｜S4 常规｜S5 异步
#
# 阿里云百炼（现有）：
#   3.1 流式按 Token（6/4.5 元每百万），按 40 token/s 折算 ≈ 0.00026 元/秒（文档）
#   3.0 流式 / qwen3-asr-flash-realtime / fun-asr-realtime 老款按秒 0.00033 元/秒
#   3.0 / 3.1 flash 同步按秒 0.00026 元/秒；filetrans 0.00022 元/秒（异步，未实现）
_ALIYUN_QUOTA = "quota"  # 一次性 36000 秒(10h)/90 天
_GROQ_MAX_BYTES = 25 * 1024 * 1024  # 文件 >25MB 硬排除
_TENCENT_FLASH_MAX_BYTES = 100 * 1024 * 1024  # 极速版文件 >100MB 硬排除
_TENCENT_FLASH_MAX_SEC = 2 * 3600  # 极速版 t >2h 硬排除

_PROFILES: Dict[_ProfileKey, ModelProfile] = {
    ("aliyun", "qwen-audio-3.1-asr-flash-streaming"): _p(
        "aliyun", "qwen-audio-3.1-asr-flash-streaming",
        speed=4, accuracy=1,
        cost=_per_sec(0.00026, free_tier=_ALIYUN_QUOTA),  # 按 Token 折算(40 tok/s)
    ),
    ("aliyun", "qwen-audio-3.0-asr-flash-streaming"): _p(
        "aliyun", "qwen-audio-3.0-asr-flash-streaming",
        speed=4, accuracy=3,
        cost=_per_sec(0.00033, free_tier=_ALIYUN_QUOTA),
    ),
    ("aliyun", "qwen-audio-3.0-asr-flash"): _p(
        "aliyun", "qwen-audio-3.0-asr-flash",
        speed=3, accuracy=2,
        cost=_per_sec(0.00026, free_tier=_ALIYUN_QUOTA),
    ),
    ("aliyun", "qwen-audio-3.1-asr-flash"): _p(
        "aliyun", "qwen-audio-3.1-asr-flash",
        speed=3, accuracy=2,
        cost=_per_sec(0.00026, free_tier=_ALIYUN_QUOTA),  # 文档未单列，按 3.0-flash 推断
    ),
    ("aliyun", "qwen3-asr-flash-realtime"): _p(
        "aliyun", "qwen3-asr-flash-realtime",
        speed=4, accuracy=3,
        cost=_per_sec(0.00033, free_tier=_ALIYUN_QUOTA),
    ),
    ("aliyun", "qwen3-asr-flash"): _p(
        "aliyun", "qwen3-asr-flash",
        speed=3, accuracy=2,
        cost=_per_sec(0.00026, free_tier=_ALIYUN_QUOTA),
    ),
    ("aliyun", "fun-asr-realtime"): _p(
        "aliyun", "fun-asr-realtime",
        speed=4, accuracy=3,
        cost=_per_sec(0.00033, free_tier=_ALIYUN_QUOTA),
    ),
    ("aliyun", "fun-asr-flash"): _p(
        "aliyun", "fun-asr-flash",
        speed=3, accuracy=3,
        cost=_per_sec(0.00026, free_tier=_ALIYUN_QUOTA),
    ),
    # 异步 filetrans：当前代理未实现（需要公网音频 URL），标记 supported=False 让策略跳过
    ("aliyun", "qwen-audio-3.0-asr-flash-filetrans"): _p(
        "aliyun", "qwen-audio-3.0-asr-flash-filetrans",
        speed=5, accuracy=2,
        cost=_per_sec(0.00022, free_tier=_ALIYUN_QUOTA),
        supported=False,
    ),
    ("aliyun", "qwen3-asr-flash-filetrans"): _p(
        "aliyun", "qwen3-asr-flash-filetrans",
        speed=5, accuracy=2,
        cost=_per_sec(0.00022, free_tier=_ALIYUN_QUOTA),
        supported=False,
    ),

    # 豆包（现有）：asr-2.0=seedasr、asr-1.0=bigasr，4.5 元/时 = 0.00125 元/秒（毫秒级计量）
    ("doubao", "asr-2.0"): _p(
        "doubao", "asr-2.0",
        speed=3, accuracy=1,
        cost=_per_sec(0.00125, free_tier="quota"),  # 一次性 20 小时
    ),
    ("doubao", "asr-1.0"): _p(
        "doubao", "asr-1.0",
        speed=3, accuracy=2,
        cost=_per_sec(0.00125, free_tier="quota"),
    ),
    ("doubao", "asr-2.0-concurrent"): _p(
        "doubao", "asr-2.0-concurrent",
        speed=3, accuracy=1,
        cost=_per_sec(0.00125, free_tier="quota"),
    ),
    ("doubao", "asr-1.0-concurrent"): _p(
        "doubao", "asr-1.0-concurrent",
        speed=3, accuracy=2,
        cost=_per_sec(0.00125, free_tier="quota"),
    ),

    # 硅基流动（现有）：SenseVoiceSmall 长期免费
    ("siliconflow", "funaudiollm/sensevoicesmall"): _p(
        "siliconflow", "FunAudioLLM/SenseVoiceSmall",
        speed=2, accuracy=3,
        cost=_free("longterm"),
    ),
    ("siliconflow", "teleai/telespeechasr"): _p(
        "siliconflow", "TeleAI/TeleSpeechASR",
        speed=2, accuracy=3,
        cost=_free("longterm"),
    ),

    # Groq（现有，海外）：whisper；$0.111/时 / $0.04/时；免费层 2000 次/日、28800 秒/日
    # 硬限：单次文件 ≤25MB；最短按 10s 计费（计费门槛，不是硬拒）
    ("groq", "whisper-large-v3"): _p(
        "groq", "whisper-large-v3",
        speed=2, accuracy=1,
        cost=_per_sec(
            0.000031, free_tier="quota",
            min_bill_sec=10.0, currency="USD",
            max_bytes=_GROQ_MAX_BYTES,
        ),
    ),
    ("groq", "whisper-large-v3-turbo"): _p(
        "groq", "whisper-large-v3-turbo",
        speed=2, accuracy=2,
        cost=_per_sec(
            0.000011, free_tier="quota",
            min_bill_sec=10.0, currency="USD",
            max_bytes=_GROQ_MAX_BYTES,
        ),
    ),

    # ---- 已接入平台（docs/asr_models_guide.md）----
    ("stepfun", "stepaudio-2.5-asr"): _p(
        "stepfun", "stepaudio-2.5-asr",
        speed=3, accuracy=2,  # 文档 S2–S3，取保守 S3
        cost=_per_sec(0.000042),
    ),
    ("stepfun", "stepaudio-2.5-asr-stream"): _p(
        "stepfun", "stepaudio-2.5-asr-stream",
        speed=2, accuracy=2,  # 速度等级推断
        cost=_per_sec(0.000333),
    ),
    ("tencent", "16k_zh_en"): _p(  # 录音文件识别·极速版，S1 速度兜底
        "tencent", "16k_zh_en",
        speed=1, accuracy=3,
        cost=_per_sec(  # 3.1 元/时起；最低计费 1 秒；硬限 2h / 100MB
            0.000861, free_tier="quota",
            min_bill_sec=1.0,
            max_duration=_TENCENT_FLASH_MAX_SEC,
            max_bytes=_TENCENT_FLASH_MAX_BYTES,
        ),
    ),
    # 腾讯 16k_zh_en_2.0（异步大模型 2.0）需公网 URL，本轮未接入，不登记档案
    ("baidu", "1537"): _p(  # 短语音识别标准版，硬限 60s
        "baidu", "1537",
        speed=3, accuracy=3,
        cost=_per_call(0.0034, free_tier="quota", max_duration=60.0),  # 约 200 万次
    ),
    ("baidu", "80001"): _p(  # 短语音识别极速版，硬限 60s
        "baidu", "80001",
        speed=2, accuracy=3,
        cost=_per_call(0.0034, free_tier="quota", max_duration=60.0),  # 5 万次；按次价略高于标准版
    ),
}

# 别名：配置/请求中的常见写法 → 档案键
_ALIASES: Dict[_ProfileKey, _ProfileKey] = {
    ("siliconflow", "sensevoice"): ("siliconflow", "funaudiollm/sensevoicesmall"),
    ("siliconflow", "sensevoicesmall"): ("siliconflow", "funaudiollm/sensevoicesmall"),
    ("siliconflow", "sensevoice-small"): ("siliconflow", "funaudiollm/sensevoicesmall"),
    ("siliconflow", "telespeechasr"): ("siliconflow", "teleai/telespeechasr"),
    ("doubao", "1.0"): ("doubao", "asr-1.0"),
    ("doubao", "2.0"): ("doubao", "asr-2.0"),
    ("doubao", "bigasr"): ("doubao", "asr-1.0"),
    ("doubao", "seedasr"): ("doubao", "asr-2.0"),
    ("doubao", "volc.bigasr.sauc.duration"): ("doubao", "asr-1.0"),
    ("doubao", "volc.seedasr.sauc.duration"): ("doubao", "asr-2.0"),
    ("doubao", "volc.bigasr.sauc.concurrent"): ("doubao", "asr-1.0-concurrent"),
    ("doubao", "volc.seedasr.sauc.concurrent"): ("doubao", "asr-2.0-concurrent"),
    ("aliyun", "qwen3-asr-flash-realtime-2025-10-27"): ("aliyun", "qwen3-asr-flash-realtime"),
}


def _norm_key(provider: str, model: str) -> _ProfileKey:
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower().replace(" ", "")
    return p, m


def get_profile(provider: str, model: str) -> ModelProfile:
    """查询模型档案；未收录时返回中性默认档案（成本未知、速度/准确度最末档）。"""
    key = _norm_key(provider, model)
    key = _ALIASES.get(key, key)
    profile = _PROFILES.get(key)
    if profile is not None:
        return profile
    return ModelProfile(
        provider=provider or "",
        model=model or "",
        speed_tier=5,
        accuracy_tier=5,
        cost=CostSpec(),
        supported=True,
    )


def to_cny(profile: ModelProfile, amount: float) -> float:
    """把档案币种金额折成人民币；USD 用固定汇率 USD_TO_CNY，其余原样。"""
    cur = (profile.cost.currency or "CNY").strip().upper()
    if cur == "USD":
        return amount * USD_TO_CNY
    return amount


def is_free_profile(profile: ModelProfile) -> bool:
    """是否可作为免费策略候选：长期免费或带免费额度。"""
    if profile.cost.billing == "free":
        return True
    return profile.cost.free_tier in ("longterm", "quota")


def estimate_cost(profile: ModelProfile, duration: Optional[float]) -> Optional[float]:
    """按音频时长 t（秒）预估单次刊例费用（人民币）。返回 None 表示无法预估。

    规则（docs/asr_models_guide.md 费用优先）：
      - billing=free → None（无刊例价，不进 cost）
      - per_second → 秒单价 × max(t, 最短计费时长)
      - per_call → 按次价（超出硬上限视为不可用，由调用方在 rank 中排除）
      - USD 刊例 × USD_TO_CNY（1:7）后再返回
      - 其他 → None
    免费额度不把费用算成 0。
    """
    cost = profile.cost
    if cost.billing == "free":
        return None
    raw: Optional[float] = None
    if cost.billing == "per_second":
        if duration is None:
            return None
        t = max(float(duration), cost.min_bill_sec)
        raw = cost.price_per_sec * t
    elif cost.billing == "per_call":
        if duration is not None and cost.max_duration > 0 and duration > cost.max_duration:
            return None
        raw = cost.price_per_call
    if raw is None:
        return None
    return to_cny(profile, raw)
