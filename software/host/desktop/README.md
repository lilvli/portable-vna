# Portable VNA Desktop

Windows 优先的 Electron + React/TypeScript 操作台。Electron 主进程独占本机 Python 服务生命周期；Python 仍是唯一业务状态机和设备所有者。

## 运行闭环

- 每次 Electron 启动用密码学随机数生成一次性令牌，只经子进程环境和最小 preload 配置传递；不放入命令行或日志。
- Python 只绑定 `127.0.0.1`。可用 `PVNA_PYTHON` 指定解释器；开发态默认发现 `../backend/.venv/Scripts/python.exe`，缺失时 UI 明确显示服务不可用。
- 运行数据、生命周期审计与服务工作目录位于 Electron `userData`。
- 应用退出先有界请求 HOLD，验证 `HOLD + rf_output_enabled=false` 后再终止自己启动的服务；无法确认时审计为 `UNKNOWN`。
- 初始不自动连接设备。SIMULATED 与 HARDWARE 都必须显式连接；串口软件层已离线完成，但实板尚未验收。

UI 支持单点、线性扫频、步进/点数预估、进度、取消、运行列表与只读回放、服务事件日志、Raw R/A、A/R、R/A、dB/相位，以及 SIMULATED O/S/L 采集、SOL 校准迹线和安全保存 Touchstone `.s1p`。响应中的 u64 频率和原始积分值保持十进制字符串；绘图前执行 `BigInt` 安全范围检查。

## 命令

```powershell
npm install
npm run lint
npm run typecheck
npm test
npm run build
npm start
```

固定依赖记录在 `package-lock.json`；Electron 固定为 `43.4.0`，oxlint 固定为 `1.78.0`。`node_modules` 和 Electron 二进制不提交。

离线真实桌面闭环：

```powershell
npm run e2e:simulated
```

该命令实际启动 Electron 43.4.0 窗口，由主进程拉起 Python 服务，使用隔离 `test-artifacts/electron-e2e/user-data/run-<pid>`，只运行 SIMULATED。报告写入 `test-artifacts/electron-e2e/e2e-report.json`；PNG 与隔离 userData 被忽略，不作为源码提交。

## 安全边界

- Renderer 无 Node、无 webview，启用 context isolation、sandbox 与 web security；新窗口、外部导航和权限请求默认拒绝。
- preload 只暴露启动配置和受控文本保存；服务令牌不持久化。
- WebSocket 仅通知，REST 快照是权威状态；运行详情用选择代次门避免旧异步响应覆盖当前运行。
- SIMULATED 软件闭环不证明串口、FPGA、JESD、DAC/ADC、RF、真实 SOL 或实板安全行为。
