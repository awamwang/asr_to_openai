# 启动与自启动参考

> 面向 Windows 桌面（本项目所在环境）。所有命令均可在 PowerShell 中直接执行。

## 0. 前置条件

```powershell
# Python 3.10+（项目在 3.12 下开发）；进入项目根目录
cd G:\Projects\Tools\asr_to_openai

# 安装依赖（首次或依赖变更后）
python -m pip install -r requirements.txt
```

依赖：fastapi、uvicorn、dashscope、pyyaml、httpx、websocket-client、python-multipart（pytest 系为测试用）。

## 1. 前台启动（调试 / 日常）

```powershell
python main.py
```

启动日志会打印监听地址、已启用服务商、auto 顺序。日志写入 `asr_proxy.log`（按天滚动，默认保留 7 天）。

- 改端口：`$env:ASR_PORT = "8005"; python main.py`（或改 `config.yaml` 的 `server.port`）
- 无 `config.yaml` 时自动回退 `config.example.yaml`，但服务商会全部禁用（无密钥）。

启动后可立即验证：

```powershell
curl.exe http://127.0.0.1:8004/health
curl.exe http://127.0.0.1:8004/v1/models
```

## 2. 后台启动（不占当前窗口）

```powershell
Start-Process -FilePath "python" -ArgumentList "main.py" -WorkingDirectory "G:\Projects\Tools\asr_to_openai" -WindowStyle Hidden
```

查看进程 / 停止：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*main.py*' } | Select-Object ProcessId, CommandLine
# 停止：Stop-Process -Id <PID>
```

## 3. 添加自启动的建议（按推荐程度排序）

> 前提：先确认 `python main.py` 能正常启动、`/health` 返回 200。自启动均以**项目根目录为工作目录**，否则相对日志路径会落到别处。
> 所有方案只负责开机拉起进程，**不自动重试崩溃**——如需守护，用任务计划程序的「重启失败任务」或 nssm 的 Restart 参数。

### 方案 A：任务计划程序 · 登录时启动（推荐日常使用）

不需要管理员权限（登录触发），界面可查、可禁用，支持崩溃重启策略。

```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "main.py" -WorkingDirectory "G:\Projects\Tools\asr_to_openai"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "ASR-OpenAI-Proxy" -Action $action -Trigger $trigger -Settings $settings -Description "ASR to OpenAI 本地代理"
```

- 想彻底隐藏控制台窗口：把 `-Execute` 换成 `pythonw`（`C:\Users\Administrator\.pyenv\pyenv-win\versions\3.12.0\pythonw.exe` 之类），`-Argument "main.py"`。注意 pythonw 下日志仍写文件，但控制台不可见。
- 立即测试：`Start-ScheduledTask -TaskName "ASR-OpenAI-Proxy"`；`Get-ScheduledTask -TaskName "ASR-OpenAI-Proxy"` 查看状态。
- 删除：`Unregister-ScheduledTask -TaskName "ASR-OpenAI-Proxy" -Confirm:$false`

### 方案 B：启动文件夹快捷方式（最简单，登录即起）

```powershell
$startup = [Environment]::GetFolderPath("Startup")
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$startup\ASR-OpenAI-Proxy.lnk")
$sc.TargetPath = "C:\Users\Administrator\.pyenv\pyenv-win\versions\3.12.0\pythonw.exe"
$sc.Arguments = "main.py"
$sc.WorkingDirectory = "G:\Projects\Tools\asr_to_openai"
$sc.Save()
```

### 方案 C：nssm 注册为 Windows 服务（开机即服务、无需登录，最稳）

用 nssm（https://nssm.cc/）把 `python main.py` 做成系统服务，可自启、自动重启、随系统开机：

```powershell
# 首次安装（管理员 PowerShell）：
nssm install ASROpenAIProxy "C:\Users\Administrator\.pyenv\pyenv-win\versions\3.12.0\python.exe" "main.py"
nssm set ASROpenAIProxy AppDirectory "G:\Projects\Tools\asr_to_openai"
nssm set ASROpenAIProxy AppExit Default Restart   # 崩溃自动重启
nssm set ASROpenAIProxy Start SERVICE_AUTO_START
nssm start ASROpenAIProxy

# 管理：nssm status/restart/stop/remove ASROpenAIProxy
```

服务方式启动时环境变量（如代理 `GROQ_PROXY` 指向的本地代理端口）需在服务内自行配置或改为写进 `config.yaml`。

### 方案 D：系统内置任务计划 · 开机时（System 权限）

`AtStartup` 触发器适合「开机即服务、不等登录」，但需要管理员权限，且用 `python`（控制台）会无窗口常驻，管理不便。一般场景用方案 A 即可。

## 4. 自启动后的验证清单

```powershell
# 1) 服务/任务是否在运行
Get-ScheduledTask -TaskName "ASR-OpenAI-Proxy" | Select-Object State
nssm status ASROpenAIProxy          # 方案 C

# 2) 端口是否在监听
Get-NetTCPConnection -LocalPort 8004 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Listen' }

# 3) 接口是否可用
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8004/health   # 期望 200

# 4) 日志是否有报错
Get-Content G:\Projects\Tools\asr_to_openai\asr_proxy.log -Tail 20
```

## 5. 与闪电说面板对接

| 字段 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:8004`（带不带 `/v1` 均可） |
| API Key | 随便填（本地无鉴权） |
| 模型 | `auto` / `strategy-cost` / `aliyun/auto` / `doubao/asr-2.0` 等（用 `report_status.py` 输出的完整列表） |

> 注意：`8004` 是局域网内**无鉴权**的本地服务，仅建议监听 `127.0.0.1`；如需局域网使用，把 `server.host` 改成 `0.0.0.0` 并在前面加一层鉴权（如反代）。
