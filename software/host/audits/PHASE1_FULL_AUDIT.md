# 便携矢网第一阶段上位机独立全面审查

- 审查日期：2026-08-12（Asia/Shanghai）
- 审查角色：独立审查负责人；与开发责任分离
- 仓库：`D:\workspace\project\protable_vna`
- 开发基线：`367bae95485aba90512d002444eb5dd5fe8d2142`
- 复验提交：`c02b00f13ad1948eeabc71d7eb88001b43809bc7`
- 基线提交：`feat(host): complete offline phase-one workstation`
- 审查方式：实现只读；唯一项目写入为本报告及其父目录
- 复验方式：实现只读；执行纯 Fake/Virtual、临时目录和既有 SIMULATED Electron 证据复核；未修改实现
- 真实资源访问：0
- 最终结论（`c02b00f` 复验后）：**PASS**
- 当前未闭合计数：**P0 0 / P1 0 / P2 0 / P3 0**
- 首轮历史结论（`367bae9`）：`FAIL`，当时为 P0 0 / P1 3 / P2 6 / P3 2

> 第 1～8 节保留首轮审查证据；第 9 节是对修复提交 `c02b00f` 的正式复验，复验结论取代首轮最终判定。

## 1. 首轮结论摘要（历史）

首轮审查不能给出 `PASS` 或 `PASS_WITH_FINDINGS`。当时共确认：

| 等级 | 数量 | 结论 |
|---|---:|---|
| P0 | 0 | 未发现立即导致不可逆真实硬件危险的证据；本轮也没有接触真实硬件 |
| P1 | 3 | 阻断第一阶段正确性与真实性 |
| P2 | 6 | 需在同一开发/复验链中处理 |
| P3 | 2 | 建议修复或补齐证据 |

三个 P1 分别是：

1. 真实 `HARDWARE` 的 Open/Short/Load 采集角色被 API、串口适配器和 UI 同时封死，现有 SOL 只能使用 `SIMULATED` 运行；这使第一阶段真实硬件 SOL 闭环不可达。
2. `START_POINT` ACK 丢失且恢复证据不可用时，协议层正确产生 `MeasurementUnknown`，但串口适配器在 ACK 阶段把它包装成普通 `DeviceError`，运行最终会被归为 `FAILED` 而非 `UNKNOWN`。
3. 主机对最终 `POINT_RESULT` 只校验身份和请求频率；带 JESD 错误等致命 `result_flags`、零参考通道或其他明显无效结果仍可被保存、推进进度并形成 `COMPLETED/VALID` 运行。

因此，本基线只能描述为“具备较完整的 SIMULATED/离线软件闭环和真实 Electron 证据”，不能描述为第一阶段上位机正确性通过，更不能描述为真实串口、FPGA、JESD、RF 或 SOL 实物验收通过。

## 2. 范围、基线与边界

### 2.1 实际读取范围

严格限制在以下入口：

1. 完整读取 `docs/protocol/portable_vna_phase1_protocol_v0.1.md`。
2. 只读取根 `README.md` 的“第一阶段 MVP”“开发阶段边界”“RTC 后续集成”内容。
3. 读取和检查 `software/host/**`，包括源码、测试、启动脚本、锁文件、已有 E2E JSON、四张现有 PNG 以及该目录内既有缓存的只读元数据。

未读取厂商资料正文、RAR/EXE、`software/FPGA/**`、`hardware/**` 或天线平台历史；未读取 RTC 协议正文。

### 2.2 基线核验

- `HEAD`：`367bae95485aba90512d002444eb5dd5fe8d2142`
- `367bae9^{commit}`：同一完整提交
- 在写本报告前，`README.md`、协议源和 `software/host` 相对 `367bae9` 无差异；授权范围内 `git status --short` 为空。
- 协议 SHA-256：`335FFC957BFB2A910E2ADA8F7A3B810CF8DF5F98588C5412B339300B597A0743`
- 根 README SHA-256：`23DA30CA622FA1BEEF4F91F845F05883C3C3BDFD7C00E44D7D8870FE6534FF39`

### 2.3 验证边界

- 未运行会改写 `test-artifacts`、`userData`、运行目录、pytest 临时目录或 build 输出的完整 `verify.ps1`/Electron E2E；这是为了遵守“唯一允许写入正式报告”的首轮边界。
- 已执行两个 `Python -B` 纯内存离线探针；只使用 `FakeTransport` 和 `VirtualPvnaDevice`，没有运行目录，也没有真实串口枚举或打开。
- 已人工检查现有 E2E JSON 和全部四张 PNG；这是对既有证据的独立阅读，不等于本审查任务重新运行了 Electron。
- 既有 E2E JSON 自报 Electron 43.4.0、`SIMULATED`、19/19 步骤 PASS、真实串口/设备访问 0、浏览器预览证据为 false。该自报边界被保留，不提升为真实硬件证据。

## 3. 分级问题

### P0

未发现 P0。由于真实资源访问为 0，本结论不包含对真实 RF 安全闭环的证明。

### P1

#### P1-01：真实 HARDWARE SOL 工作流不可达

**位置**

- `software/host/backend/src/pvna_host/api.py:178-179`
- `software/host/backend/src/pvna_host/domain/serial_device.py:83-85`
- `software/host/backend/src/pvna_host/domain/calibration_manager.py:45-54`
- `software/host/desktop/src/App.tsx:483-489`
- `software/host/backend/tests/test_serial_device_adapter.py:51-68`

**证据/复现**

1. API 对任何非 `simulated` 来源强制 `simulation_profile == "dut"`。
2. `SerialDeviceAdapter.prepare_plan()` 明确拒绝 `open/short/load`。
3. `CalibrationManager.create()` 又要求标准运行的 `plan.simulation_profile` 分别等于 `open/short/load`。
4. UI 的标准运行列表只接受 `item.source === "SIMULATED"`。
5. 现有测试 `test_serial_adapter_rejects_simulation_standard_profile` 把串口标准运行失败固化为预期行为。

这四层条件构成不可满足约束：真实串口运行不能被标记为 O/S/L，而校准求解又不接受未标记为 O/S/L 的运行。

**影响**

第一阶段 MVP 明确要求上位机执行 SOL 校准；即使未来真实串口、FPGA 和 R/A 数据链路全部可用，本基线仍无法采集真实标准件并建立真实设备校准。现有 SOL 只能证明模拟数学与 UI 闭环，阻断第一阶段硬件功能闭环。

**建议**

将“测量角色”与“模拟器生成模型”拆开：

- 增加与来源无关的 `measurement_role = dut/open/short/load`，作为人工连接标准件后的运行标签和审计字段。
- `simulation_profile` 只控制 `SimulatedDevice` 如何生成数据，不能成为硬件 O/S/L 的准入条件。
- HARDWARE 标准运行必须保留操作者确认、设备/FPGA 身份、端口/路径、频率轴、幅度、建立时间、积分数和采集时间。
- UI 标准选择按同一来源和绑定条件筛选；补真实串口 fake 会话的 O/S/L 端到端离线测试，但仍标记为离线协议证据。

#### P1-02：START_POINT ACK 恢复阶段的 UNKNOWN 被降级为 FAILED

**位置**

- `software/host/backend/src/pvna_host/protocol/session.py:368-403`
- `software/host/backend/src/pvna_host/domain/serial_device.py:87-92`
- `software/host/backend/src/pvna_host/domain/serial_device.py:112-122`
- `software/host/backend/src/pvna_host/domain/manager.py:292-297`
- 协议：`docs/protocol/portable_vna_phase1_protocol_v0.1.md:108`、`:271`

