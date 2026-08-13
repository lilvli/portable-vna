# 上位机负责人任务看板

状态仅代表现有证据，不代表真实板卡/RF 验收。

| 里程碑 | 内容 | 状态 | 证据 |
|---|---|---|---|
| M0 | 分层工程、Windows 入口、说明与看板 | COMPLETE | 可复现目录和入口 |
| M1 | PVNA-Link、模拟器、状态机、REST/WS | COMPLETE | 固定向量、状态/保存/API 测试 |
| M2 | Electron/React 完整模拟闭环 | COMPLETE_SIMULATED | Electron 43.4.0 全流程和三尺寸证据 |
| M3-offline | pyserial、会话、Fake/Virtual 设备 | COMPLETE_OFFLINE | 分片/粘包/噪声/CRC/sequence/恢复/UNKNOWN/取消测试 |
| M3-hardware | 真实串口与 FPGA 数字链路 | NOT_HARDWARE_VALIDATED | 需要用户提供实板和安全连接条件 |
| M4 | SOL、保存/回放、S1P、摘要和 UI | COMPLETE_OFFLINE | 数学、API、UI、独立 RI 解析和 E2E |
| QA | 独立全面审查与复验 | PASS | 同一任务复验：P0/P1/P2/P3 未闭合项均为 0 |

## 已满足的强制语义

- [x] 附录 C 固定帧、CRC、噪声/分片/粘包重同步。
- [x] ACK 与最终点分离，sequence 和 identity 双重关联。
- [x] 查询恢复、重复副作用保护和无法证明时 UNKNOWN。
- [x] 保存成功后才推进点进度；完成前校验完整性与 HOLD。
- [x] Raw R/A 与所有 u64/i64 原始整数精确保留。
- [x] SIMULATED、HARDWARE、FAULT、UNKNOWN 明确区分。
- [x] 一端口 SOL 三项模型、绑定拒绝、奇异/有限性检查。
- [x] Touchstone S1P RI 独立解析往返验证。
- [x] 启动默认不连接；真实资源访问计数为 0。

## 发布前剩余门

- [x] 真实 Electron 1920×1080、1366×768、1080×720 截图和交互记录。
- [x] 完整 Python/前端/build/Electron smoke/`git diff --check` 统一复跑。
- [x] 创建独立 Codex 审查任务并生成首轮正式审查报告。
- [x] 修复首轮 P1/P2/P3 并由同一任务复验至 PASS。
- [x] 只提交 `software/host/**`，不暂存 FPGA 现场，不推送远端。

## 延后到真实硬件

- 真实 COM 打开、设备身份和关键 link flags；
- FPGA 协议端、JESD 链路、时钟/SYSREF 和实际数据通路；
- 50 Ω 直采 RF、安全幅度、重复性、动态范围与 SOL 校准件；
- 外部触发电气/时序；
- 最终上下变频模块及其频率切换握手；
- 后续 RTC/转台/波控 host adapter 联合台架。
