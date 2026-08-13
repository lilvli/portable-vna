# Portable VNA Host

当前发布版本：`v0.1.0`（2026-08-13）。变更记录见 [CHANGELOG.md](CHANGELOG.md)，发布说明见 [RELEASE_NOTES_v0.1.0.md](RELEASE_NOTES_v0.1.0.md)。

便携矢网第一阶段上位机。目标是在 Windows 上用 `XCKU5P + FMCADDA-9250-9144` 完成直采复数测量验证；第一阶段不控制上下变频、RTC、转台或波控。

当前软件已经形成可离线验证的闭环：Electron + React 操作台、Python 唯一业务状态机、`SIMULATED` 设备、PVNA-Link V0.1 串口会话、运行保存/回放、一端口 SOL 校准和 Touchstone S1P 导出。串口、FPGA、JESD 和 RF 实板仍未验收。

## 目录

```text
desktop/                 Electron + React + TypeScript
backend/                 Python 状态机、协议、设备适配、校准与保存
scripts/                 Windows 启动和离线验收入口
audits/                  独立审查报告（审查完成后生成）
ARCHITECTURE.md           分层、数据和安全边界
TASKS.md                  里程碑和证据状态
```

协议唯一规范源是 `../../docs/protocol/portable_vna_phase1_protocol_v0.1.md`。

## Windows 开发启动

准备 Python（推荐使用已锁定包、哈希和构建约束的 `uv.lock`）：

```powershell
cd software\host\backend
uv sync --frozen --all-extras
```

`requirements.lock` 保留为当前 Windows 验证环境的可读快照；发布与新环境复现以
`uv.lock` 和精确固定的 `setuptools==80.9.0` 为权威，不使用开放构建工具范围。

准备桌面依赖：

```powershell
cd software\host\desktop
npm.cmd ci
```

从统一入口启动：

```powershell
cd software\host
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

Electron 主进程每次生成随机本机令牌，只在子进程环境和受控 preload 中传递；它启动 Python 服务、等待健康检查，并在退出时先请求 HOLD 再收尾子进程。Python 只绑定 `127.0.0.1`。启动后设备仍为断开/UNKNOWN，必须由用户在界面中显式连接。

开发入口默认使用 `127.0.0.1:8765`；若该端口已被其他进程占用且未显式指定 `-Port`，入口会为本次会话自动选择一个空闲本机端口。需要固定端口时可传入 `-Port 8766`，若固定端口被占用则会在打开界面前明确报错。正常关闭 Electron 不应再把随之结束的 Vite/TypeScript 监听进程误报为启动失败。

`scripts/start-backend.ps1` 仅用于后端单独调试，不是桌面端的正常入口。

## 操作流程

普通测量：

1. 显式连接 `SIMULATED`；需要真实串口时，先显式刷新端口，再选定端口并连接。
2. 设置单点或线性扫频参数；开始后计划被冻结。
3. 只有保存并校验后的 `POINT_RESULT` 才推进点级进度；ACK 仅表示接受。
4. 查看原始 `R/A`、`A/R`、`R/A` 倒数、dB 和相位。
5. 完成、取消或故障收尾均确认 HOLD/RF 关闭；无法证明时显示 `UNKNOWN`。
6. 从运行列表只读回放历史运行并查看摘要/证据边界。

SOL 校准：

1. 在完全相同的冻结参数下分别采集 `OPEN`、`SHORT`、`LOAD`；`measurement_role` 标记标准件角色，SIMULATED 的 `simulation_profile` 只负责生成离线模型。
2. 选择三个已完成且校验有效的标准运行，创建 SOL 校准集。
3. 采集 `DUT`，在 RAW 与校准 S11 之间切换；原始数据不会被覆盖。
4. 导出 50 Ω、`# Hz S RI` 的 `.s1p`。导出内容记录 run、来源和校准身份，并追加 run/calibration/输出哈希派生记录；查看 trace 不改写原始运行。

校准会拒绝来源、端口/路径、频率轴和采集参数不一致；也会拒绝 R 近零、NaN/Inf、奇异或近奇异方程。

## 离线验收

```powershell
cd software\host
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

该入口执行 Python 测试/Ruff/语法检查、前端单测/TypeScript/production build，以及 `software/host` 范围的 `git diff --check`。真实 Electron 视觉烟测的证据单独记录在 `desktop/audits/` 和 `desktop/test-artifacts/`。

## 证据边界

- `SIMULATED`：证明软件状态机、UI、保存、回放、校准和导出闭环；不证明真实串口、FPGA、JESD 或 RF。
- 串口离线层：证明帧/CRC、流式重同步、关联、重放保护、结果恢复、超时与 UNKNOWN；没有打开真实 COM 端口。
- `HARDWARE`：只有用户显式连接真实端口并保存相应证据后才成立；当前没有这样的证据。
- 第一阶段不包含上下变频。最终版本预期加入上下变频，但通过未来 host adapter 组合，不反向侵入当前直采核心。
- RTC、转台和波控是后续 host 侧集成；便携 VNA 核心保持可独立运行。

当前发布物是开发/验证软件，不是已完成真实硬件验收的仪器软件，也未制作安装包。