**证据/复现**

纯内存探针让 `START_POINT` 的原始响应与一次同帧重发响应均丢失，同时让恢复用 `GET_STATUS` 的两次响应丢失。输出：

```text
ACK_UNKNOWN_EXCEPTION=DeviceError
ACK_UNKNOWN_PRESERVED=False
ACK_UNKNOWN_TEXT=START_POINT acknowledgement and recovery evidence are unavailable
```

协议层在该路径产生 `MeasurementUnknown`；但 `SerialDeviceAdapter.start_point()` 的通用 `except Exception` 把它改成 `DeviceError`。适配器只在后续 `_wait_point()` 阶段把 `MeasurementUnknown` 映射成 `MeasurementResultUnknown`。`RunManager` 只对后者形成 `RunState.UNKNOWN`，普通 `DeviceError` 落入通用异常并形成 `FAILED`。

**影响**

系统无法证明设备是否接受并执行过该点，却向上层表达成“已知失败”。这破坏 ACK/最终完成分离、sequence 恢复和 UNKNOWN 真实性；操作者可能依据错误终态启动下一运行或误判该点没有副作用。

**建议**

- 在 `SerialDeviceAdapter.start_point()` 中显式捕获 `MeasurementUnknown` 并转换为 `MeasurementResultUnknown`，不要经过通用 `DeviceError`。
- 保留 sequence、measurement_id、point_index、ACK 重发和恢复失败原因。
- 增加 RunManager 级测试：丢失两次 START_POINT ACK + 丢失恢复状态响应，最终必须是 `UNKNOWN`；即使随后 HOLD 可确认，也不能把点结果 UNKNOWN 改成 FAILED。

#### P1-03：明显无效的 POINT_RESULT 可被确认、保存并推进进度

**位置**

- `software/host/backend/src/pvna_host/protocol/payloads.py:71-107`
- `software/host/backend/src/pvna_host/protocol/session.py:566-602`
- `software/host/backend/src/pvna_host/domain/manager.py:263-274`
- 协议：`docs/protocol/portable_vna_phase1_protocol_v0.1.md:242`、`:288`、`:365`

**证据/复现**

`ProtocolSession._validate_result()` 只检查 measurement/point 身份和 `requested_frequency_hz`。`RunManager` 再次只检查相同三项，然后立即 `append_point()` 和 `confirmed_points += 1`。没有检查：

- `result_flags` 的 bit2 积分器饱和、bit3 测量窗失锁、bit4 JESD 错误；
- 未定义的结果标志位；
- `R == 0` 或近零导致 `A/R` 不可定义；
- 结果采集参数与冻结请求是否满足已定义约束。

纯内存探针注入正确身份/频率、但 `result_flags=0x0010`（JESD 错误）且 `integration_count=123` 的 `POINT_RESULT`。输出：

```text
INVALID_RESULT_ACCEPTED=True
INVALID_RESULT_FLAGS=0x10
INVALID_RESULT_INTEGRATION_COUNT=123
```

现有测试中出现的 `result_flags` 均为 0，没有覆盖致命标志成功帧；`rg` 只命中 `test_protocol.py:80` 和 `test_calibration_sol.py:224` 的零值构造。

**影响**

主机可把协议已经标明“明显无效”的 JESD/时钟/饱和数据发布为确认点，并最终形成 `COMPLETED/VALID`、曲线和 S1P。若 R 为零，运行仍可完成，之后 trace/export 才失败，造成“运行有效但核心比值不可计算”的自相矛盾。这直接影响第一阶段测量正确性。

**建议**

- 建立唯一的“结果可确认”验证函数，在保存前检查身份、请求频率、结果 flags、保留位、R/A 可用性和冻结采集约束。
- 至少将 `result_flags & 0x001C != 0` 视为失败；未知保留位 fail closed。
- 明确定义削顶 bit0/bit1 是“可保存但质量告警”还是失败，UI/文件必须可见。
- 对 `R == 0` 必须拒绝确认；近零阈值需按输入尺度和标定定义，不能只在后续 SOL/trace 阶段拒绝。
- 增加 flags、R=0、近零 R、未定义 flags、错误参数回显和保存不推进测试。

### P2

#### P2-01：COMPLETED 先写 manifest、后写 summary，恢复时不验证终态发布完整性

**位置**

- `software/host/backend/src/pvna_host/domain/manager.py:301-331`
- `software/host/backend/src/pvna_host/domain/manager.py:337-367`
- `software/host/backend/src/pvna_host/domain/store.py:63-97`

**证据/复现**

`_finish_in_hold()` 先把 `record.state` 设为终态，然后依次写 `manifest.json`、`summary.json`。若进程在两次原子写之间中断，磁盘上会出现 `COMPLETED` manifest，但 summary 缺失或仍是旧内容。重启 `_load_archive()` 对 COMPLETED 仅调用 `validate_complete_run()` 并比较 HOLD、data_validation、points digest；没有要求 summary 存在且与 manifest 一致，也没有检查 `record.confirmed_points == expected_points`。UI 对 summary 的 409 会静默忽略，仍显示 COMPLETED。

**影响**

不完整终态发布可在重启后继续被当作完整运行；证据边界、校准身份或完成点计数可能缺失/矛盾。该路径没有被现有 persistence 测试覆盖。

**建议**

- 使用明确的终态提交协议：先生成并 fsync points/summary，再以最后一个原子 manifest/commit marker 发布终态。
- 恢复时要求 manifest、summary、点数、confirmed_points、digest、HOLD 证据完全一致；任何缺失或矛盾都降为 `UNKNOWN/INVALID`。
- 增加“manifest 已终态但 summary 缺失/不一致”和 confirmed_points 被修改的崩溃恢复测试。

#### P2-02：GET trace 和 export 会改写原始运行的校准身份

**位置**

- `software/host/backend/src/pvna_host/api.py:269-277`
- `software/host/backend/src/pvna_host/domain/calibration_manager.py:83-91`
- `software/host/backend/src/pvna_host/domain/calibration_manager.py:122-131`
- `software/host/backend/src/pvna_host/domain/manager.py:185-189`

**证据/复现**

只读语义的 `GET /runs/{run_id}/trace?calibration_id=...` 调用 `record_calibration_use()`，后者直接覆盖 `RunRecord.calibration_id`，再写 manifest 和 summary。每次选择另一个校准都会覆盖前一次；回看 RAW 不会恢复或追加历史。导出也执行同一覆盖。

**影响**

原始采集运行的持久化元数据会因“查看/导出派生结果”而变化，无法区分采集时事实、曾应用过的校准和当前 UI 选择；这破坏原始证据不可覆盖与审计可重放性。

**建议**

保持 RunRecord 不可变。校准 trace/export 应生成独立派生记录或 append-only 使用事件，至少包含 run_id、run points digest、calibration_id、校准文件 digest、时间、输出 digest 和操作类型。GET 不应产生持久写副作用。

#### P2-03：Electron 允许任意 file: 导航，IPC 未校验调用方页面

**位置**

- `software/host/desktop/electron/main/security.ts:12-20`
- `software/host/desktop/electron/main/index.ts:78-82`
- `software/host/desktop/electron/main/index.ts:139-154`
- `software/host/desktop/electron/preload/index.ts:3-7`
- `software/host/desktop/tests/electron-security.test.ts:17-21`

**证据/复现**

