# asr_to_openai 项目待办

## 暂缓

- [ ] **规划多 worker 改造**
  - 状态：待开始（先不订方案、不实施）
  - 记录日：2026-11-08
  - 阻塞：关键决策未拍板
  - 重启前需确认：
    1. 目标：本机高并发 / 对外多用户 / 主要绕开单核 CPU 瓶颈
    2. 路由状态（`auto` 粘性、failover 冷却等）：进程本地即可 / 必须跨 worker 共享
  - 相关：`main.py` 当前 `uvicorn.run(app, …)` 单进程；`ProviderRouter` 进程内状态见 `asr_proxy/providers/__init__.py`
