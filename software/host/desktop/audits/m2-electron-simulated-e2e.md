# M2 Electron + SIMULATED 验证记录

日期：2026-08-12（Asia/Shanghai）
证据级别：SIMULATED 软件闭环；真实串口/设备访问 0
Electron：43.4.0，Windows x64

自动截图模式固定使用 Electron 软件渲染，以避免测试主机 GPU 运行库状态影响证据；正式应用运行不强制关闭硬件加速。

## 结果

- Electron 主进程受控启动 Python，服务仅绑定 `127.0.0.1:18765`。
- 初始 `DISCONNECTED / UNKNOWN`，无自动连接和陈旧故障 banner。
- 显式 SIMULATED 连接后为 HOLD；手动 HOLD 与应用退出 HOLD 均确认 RF 关闭。
- 单点、401 点扫频进度/完成、5000 点运行取消、降序频率错误均通过。
- OPEN/SHORT/LOAD 各 11 点，创建 SOL 后完成 DUT 校准 S11 回看；Raw R/A 未被覆盖。
- 运行列表、当前运行、事件日志和旧请求竞态保护通过。
- 真实 `Tab/Shift+Tab/Enter/Space/Escape` 覆盖连接、HOLD、单点、取消、错误焦点恢复及三尺寸焦点路径。
- 1920x1080、1366x768、1080x720 均无横向溢出，HOLD、开始扫频、保存 `.s1p` 按钮矩形有效。
- 校准 S1P 在隔离目录完成同目录临时写、fsync、独立 RI 解析及原子替换，验证后清理。

机器可读报告：`../test-artifacts/electron-e2e/e2e-report.json`。

## 门禁

- `npm run lint`：通过，0 warning。
- `npm run typecheck`：通过。
- `npm test`：6 files，49 tests，通过。
- `npm run build`：通过。
- `git diff --check -- software/host/desktop`：无空白错误。

## 截图

- `../test-artifacts/electron-e2e/electron-1920x1080-initial-disconnected.png`
- `../test-artifacts/electron-e2e/electron-1920x1080-simulated-sol.png`
- `../test-artifacts/electron-e2e/electron-1366x768-simulated-sol.png`
- `../test-artifacts/electron-e2e/electron-1080x720-simulated-sol.png`

PNG 是本地生成证据，按 `.gitignore` 不提交。浏览器预览未计入上述证据。

## 未验证边界

未访问真实 COM、FPGA、LMK/JESD、DAC/ADC 或 RF；未证明真实设备 HOLD、幅相、SOL 准确度或 Touchstone 对外部仪器兼容性。原生保存对话框仍不自动确认，以避免产生用户文件；E2E 改在隔离目录调用同一原子保存原语并完成独立解析，然后清理证据文件。