`isAllowedRendererUrl()` 对任意 `url.protocol === "file:"` 返回 true；测试也把任意示例本地文件视为允许。BrowserWindow 的 preload 在导航后仍可暴露 `getBootstrapConfig()` 和 `saveTextFile()`；两个 `ipcMain.handle()` 没有验证 `event.senderFrame.url` 是否为本应用精确入口。

**影响**

如果渲染器被诱导导航到攻击者可控的本地 HTML（例如另一个本地文件或后续 XSS 链），该页面可获得 Bearer token、调用本机 API并触发保存桥。当前 CSP、sandbox、禁新窗口和远程导航拒绝降低了可利用性，因此定为 P2 而不是 P1。

**建议**

- 生产态只允许解析后的精确 `dist/index.html` URL；开发态只允许固定 dev origin。
- `get-bootstrap-config` 和 `save-text-file` 均校验 senderFrame 的 URL、webContents 身份和目标 BrowserWindow。
- 测试必须断言其他 `file:///...` 被拒绝。

#### P2-04：设备协议状态 UNKNOWN 时 UI 仍可能启用测量

**位置**

- `software/host/desktop/src/domain/sweep.ts:57-64`
- `software/host/desktop/src/App.tsx:201-209`
- `software/host/desktop/src/App.tsx:466-478`

**证据/复现**

`operatorStateFromSnapshot()` 在 connected 且 source=HARDWARE 时返回 `HARDWARE`，即使 `device.state == "UNKNOWN"`。测量禁用逻辑只检查 `deviceState` 是否属于 `SIMULATED/HARDWARE`，不检查 `protocolState`。因此页面会同时显示 `DEVICE UNKNOWN` 和激活的 HARDWARE 来源，但“开始扫频”不被前端禁用。后端 `RunManager.start_sweep()` 会拒绝，所以不会直接发出点测量；用户只会在点击后得到错误。

**影响**

安全门禁和禁用原因不一致，UNKNOWN 状态没有在操作层 fail closed，增加误操作和错误恢复压力。

**建议**

来源与可信状态保持两个独立字段；测量按钮必须同时要求 connected、来源已知、协议状态属于允许集合且无 active run。`UNKNOWN/FAULT/BOOT/BUSY` 显式禁用并展示权威原因。补 connected HARDWARE + state UNKNOWN 测试。

#### P2-05：Electron 最终 S1P 保存不是原子发布，且 E2E 未完成保存动作

**位置**

- `software/host/desktop/electron/main/index.ts:140-154`
- `software/host/backend/src/pvna_host/export/touchstone.py:69-90`
- `software/host/desktop/audits/m2-electron-simulated-e2e.md:41`

**证据/复现**

Electron 选择目标后直接 `writeFile(selection.filePath, content)`。后端已有临时文件 + fsync + `os.replace` 的原子帮助函数，但实际桌面保存链未使用。既有 E2E 记录明确说明没有自动确认保存对话框；现有 Electron 单测只验证文件名和文本大小。

**影响**

应用/系统在写入中断时可留下截断的最终 `.s1p`；当前没有端到端证据证明保存后的文件能被独立解析器读取并与服务输出往返一致。

**建议**

在主进程对同目录临时文件执行写入、flush/fsync、独立 S1P 解析校验和原子替换；失败时清理临时文件并保留原目标。使用隔离测试目录完成实际保存与独立解析 E2E，不写用户 Documents。

#### P2-06：Python 锁定不包含可复现的构建工具与哈希

**位置**

- `software/host/backend/pyproject.toml:1-3`
- `software/host/backend/requirements.lock:1-29`
- `software/host/README.md:23-29`

**证据/复现**

运行依赖版本在 `requirements.lock` 中固定，但 `build-system` 使用开放范围 `setuptools>=75`；锁文件没有 setuptools/wheel，也没有 `--require-hashes` 所需哈希。README 随后执行 editable install；pip build isolation 仍可能解析或下载不同构建工具。

**影响**

新的 Windows 环境不一定能离线复现同一构建，构建后端会随时间漂移。Node `package-lock.json` 则为 lockfile v3，根依赖和 Electron 43.4.0 均精确锁定并带 integrity。

**建议**

冻结 Python 构建工具和平台依赖，提供含哈希的锁文件；明确使用 `--require-hashes` 与受控 `--no-build-isolation`，或采用能锁定 build requirements 的工具。统一入口需验证 Python 版本和锁文件身份。

### P3

#### P3-01：键盘/焦点证据只覆盖程序化聚焦一个按钮

**位置**

- `software/host/desktop/electron/main/e2e.ts:113-123`
- `software/host/desktop/src/styles.css:21-24`

**证据/复现**

CSS 有清晰的 `:focus-visible`；E2E 通过 `button?.focus()` 检查“显式连接”一个按钮的 outline。没有真实 Tab/Shift+Tab 序列、焦点顺序、select/input 键盘操作、取消/HOLD 键盘激活或焦点在异步刷新后的保持证据。

**影响**

可确认“存在焦点样式”，不能确认完整键盘可操作性。

**建议**

在真实 Electron 中发送 Tab/Shift+Tab/Enter/Space/Escape 输入，验证关键控件顺序、禁用控件跳过、运行取消与错误后焦点恢复，并在三种尺寸重复。

#### P3-02：完成态顶部 STATUS 文案陈旧，图标题把相位与仅 dB 曲线混在一起

**位置**

- `software/host/desktop/src/App.tsx:286-294`
- `software/host/desktop/src/App.tsx:413`
- `software/host/desktop/src/App.tsx:565-569`
- UI 证据：`electron-1920x1080-simulated-sol.png`、`electron-1366x768-simulated-sol.png`、`electron-1080x720-simulated-sol.png`

**证据/复现**

完成态截图中运行面板已显示 `COMPLETED`，顶部 STATUS 仍显示“运行已创建；服务已冻结参数快照”。run 终态事件会刷新详情，但不更新 message。图面板标题为“dB / 相位结果”，实际 SVG 只有 dB 两条曲线，相位只出现在下方表格。

**影响**

不影响后端终态，但降低状态来源一致性和界面语义清晰度。

**建议**

顶部状态由当前权威 run snapshot 派生，终态事件同步更新；把图标题改为“幅度 dB”，或增加明确的相位图/切换器。

## 4. 逐域检查依据与通过项

以下是本轮确认的正向证据；它们不抵消上述问题，也不自动构成 PASS。

### A. 协议与逻辑

- 帧头、little-endian、CRC-32/ISO-HDLC 和附录 C PING 固定向量在实现与测试中一致。
- `StreamParser` 支持分片、噪声、坏 CRC 后从候选第二字节继续重同步；存在帧内超时重置。
- 请求 sequence 随机非零起始、回绕跳过 0；同帧最多重发一次。
- ACK 与最终事件在类型和 API 事件上分离；事件可先于 ACK 被队列保留。
- 响应/事件丢失后有 `GET_STATUS + READ_LAST_RESULT` 恢复路径；结果阶段不可证明会进入 UNKNOWN。
- 保存顺序为 raw point fsync 后更新 manifest，再发布 `point.confirmed` 和进度；保存失败不会推进点索引。
- 取消、结束和故障收尾均尝试 HOLD，并在无法确认时把运行降为 UNKNOWN；但 P1-02 的 ACK 阶段例外和 P2-01 的终态发布窗口仍需修复。

### B. API 与安全

