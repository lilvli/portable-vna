# 上位机架构与边界

## 1. 运行结构

```text
Electron main（生命周期与安全边界）
  ├─ 生成一次性随机令牌
  ├─ 启动/监控 Python 子进程（127.0.0.1）
  ├─ 退出前请求 HOLD 并记录结果
  ├─ 安全保存 S1P 的原生对话框
  └─ contextBridge 最小能力
          │
React renderer（只展示与发请求）
  ├─ 参数、状态、曲线、表格、日志
  ├─ REST 权威快照
  └─ WebSocket 变化通知；断线后 REST 重同步
          │  localhost REST + WebSocket / Bearer token
Python service（唯一业务状态机）
  ├─ 冻结计划、点级事务、取消、超时和安全收尾
  ├─ append-only Raw R/A、派生记录、manifest、summary、校验哈希
  ├─ SOL 校准与 S1P 派生输出
  └─ DeviceAdapter
       ├─ SimulatedDevice
       └─ SerialDeviceAdapter → ProtocolSession → ByteTransport
                                  ├─ SerialTransport（显式 open）
                                  └─ FakeTransport + VirtualPvnaDevice（离线测试）
```

## 2. 核心约束

1. 启动不枚举、不连接串口，也不退出 HOLD；初态是断开/UNKNOWN。
2. Python 独占业务状态和设备所有权。React/Electron 不自行宣布测量完成。
3. `START_POINT/ACCEPTED` 与 `POINT_RESULT/POINT_FAILED` 分离；同一线上帧最多有界重发一次，重复副作用由 sequence 去重。
4. 响应/事件丢失后先用 `GET_STATUS + READ_LAST_RESULT` 恢复；仍无法证明时是 `UNKNOWN_RESULT`，运行状态为 `UNKNOWN`。
5. 原始点先追加、刷盘，再发 `point.confirmed` 和推进进度。完整数据校验及安全 HOLD 都成功后，先原子发布 summary，最后以 terminal manifest 作为提交标记发布 `COMPLETED`。
6. 所有跨 TypeScript 边界的 u64/i64 原始值使用十进制字符串；绘图转换会检查 JavaScript 安全范围。
7. Raw `R/A` 不被派生或校准结果覆盖。协议点中的比值定义为 `A/R`；`R/A` 是 UI/服务明确计算的倒数。
8. 来源和状态分别表达：`SIMULATED/HARDWARE` 是证据来源，`FAULT/UNKNOWN` 是可信状态；离线结果不能冒充硬件验收。
9. 上下变频、RTC、转台、波控和天线 HDF 不进入第一阶段核心。

## 3. 运行数据

每个运行目录包含：

- `manifest.json`：冻结计划、run/measurement 身份、来源、状态和安全证据；
- `points.jsonl`：逐点追加的请求/实际频率、时间戳、来源与精确 Raw R/A；
- `summary.json`：完成/故障状态、点数、校验哈希和证据边界；必须与终态 manifest 一致；
- `.calibrations/derivations.jsonl`：S1P 等派生输出对 run/calibration/输出哈希的追加式审计记录。

服务启动时只读重开运行目录。中断中的运行恢复为 `UNKNOWN`；声称完成但点文件、哈希或 HOLD 证据不一致的运行也恢复为 `UNKNOWN/INVALID`。

计划中的 `measurement_role=dut/open/short/load` 是来源无关的操作者测量角色；`simulation_profile` 只决定模拟器数据模型，不能限制真实硬件标准件采集。校准集单独原子保存，绑定来源、端口、路径、device/FPGA 身份、频率轴、幅度、稳定时间、积分次数和标准采集时间。查看校准 trace 是只读操作，导出只追加派生审计记录，不覆盖原始 RunRecord。

## 4. 安全模型

- HTTP/WS 只接受回环地址，API 需要随机 Bearer token，CORS/WS Origin 使用固定白名单。
- Electron 使用 `contextIsolation: true`、`sandbox: true`、禁用 Node renderer；preload 仅暴露启动配置和文本保存。
- Python access log 关闭，运行日志对令牌做脱敏；令牌不写入文件。
- 串口构造和导入没有资源访问，只有用户显式连接触发 `open()`。
- 退出、取消、断连和故障路径优先确认 HOLD/RF-off；无法确认不会降级成 STOPPED/COMPLETED。

## 5. 后续适配边界

上下变频和 RTC 均通过 host adapter 与冻结点计划、READY/BUSY/DONE、外部触发和复数结果接口组合。VNA 底层不依赖 RTC 协议。真实设备阶段还需逐级验证串口电气、FPGA 协议、JESD、触发时序、直采 RF、校准件和最终上下变频链路。