- `main.py` 拒绝非 `127.0.0.1` 绑定；Electron 构造固定回环 URL。
- Electron 每次用 `randomBytes(32)` 生成 token，通过子进程环境传给 Python；不放入命令行。
- REST Bearer 使用 `secrets.compare_digest`；WS 校验 token 和 Origin；CORS 为固定列表；Uvicorn access log 关闭。
- Pydantic 模型普遍 `extra="forbid"`，核心数值有边界；串口端口必须显式提供。
- Electron 已启用 `contextIsolation=true`、`sandbox=true`、`nodeIntegration=false`、webSecurity、禁 webview、新窗口、外部导航和权限请求；存在 CSP。
- Python stdout/stderr 写审计前对 token 和 Bearer 值脱敏。
- 仍需修复 P2-03 的任意 file URL 与 IPC sender 校验。

### C. 测量与数学

- Raw R/A 在 JSONL 中以十进制字符串精确保留，u64/i64 跨 TypeScript 边界不使用 JS Number。
- `A/R` 是权威比值，`R/A` 明确作为倒数派生；原始点不被 SOL 覆盖。
- 三项 SOL 正向模型、线性化和逆公式正确；已有已知误差项回收、奇异/近奇异、R 近零、NaN/Inf、轴/来源/端口/路径/时间绑定测试。
- 校准应用使用 actual frequency 的严格有序轴，绑定设备/FPGA、来源、端口/路径、幅度、建立时间和积分数。
- Touchstone 使用 50 Ω、`# Hz S RI`，包含 source/device/build/calibration 元数据；存在与渲染器分离的严格 RI 解析器和独立 MHz fixture 往返测试。
- P1-01、P1-03、P2-02、P2-05 说明硬件校准、结果准入、派生审计和最终文件发布仍未闭合。

### D. UI/UX

- 人工检查四张现有真实 Electron PNG：初始 UNKNOWN/DISCONNECTED、显式 SIMULATED、HOLD、COMPLETED、Raw R/A、SOL S11、来源/数据/HOLD 证据均可见。
- 1920×1080、1366×768、1080×720 为 Electron `contentSize`；Windows 125% 缩放使 PNG 物理像素分别约为 2400×1350、1710×960、1350×900。
- 三尺寸无明显横向溢出；1080×720 使用纵向滚动，关键连接/HOLD/运行/结果区在合理滚动范围内。
- 视觉层级、单位（MHz/Q15/μs/ms/dB/°）、空态、禁用按钮、错误 banner、来源和安全 HOLD 证据总体清楚。
- 需要修复 P2-04 和 P3-02，并补 P3-01 的键盘证据。

### E. 测试与可维护性

- 已阅读全部跟踪的后端/桌面测试、Windows 启动脚本、verify 入口和锁文件。
- 现有测试覆盖 CRC、流式分包/粘包、sequence、重复副作用、ACK/最终结果、丢事件恢复、取消、HOLD、保存失败、崩溃恢复、SOL、Touchstone、CORS/WS、Electron 安全选项、运行详情竞态和 64 位字符串。
- 现有 E2E JSON 为 19/19 PASS；桌面审计文档声称前端 34 tests 及 build/lint/typecheck 通过。pytest cache 的 `nodeids` 有 65 项，但 `lastfailed` 含一个已改名的旧 nodeid，因此缓存不能作为当前全绿的独立证明。
- 本审查未独立重跑完整测试/build；测试通过也不会覆盖 P1/P2 的静态和定向复现证据。
- 生成物忽略规则覆盖 `.venv`、node_modules、pytest/ruff cache、dist、PNG 和 E2E userData。

### F. 边界真实性

- 代码和文档持续区分 `SIMULATED`、`HARDWARE`、FAULT、UNKNOWN；summary 和 S1P 注释可见来源。
- 现有 UI 证据明确写出“not serial, FPGA, JESD, or RF validation”。
- `software/host` 实现中没有 RTC/转台/波控/HDF 业务依赖；命中仅在边界说明中。
- 当前证据最多覆盖 H1-H5 的一部分软件/离线闭环；H6-H8 真实硬件、R/A 和标准件均未验证。

## 5. UI 证据清单

| 证据 | Electron 逻辑尺寸/状态 | PNG 物理尺寸 | SHA-256 | 人工检查 |
|---|---|---:|---|---|
| `electron-1920x1080-initial-disconnected.png` | 1920×1080，初始断开 | 2400×1350 | `3A605EB2C054E6409B0A954C613BB4B4FB330D8E74235028D1BCFCD101E5ECA7` | UNKNOWN、DEVICE DISCONNECTED、Service READY、无自动连接、空态与禁用原因可见 |
| `electron-1920x1080-simulated-sol.png` | 1920×1080，SIMULATED SOL | 2400×1350 | `4B33D1D72A7CC2B1334843E149905DA5CAB9E45DE002F68E63E3EF75AB4ABEEE` | COMPLETED、11/11、VALID、HOLD CONFIRMED、校准曲线与 Raw 表可见 |
| `electron-1366x768-simulated-sol.png` | 1366×768，SIMULATED SOL | 1710×960 | `A342AF404F054E18B6432C24DD128D52436561C6A464861360AF16136E546639` | 关键连接/HOLD/结果/曲线可见，无明显横向溢出 |
| `electron-1080x720-simulated-sol.png` | 1080×720，SIMULATED SOL | 1350×900 | `8D91F634E2542647FD4E154AC1459E35D1098579D971154D1E45CEBD38B45A45` | 最小窗口可用，采用纵向滚动，文字与关键按钮未横向裁切 |
| `e2e-report.json` | Electron 43.4.0，19 PASS/0 FAIL | — | `FCC1A39DFAC06787C450492B7ACBC668C72CE2A45D8BEEA7F38DB89782D61B95` | `run_mode=SIMULATED`、`real_serial_accesses=0`、`real_device_accesses=0`、`browser_preview_evidence=false` |

## 6. 实际执行的命令与动作

以下命令均为只读，除创建本报告目录/文件外没有项目写入。为便于复核，路径均相对仓库根，除非写出绝对路径。

### 6.1 基线与范围

```powershell
Get-Item -LiteralPath 'D:\workspace\project\protable_vna'
git rev-parse --verify HEAD
git rev-parse --verify '367bae9^{commit}'
git log -1 --format='%H%n%ci%n%s' HEAD
git status --short -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --stat 367bae9 -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --name-status 367bae9 -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --exit-code 367bae9 -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --check -- software/host
rg --files software/host
git ls-files software/host
git status --short --ignored -- software/host/desktop/test-artifacts/electron-e2e software/host/backend/.pytest-tmp software/host/backend/.pytest_cache software/host/desktop/node_modules
```

### 6.2 文档、源码与测试读取

```powershell
Get-Content -Raw -Encoding UTF8 -LiteralPath 'docs\protocol\portable_vna_phase1_protocol_v0.1.md'
Get-Content -Encoding UTF8 -LiteralPath 'README.md' | Select-Object -Skip 8 -First 22
Get-Content -Encoding UTF8 -LiteralPath 'README.md' | Select-Object -Skip 93 -First 10
Get-Content -Raw -Encoding UTF8 -LiteralPath <git ls-files software/host 中的全部跟踪文本文件>
Get-Content -Encoding UTF8 -LiteralPath <大文件> | Select-Object -Skip <n> -First <n>
rg -n <审查模式> <授权文件>
rg -n -i 'RTC|turntable|positioner|beam control|波控|转台|HDF5|HDF' software/host --glob <排除依赖/缓存内容>
```

实际完整阅读包括所有跟踪的 Python/TypeScript/TSX/CSS/HTML/PowerShell/Markdown/TOML/JSON 测试与实现；`package-lock.json` 用 Node JSON 解析关键锁定项，没有人工输出整文件。

### 6.3 锁文件、E2E 与哈希

```powershell
node -e "const fs=require('fs'); const l=JSON.parse(fs.readFileSync('software/host/desktop/package-lock.json','utf8')); ..."
node -e "const fs=require('fs'); const r=JSON.parse(fs.readFileSync('software/host/desktop/test-artifacts/electron-e2e/e2e-report.json','utf8')); ..."
node -e "const fs=require('fs'); ...读取 backend/.pytest_cache/v/cache/nodeids 与 lastfailed..."
Get-ChildItem -LiteralPath 'software\host\desktop\test-artifacts\electron-e2e' -File
Add-Type -AssemblyName System.Drawing
[System.Drawing.Image]::FromFile(<每张 PNG>)
Get-FileHash -Algorithm SHA256 -LiteralPath <协议、README、E2E JSON 和每张 PNG>
Get-Item -LiteralPath 'software\host\backend\.pytest_cache\v\cache\nodeids','software\host\backend\.pytest_cache\v\cache\lastfailed'
```

图像人工检查使用本地只读图像查看动作逐张打开四个绝对路径 PNG；没有启动浏览器预览，也没有重新启动 Electron。

### 6.4 纯内存定向探针

执行入口：

```powershell
& '.\.venv\Scripts\python.exe' -B -c $code
```

`$code` 使用现有 `FakeTransport`、`VirtualPvnaDevice`、`ProtocolSession` 和 `SerialDeviceAdapter`：

1. 丢弃两次 `START_POINT` 响应和两次恢复 `GET_STATUS` 响应，打印适配器最终异常类型。
2. 注入正确 identity/sequence/frequency、但 `result_flags=0x0010`、`integration_count=123` 的成功 `POINT_RESULT`，打印是否被 `wait_point_result()` 接受。

完整输出已列在 P1-02 和 P1-03。Python 使用 `-B`，未产生 pycache；探针没有创建 RunStore 或任何临时文件。

### 6.5 读取工具失败/回退记录

这些是审查工具问题，不是产品问题：

- 未加引号的 `git rev-parse --verify 367bae9^{commit}` 被 PowerShell 解析后失败；改为单引号后成功。
- 首次 `Get-Content -Raw` 未显式 UTF-8，中文显示乱码；随后用 `-Encoding UTF8` 完整重读。
- PowerShell 5.1 `ConvertFrom-Json` 无法处理 package-lock 的空字符串根键；改用 Node `JSON.parse` 成功。
- 一次 `Get-ChildItem software/host -Recurse` 只枚举了授权目录内大量依赖/缓存文件名和元数据，输出过大；没有读取这些第三方文件内容，随后改用 `git ls-files` 与精确 artifact 路径。
- 一次组合 `rg` 的引号转义失败；改为小型、明确路径命令后成功。

## 7. 真实资源访问计数

| 资源 | 本审查实际访问次数 |
|---|---:|
| 真实串口枚举 | 0 |
| 真实 COM 打开/读/写 | 0 |
| FPGA/JTAG/Vivado 硬件管理器 | 0 |
| JESD/LMK/DAC/ADC 实板 | 0 |
| RF 激励/接收/功率 | 0 |
| 真实 SOL 标准件/负载 | 0 |
| RTC/转台/波控 | 0 |
| 浏览器预览作为 UI 证据 | 0 |
| 本审查重新启动 Electron | 0 |
| 纯内存 Fake/Virtual 协议探针 | 2 |

本轮不能证明真实设备 HOLD、RF-off、串口电气、FPGA 协议端、JESD、R/A 相干性、RF 动态范围、真实 SOL 或外部 Touchstone 仪器兼容性。

## 8. 首轮最终判定与复验入口（历史）

**首轮最终判定：FAIL**

依据：存在 3 个 P1，且均涉及第一阶段核心正确性/真实性。按审查规则，有任何 P0/P1 或影响第一阶段正确性的 P2 均不得给 PASS。

开发负责人修复后，建议同一独立审查任务至少复验：

1. HARDWARE O/S/L 角色与 SIMULATED profile 已解耦，fake 串口标准运行可完成并建立同源校准。
2. START_POINT ACK 与恢复同时丢失时最终为 UNKNOWN，且不会自动续扫或新 sequence 重测。
3. flags bit2-bit4、R=0/近零、未定义 flags 和关键参数不一致不会保存或推进进度。
4. manifest/summary 崩溃窗口恢复为 UNKNOWN/INVALID，GET trace 不再改写原始运行。
5. Electron 精确 file URL/IPC sender 校验、UNKNOWN 禁用原因、原子 S1P 保存和键盘序列证据闭合。
6. 再运行完整 Python/前端/lint/typecheck/build/真实 Electron SIMULATED E2E，并保留真实资源访问 0；之后才进入单独授权的真实串口/硬件台架。

首轮审查没有修改实现、没有执行 `git add/commit/push`。

## 9. 修复提交 `c02b00f` 独立复验

### 9.1 复验基线、范围与结论

- 复验日期：2026-08-12（Asia/Shanghai）。
- `HEAD`、`c02b00f^{commit}` 均为 `c02b00f13ad1948eeabc71d7eb88001b43809bc7`；父审查基线为 `367bae95485aba90512d002444eb5dd5fe8d2142`。
- 写报告前，`README.md`、协议源和 `software/host/**` 的授权范围内 `git status --short` 为空；开发已停止编辑。
- 完整重读协议源；根 README 只重读“第一阶段 MVP”“开发阶段边界”“RTC 后续集成”；实现与证据只读取 `software/host/**`。
- 未读取或访问 `software/FPGA/**`、`hardware/**`、厂商资料、RAR/EXE、RTC 协议正文、天线平台历史或其他禁止范围。
- 未连接、枚举或访问真实串口、FPGA、JESD、RF、RTC、转台或波控；真实资源访问为 0。
- 未重新启动 Electron；对开发侧最新 Electron 43.4.0 SIMULATED 证据执行了 JSON/lifecycle 一致性检查、E2E 实现审查和四张 PNG 逐张人工检查。浏览器预览不计证据。
- 本复验未修改实现，没有执行 `git add/commit/push`；项目内唯一主动修改仍为本报告。

**复验结论：PASS。** 原 3 个 P1、6 个 P2、2 个 P3 均闭合；未发现新 P0/P1、影响第一阶段正确性的 P2 或其他阻断项。该 PASS 只适用于 `c02b00f` 的第一阶段上位机软件、离线协议路径和既有真实 Electron SIMULATED 证据，不是串口、FPGA、JESD、RF 或真实 SOL 验收。

### 9.2 原问题逐项复验

#### P1-01：measurement_role 与 simulation_profile 解耦 — PASS

**位置**

- `software/host/backend/src/pvna_host/domain/models.py:56-77`
- `software/host/backend/src/pvna_host/api.py:177-200`
- `software/host/backend/src/pvna_host/domain/serial_device.py:83-86`
- `software/host/backend/src/pvna_host/domain/calibration_manager.py:42-65`、`:216-241`
- `software/host/desktop/src/App.tsx:398-410`、`:525`、`:543-546`
- `software/host/backend/tests/test_serial_device_adapter.py:231-294`

**独立证据/复现**

1. `SweepPlan.measurement_role` 是冻结运行来源角色；`simulation_profile` 仍存在，但只由 `SimulatedDevice` 选择离线生成模型。
2. API 对 serial/HARDWARE 保留显式 `measurement_role`，同时把线上无意义的 `simulation_profile` 归一为 `dut`；`SerialDeviceAdapter.prepare_plan()` 不因 O/S/L 角色发送额外串口字节。
3. 校准建立按 `record.plan.measurement_role` 校验 OPEN/SHORT/LOAD，并由 `AcquisitionBinding` 约束 source、device/build、端口路径、频率轴与采集参数。
4. 定向测试在同一个 `FakeTransport + VirtualPvnaDevice + SerialDeviceAdapter` 上依次完成 HARDWARE 标记的 O/S/L/DUT 四次运行，创建 `source=HARDWARE` 的 SOL，并得到一条 CALIBRATED trace；测试名称和结果见 9.5，未打开真实 COM。
5. UI 对 HARDWARE 显示“串口软件层（实板未验收）”和“标准件由操作员接入”，不会把 Fake/Virtual 或 SIMULATED 证据描述成实板。

**判定/影响**

原不可满足约束已解除，真实串口软件路径未来可以承载 O/S/L 角色；本轮只证明同一虚拟字节链路可达，不证明真实板卡或标准件。

#### P1-02：START_POINT ACK 与恢复证据同时丢失保持 UNKNOWN — PASS

**位置**

- `software/host/backend/src/pvna_host/protocol/session.py:71-94`、`:431-473`
- `software/host/backend/src/pvna_host/domain/serial_device.py:88-100`
- `software/host/backend/src/pvna_host/domain/manager.py:282-293`
- `software/host/backend/tests/test_serial_device_adapter.py:120-182`

**独立证据/复现**

1. `START_POINT` 的两次线上发送使用同一 sequence、同一 payload；两次 ACK 均超时后只查询状态，不以新 sequence 重测。
2. 若 `GET_STATUS` 恢复证据也不可用，`MeasurementUnknown` 携带原 `sequence/measurement_id/point_index`。
3. 串口适配器在 ACK 阶段单独捕获并映射为 `MeasurementResultUnknown`，没有再包装成普通 `DeviceError`；运行管理器将其收尾为 `RunState.UNKNOWN`。
4. 定向测试丢弃两次 START_POINT ACK 与两次恢复状态响应，最终运行为 UNKNOWN；错误文本保留三个关联字段，随后 HOLD 仍可独立确认。

**判定/影响**

无法证明的测量不再降级成 FAILED，也不会推进点索引或自动续扫；原 P1 正确性问题闭合。

#### P1-03：唯一结果准入与无效点不保存 — PASS

**位置**

- `software/host/backend/src/pvna_host/protocol/payloads.py:93-100`
- `software/host/backend/src/pvna_host/protocol/session.py:262-300`、`:490-529`、`:643-653`
- `software/host/backend/src/pvna_host/domain/manager.py:253-269`
- `software/host/backend/tests/test_serial_session.py:243-370`
- `software/host/backend/tests/test_serial_device_adapter.py:184-229`

**独立证据/复现**

1. 公共 `validate_point_result()` 是来源无关的单一准入函数；实时事件、`READ_LAST_RESULT` 恢复和 RunManager 保存前均调用它。`PointResult.decode()` 还先拒绝非零 reserved 字段。
2. bit2/bit3/bit4（`0x001C`）为致命标志；bit5 及以上未知位 fail closed。bit0/bit1 只作为削顶质量标志保留在 `PointResult`、points API 和 `points.jsonl`，不转成致命失败。
3. 准入拒绝 `|R|² <= 1`（零或不超过一个 accumulator LSB 的参考）、零积分、零实际频率、积分数与冻结 START_POINT 不一致、`duration_us > max_duration_ms * 1000`，并继续校验 identity 与请求频率。
4. 所有检查发生在 `append_point()` 和 `confirmed_points += 1` 之前；定向测试证明 JESD 错误点最终 FAILED、confirmed=0、points=[]，而 bit0/1 削顶点可保存且 flags 原样保留。
5. 全量测试还覆盖 bit2～4、未知 flags、R=0/±1 LSB、错误积分回显、超时回显、事件/重读两条路径。

**判定/影响**

明显无效结果不能再形成确认点或 COMPLETED/VALID 运行。当前一个 accumulator LSB 是软件最小准入阈值；真实硬件阶段仍需依据 ADC/积分尺度和噪声标定收紧工程阈值，这属于 H6～H8 台架工作，不是本轮实板证明。

#### P2-01：终态发布顺序与重启一致性 — PASS

**位置**

- `software/host/backend/src/pvna_host/domain/manager.py:297-365`
- `software/host/backend/src/pvna_host/domain/store.py:63-147`
- `software/host/backend/tests/test_persistence.py:52-102`

**独立证据/复现**

1. `_finish_in_hold()` 先原子写并 fsync `summary.json`，再以原子 `manifest.json` 写作为终态提交标记。
2. COMPLETED 重启会重算 points digest，并要求 confirmed count、expected count、实际点数、顺序/身份/频率/source、HOLD、VALID、manifest digest 以及 summary 的关键字段与 manifest 完全一致。
3. 已有定向测试证明 summary 缺失和 manifest confirmed count 篡改均恢复为 UNKNOWN/INVALID。
4. 本复验另行在临时目录把 summary.state 从 COMPLETED 改成 FAILED；重启输出为：

```text
SUMMARY_MISMATCH_STATE=UNKNOWN
SUMMARY_MISMATCH_VALIDATION=INVALID
SUMMARY_MISMATCH_ERROR=completed archive validation failed: terminal summary state does not match manifest
```

**判定/影响**

终态提交窗口和重启伪完成路径已闭合；不完整或矛盾的归档不会继续显示为 COMPLETED。

#### P2-02：GET trace 只读，导出追加可追溯派生记录 — PASS

**位置**

- `software/host/backend/src/pvna_host/domain/calibration_manager.py:82-195`
- `software/host/backend/src/pvna_host/api.py:278-300`
- `software/host/backend/tests/test_api.py:176-208`

**独立证据/复现**

1. `trace()` 只从 immutable raw run 构造派生值，不再改写 `RunRecord.calibration_id`、manifest 或 summary。
2. `export_s1p()` 追加 fsync 的 `derivations.jsonl`，记录 derivation_id、UTC、operation、run_id、run points SHA-256、calibration_id、校准文件 SHA-256、data kind 和输出 SHA-256。
3. 定向 API 测试在 GET trace 后复读原运行，`calibration_id` 仍为 null；导出后可读到独立 `EXPORT_S1P` 派生记录。

**判定/影响**

查看不再产生写副作用；导出历史可重放且不会覆盖原始采集事实。

#### P2-03：Electron 精确入口与 IPC 调用方身份 — PASS

**位置**

- `software/host/desktop/electron/main/security.ts:29-63`
- `software/host/desktop/electron/main/index.ts:63-80`、`:98-106`、`:165-184`
- `software/host/desktop/tests/electron-security.test.ts:23-60`

**独立证据/复现**

1. 生产只允许 `pathToFileURL(dist/index.html).href` 的精确相等值，其他 file、query、fragment 均拒绝。
2. 开发只在请求 URL 的 origin 精确为 `http://127.0.0.1:5173` 时启用；`localhost`、其他端口和 file 均拒绝。
3. 两个 IPC handler 都要求 senderFrame 是 mainFrame，event.sender 与活动窗口 webContents ID 相同，`BrowserWindow.fromWebContents()` 的窗口 ID 相同，且 frame URL 满足同一策略。
4. BrowserWindow 继续保持 contextIsolation、sandbox、nodeIntegration=false、webview 禁用、权限全拒绝和外部导航/新窗口拒绝。

**判定/影响**

任意本地页面取得 token 或保存桥的原路径已关闭；未发现修复引入的 IPC 放宽。

#### P2-04：HARDWARE 不可信协议状态前端 fail closed — PASS

**位置**

- `software/host/desktop/src/domain/sweep.ts:60-85`
- `software/host/desktop/src/App.tsx:468-479`、`:535-537`
- `software/host/desktop/tests/sweep.test.ts:122-146`

**独立证据/复现**

测量按钮现在同时要求服务 READY、显式连接、可信 source、协议状态属于 HOLD/IDLE/RESULT_READY、无活动运行且无其他 busy 操作。connected HARDWARE + UNKNOWN/FAULT/BOOT/BUSY 均返回包含实际协议状态的可见禁用原因；单测逐一覆盖四种状态。

**判定/影响**

前端操作门禁与后端状态机一致，UNKNOWN 不再出现可点击的开始测量按钮。

#### P2-05：S1P 同目录原子保存与独立语义复核 — PASS

**位置**

- `software/host/desktop/electron/main/save.ts:35-128`
- `software/host/desktop/electron/main/index.ts:171-183`
- `software/host/desktop/tests/electron-save.test.ts:34-76`
- `software/host/desktop/electron/main/e2e.ts:271-301`
- `software/host/desktop/test-artifacts/electron-e2e/e2e-report.json`

**独立证据/复现**

1. 主进程文件边界只接受绝对 `.s1p`，用独立严格解析器接受一端口 `S RI` 和正有限参考阻抗、正且严格递增频率、有限 RI 数据。
2. 临时文件以 `wx` 创建在目标同目录，写入后 file fsync、关闭、读回并重新解析，对 reference/frequency/RI 执行语义等价比较，最后 rename 原子替换；异常清理临时文件。
3. 前端 49 个测试中的真实隔离目录用例覆盖已有目标替换、MHz→Hz RI 往返和注入替换失败后原目标不变。
4. Electron E2E 的隔离保存步骤取得真实校准导出，使用同一保存 primitive 写出并独立解析为 11 点、50 Ω，记录输出 SHA-256 后清理隔离目录。

**判定/影响**

截断最终文件和失败覆盖原目标的路径已闭合；该证据仍只证明本机文件发布，不证明外部仪器兼容。

#### P2-06：Python 冻结锁与构建约束 — PASS

**位置**

- `software/host/backend/pyproject.toml:1-3`、`:17-24`、`:44-45`
- `software/host/backend/uv.lock:5-6`、`:201-231`、`:488-495`
- `software/host/README.md:22-30`
- `software/host/scripts/verify.ps1:15-23`

**独立证据/复现**

1. `build-system.requires`、dev extra 和 uv build constraint 均精确固定 `setuptools==80.9.0`。
2. `uv.lock` 含 33 个包节点：1 个本地 editable root 与 32 个 registry 包；registry sdist/wheel 条目均带 SHA-256。setuptools 80.9.0 的 sdist 和 wheel 都有哈希。
3. README 使用 `uv sync --frozen --all-extras`，明确 `uv.lock` 为权威；`requirements.lock` 仅为人读环境快照。
4. verify 在 uv 可用时执行 `uv lock --check`；本复验另以 `uv lock --check --offline` 得到 `Resolved 33 packages in 1ms`。
5. `uv.lock` SHA-256：`A230E217AED2B91B1E3D75994B9AFDA703A9F6A46C83270A8C6E9EBCCD8E1477`。

**判定/影响**

开放构建后端与无哈希解析漂移已消除；新环境仍应严格执行 README 的 frozen sync，而不是把旧 `.venv` 当作锁身份。

#### P3-01：真实 Electron 键盘与三尺寸焦点证据 — PASS

**位置**

- `software/host/desktop/electron/main/e2e.ts:84-119`、`:181-238`、`:303-331`
- `software/host/desktop/test-artifacts/electron-e2e/e2e-report.json`

**独立证据/复现**

E2E 的 `sendKey()` 通过真实 `webContents.sendInputEvent` 发送 keyDown/char/keyUp；覆盖 Tab、Shift+Tab、Enter、Space、Escape，包含连接、HOLD、单点、取消、错误后焦点恢复、禁用控件跳过和可见 focus 样式。1920×1080、1366×768、1080×720 均记录保存控件的 Tab→Shift+Tab→Tab→Escape 焦点证据。

**判定/影响**

原“只程序化 focus 一个按钮”的证据缺口已闭合。

#### P3-02：顶部权威完成状态与图语义 — PASS

**位置**

- `software/host/desktop/src/domain/sweep.ts:87-95`
- `software/host/desktop/src/App.tsx:488-505`、`:565`
- `software/host/desktop/electron/main/e2e.ts:263-269`

**独立证据/复现**

顶部 STATUS 由当前 `RunSnapshot` 派生，完成态显示 run_id、COMPLETED、confirmed/total 和 HOLD CONFIRMED；不再停留在“运行已创建”。图标题为“幅度曲线 · dB”，右侧明确“相位见下表”。四张 PNG 人工检查与 E2E 文本断言一致。

**判定/影响**

状态来源与图表语义不一致问题已闭合。

### 9.3 UI/Electron 证据独立检查

`e2e-report.json` 解析结果为 24/24 PASS、0 FAIL，记录 Electron 43.4.0、`run_mode=SIMULATED`、`real_serial_accesses=0`、`real_device_accesses=0`、`browser_preview_evidence=false`。`lifecycle-e2e.jsonl` 记录：服务只绑定 `127.0.0.1:18765`，token 仅经子进程环境传递，退出时 `shutdown.hold=CONFIRMED`，随后服务停止；未发现 token/Bearer 值写入证据文件。

| 证据 | SHA-256 | 独立检查结果 |
|---|---|---|
| `e2e-report.json` | `AF75ECCC228829DDA0653FEDAE05639F47846857F710D30E5070A33DC9342D27` | 24/24 PASS；SIMULATED；真实资源 0；浏览器预览 false |
| `lifecycle-e2e.jsonl` | `CE6FB7DE57A84931DF4D85804228F567EC891A0CB13213F07CA631CE4733B2D5` | loopback、token transport、HOLD CONFIRMED、service stopped 顺序一致 |
| `electron-1920x1080-initial-disconnected.png` | `2C51CB1BE3AF4BB1F5D1C5E4D574CD661D58C1A6A1FE76BADAA3095BB1F32A1A` | UNKNOWN、DEVICE DISCONNECTED、Service READY；无伪连接 |
| `electron-1920x1080-simulated-sol.png` | `8ECA2AE3E9920A5A9D4B892F1E580A3EC6804FF279AC359331720AAE14446D32` | COMPLETED 11/11、HOLD CONFIRMED、CALIBRATED、SIMULATED 边界、dB 标题 |
| `electron-1366x768-simulated-sol.png` | `2564155B4C1415D43C5FC795E678DDF76440A94719E1CDE5EC1C3B4EC2918B3A` | 无横向裁切；状态/按钮/单位清晰；保存按钮可见焦点 |
| `electron-1080x720-simulated-sol.png` | `B84D8C9AB860A404CCF82905C2B25D5F8C448C574BD414BAF7E796823490ED60` | 最小尺寸使用纵向滚动，无横向裁切；权威状态、曲线和焦点仍可读 |

Windows 显示缩放为 125%，因此 PNG 像素分别为 2400×1350、1710×960、1350×900；E2E 代码和 JSON 记录的是 Electron `contentSize` 1920×1080、1366×768、1080×720，两者一致而非尺寸冒充。

### 9.4 回归、边界与剩余事项

- Python 全量：82 passed；唯一警告是第三方 Starlette/httpx deprecation，未发现本项目失败。
- Python 定向：7 passed，逐项覆盖 P1-01、P1-02、P1-03、P2-01、P2-02。
- Python Ruff：check PASS；format check PASS（35 files）。
- 前端：6 files / 49 tests PASS；lint PASS；TypeScript noEmit typecheck PASS。
- Python lock：`uv lock --check --offline` PASS，解析 33 packages。
- `git diff --check 367bae9..c02b00f -- software/host` PASS。
- 未重新执行会覆盖正式 E2E artifact 的 `npm.cmd run e2e:simulated`，也未把浏览器 preview 当证据；最新 Electron 证据通过实现、JSON、lifecycle、hash 和 PNG 五向核对。
- `software/host` 中 RTC/转台/波控/HDF 命中仅为边界说明，无第一阶段业务依赖；RTC 仍是后续 host adapter，不进入 VNA 核心。
- 当前审查未闭合事项：无。仍待单独授权与台架完成的项目是串口电气、FPGA 协议端、JESD、R/A 相干性、RF、真实 SOL 标准件和外部 Touchstone 工具兼容性；这些没有被本 PASS 冒充为已验收。

### 9.5 实际执行命令与结果

基线与改动范围：

```powershell
git rev-parse HEAD
git rev-parse 'c02b00f^{commit}'
git rev-parse '367bae9^{commit}'
git status --short -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --name-status 367bae9..c02b00f -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --stat 367bae9..c02b00f -- README.md docs/protocol/portable_vna_phase1_protocol_v0.1.md software/host
git diff --check 367bae9..c02b00f -- software/host
```

规范、实现与证据读取：

```powershell
Get-Content -Raw -Encoding UTF8 docs\protocol\portable_vna_phase1_protocol_v0.1.md
Get-Content -Encoding UTF8 README.md | Select-Object -Skip 8 -First 23
Get-Content -Encoding UTF8 README.md | Select-Object -Skip 93 -First 10
git diff --unified=60 367bae9..c02b00f -- software/host/backend/src/pvna_host/protocol/session.py
git diff --unified=60 367bae9..c02b00f -- software/host/backend/src/pvna_host/domain/manager.py software/host/backend/src/pvna_host/domain/store.py software/host/backend/src/pvna_host/domain/calibration_manager.py
git diff --unified=35 367bae9..c02b00f -- software/host/backend/tests/test_serial_device_adapter.py software/host/backend/tests/test_serial_session.py software/host/backend/tests/test_persistence.py software/host/backend/tests/test_api.py
rg -n 'measurement_role|simulation_profile|MeasurementUnknown|result_flags|summary|manifest|derived|atomic|UNKNOWN|INVALID' software/host/backend/src software/host/backend/tests software/host/desktop/src software/host/desktop/electron software/host/desktop/tests
Get-Content -Raw -Encoding UTF8 software/host/desktop/test-artifacts/electron-e2e/e2e-report.json
Get-Content -Raw -Encoding UTF8 software/host/desktop/test-artifacts/electron-e2e/lifecycle-e2e.jsonl
Get-FileHash -Algorithm SHA256 docs/protocol/portable_vna_phase1_protocol_v0.1.md,README.md,software/host/backend/uv.lock,software/host/backend/requirements.lock,software/host/desktop/test-artifacts/electron-e2e/e2e-report.json,software/host/desktop/test-artifacts/electron-e2e/lifecycle-e2e.jsonl,software/host/desktop/test-artifacts/electron-e2e/*.png
```

Python 独立执行（均设置 `PYTHONDONTWRITEBYTECODE=1`，pytest cache 禁用，basetemp 位于审查工作目录）：

```powershell
& .\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider -o 'addopts=' --basetemp C:\Users\15565\Documents\Codex\2026-08-12\portable-vna-phase1-audit\work\python-temp\pytest-base
# 82 passed, 1 third-party deprecation warning

& .\.venv\Scripts\python.exe -B -m pytest -vv -p no:cacheprovider -o 'addopts=' --basetemp C:\Users\15565\Documents\Codex\2026-08-12\portable-vna-phase1-audit\work\python-targeted\pytest-base `
  tests/test_serial_device_adapter.py::test_single_fake_serial_captures_roles_and_builds_sol_calibration `
  tests/test_serial_device_adapter.py::test_start_ack_unknown_keeps_run_unknown_after_safe_hold `
  tests/test_serial_device_adapter.py::test_fatal_result_is_rejected_before_save_or_progress `
  tests/test_serial_device_adapter.py::test_clipping_quality_flags_are_saved_without_becoming_fatal `
  tests/test_persistence.py::PersistenceTests::test_terminal_manifest_without_matching_summary_recovers_unknown `
  tests/test_persistence.py::PersistenceTests::test_confirmed_point_count_tamper_recovers_unknown `
  tests/test_api.py::ApiTests::test_sol_trace_and_touchstone_export_round_trip
# 7 passed

& .\.venv\Scripts\python.exe -B -c $code  # $code 创建临时 SIMULATED 完成运行、篡改 summary.state、重启复读；输出见 P2-01
& .\.venv\Scripts\python.exe -B -m ruff check src tests
& .\.venv\Scripts\python.exe -B -m ruff format --check src tests
uv.exe lock --check --offline
```

前端独立执行：

```powershell
npm.cmd test
# 首次受限沙箱因不能创建 node_modules/.vite-temp 瞬时配置而 EPERM；同一命令获准写临时配置后：6 files / 49 tests PASS
npm.cmd run lint
npm.cmd run typecheck
```

图像检查使用本地只读图像查看器逐张打开 4 个 PNG；未启动浏览器或 Electron。没有执行 build、真实端口枚举或真实资源命令。

### 9.6 本次复验真实资源访问计数

| 资源 | 本次复验实际访问次数 |
|---|---:|
| 真实串口枚举 | 0 |
| 真实 COM 打开/读/写 | 0 |
| FPGA/JTAG/Vivado 硬件管理器 | 0 |
| JESD/LMK/DAC/ADC 实板 | 0 |
| RF 激励/接收/功率 | 0 |
| 真实 SOL 标准件/负载 | 0 |
| RTC/转台/波控 | 0 |
| 外部网络依赖解析 | 0（uv 使用 `--offline`；npm 使用既有 node_modules） |
| 浏览器预览作为 UI 证据 | 0 |
| 本复验重新启动 Electron | 0 |
| 既有 Electron PNG 人工检查 | 4 |
| Python pytest 执行 | 89 test executions（82 全量 + 7 定向复跑） |
| 额外临时目录恢复探针 | 1 |

### 9.7 最终判定

**最终判定：PASS**

判定依据：原 3 P1、6 P2、2 P3 均有实现级、失败路径、定向测试或真实 Electron 证据闭环；所有 P1/P2 正确性问题已关闭，当前未发现新阻断项。测试通过不是唯一依据，本节还核验了源代码控制流、保存/恢复顺序、不可变数据边界、Electron 调用方身份、UI 状态来源、实际 E2E 实现和图像内容。

该 PASS 的边界保持不变：`SIMULATED`、Fake/Virtual、离线锁检查和真实 Electron UI 只能证明上位机软件闭环；真实串口、FPGA、JESD、RF、真实 SOL 与 RTC 联合台架仍为 0 次访问、未验收。
