# 便携矢网第一阶段通信协议 V0.1

**协议名称：** PVNA-Link<br>
**状态：** 第一阶段联调基线（verification baseline）<br>
**日期：** 2026-08-12<br>
**适用范围：** XCKU5P 系统板 + FMCADDA-9250-9144 直采验证原型<br>
**文档许可：** CC-BY-4.0

> 本协议优先解决“容易实现、容易观察、容易恢复、不会把错误结果当成成功”四件事。第一阶段只实现上位机驱动的单频点复数测量和扫频闭环；RTC、转台、波控、上下变频和连续高速流不进入本版核心。

## 1. 目标与边界

### 1.1 第一阶段必须完成

- 上位机由用户显式选择串口并连接，软件启动时不得自动连接或自动打开射频输出。
- FPGA 接收一个频点测量请求，生成同一频点、同一相位基准下的参考通道 `R` 和反射通道 `A` 复数积分结果。
- 上位机按频点循环构成扫频，保存原始复数 `R/A`，计算 `m=A/R`，并为后续 SOL 校准和 Touchstone 导出保留数据。
- 链路能够区分请求已接受、测量完成、测量失败、取消、设备故障和结果未知。
- 支持状态查询、重复帧去重、CRC 校验、最近结果重读和安全 `HOLD`。
- 上位机必须先支持 `SIMULATED` 设备，再接真实串口，以便在 FPGA 数据链路完成前验证 UI、状态机、校准和保存流程。

### 1.2 本版不实现

- RTC V0.7、转台、波控和天线自动测试业务状态机。
- 上下变频、本振、滤波器切换和宽带射频前端控制。
- FPGA 内部整段扫频；第一阶段由上位机逐点发送 `START_POINT`。
- ADC 原始高速采样流上传；本版只返回点级复数积分结果。
- 多客户端、多设备并行、远程网络访问、固件升级和寄存器任意读写。
- 对 `5–100 MHz` 以外频段或真实整机指标作保证。

### 1.3 规范词

“必须”表示互操作和验收要求；“应”表示推荐实现；“可以”表示可选能力。没有在 `GET_INFO.capabilities` 中声明的能力，上位机不得推断为存在。

## 2. 系统分层与数据所有权

| 层 | 第一阶段职责 | 明确禁止 |
|---|---|---|
| Electron 主进程 | 启动/停止 Python 服务、窗口生命周期、文件选择与受控 IPC | 直接控制 FPGA 或承担测量状态机 |
| React + TypeScript | 参数输入、连接状态、曲线、校准向导、日志和结果查看 | 直接访问串口、计算权威校准结果 |
| Python 测控服务 | 唯一业务状态机、串口协议、扫频、SOL、保存、回放、导出 | 把 ACK 当成测量完成 |
| FPGA / VNA 设备 | 单点配置、激励、同步 R/A 采集、复数积分、硬件状态和安全关闭 | 拥有扫频文件、SOL 数学、RTC 业务逻辑 |

上位机内部采用本机 REST + WebSocket；Python 服务是唯一设备所有者。React 不直接持有串口句柄。设备协议与本机 API 分开版本化。

## 3. 物理传输基线

| 项目 | V0.1 要求 |
|---|---|
| 初始链路 | USB-UART/虚拟串口，实际板载桥接芯片和接口在上电盘点后记录 |
| 串口最低能力 | `115200 baud, 8 data bits, no parity, 1 stop bit, no flow control` |
| 推荐加速 | `921600 baud`，仅在设备能力声明且用户显式选择时使用 |
| 字节序 | 所有多字节整数均为 little-endian；有符号数为二进制补码 |
| 最大负载 | `4096` 字节；V0.1 不分片，超过即拒绝 |
| 连接规则 | 用户显式连接；不得自动波特率扫描、自动重连后恢复射频或自动发送测量命令 |
| 帧间隔 | 无语义；解析器以帧头、长度和 CRC 定界 |
| 帧内超时 | 建议 `200 ms`；超时后丢弃未完成候选帧并重新搜索帧头 |

同一 PVNA-Link 帧以后可以承载于 TCP 字节流，但 V0.1 不要求以太网实现。UDP 不适合本阶段的简单可靠串口语义。

## 4. 帧格式

每帧由固定 20 字节头、可变负载和 4 字节 CRC 组成：

```text
+----------------------+-------------------+----------------+
| Header: 20 bytes     | Payload: 0..4096  | CRC32: 4 bytes |
+----------------------+-------------------+----------------+
```

### 4.1 固定头

| 偏移 | 长度 | 字段 | V0.1 定义 |
|---:|---:|---|---|
| 0 | 2 | `magic` | 固定字节 `50 56`，ASCII `PV` |
| 2 | 1 | `version_major` | `00` |
| 3 | 1 | `version_minor` | `01` |
| 4 | 1 | `message_class` | `01` 请求；`02` 响应；`03` 事件 |
| 5 | 1 | `opcode` | 命令或事件编号 |
| 6 | 2 | `flags` | 位定义见下；未定义位必须为 0 |
| 8 | 2 | `status` | 请求必须为 0；响应/事件使用状态码 |
| 10 | 2 | `header_size` | 固定 `20`，线上为 `14 00` |
| 12 | 4 | `sequence` | 非零 32 位请求序号；响应原样回显 |
| 16 | 4 | `payload_length` | `0..4096` |

`flags`：bit0 `RESPONSE_REQUIRED`，请求必须置 1；bit1 `REPLAYED_RESPONSE`，设备仅在返回缓存响应时置 1；bit2 `MORE_FRAGMENTS` 在 V0.1 必须为 0；其余保留为 0。

### 4.2 CRC

- 算法：CRC-32/ISO-HDLC（常见名称 CRC-32/IEEE）。
- 参数：poly `0x04C11DB7`，refin/refout `true`，init `0xFFFFFFFF`，xorout `0xFFFFFFFF`。
- 覆盖范围：从 `magic` 开始的完整 20 字节头与全部负载，不含 CRC 字段。
- 线上顺序：计算结果按 32 位 little-endian 发送。
- CRC 错误帧必须静默丢弃并增加 `crc_errors`；不得依据可能已损坏的序号执行或回复。

### 4.3 解析与重同步

接收端搜索 `50 56`，随后检查版本、`header_size=20`、类、保留位和 `payload_length<=4096`，再读取完整帧并校验 CRC。校验失败时从候选帧第二个字节之后继续搜索，不得执行负载。响应帧和事件帧必须整帧发送，字节不得交叉。

## 5. 事务、序号与重复请求

- 上位机每次打开串口后随机选择一个非零起始序号，再单调递增；回绕时跳过 0。
- 设备对每个合法请求先返回一个响应；耗时测量的响应只表示 `ACCEPTED`，最终结果由事件报告。
- 设备至少缓存最近 8 个请求的 `sequence + 整帧 CRC + 响应`，直到缓存被新请求替换或设备复位。
- 收到相同序号且整帧 CRC 相同的请求时，设备不得重复产生副作用，只返回缓存响应，并置 `REPLAYED_RESPONSE`。
- 相同序号但帧内容不同必须返回 `DUPLICATE_MISMATCH`，不得执行。
- 查询类请求可以用完全相同的线上帧有限重发；状态改变类请求只允许以相同序号、相同字节重发一次。
- `START_POINT` 响应超时后，主机不得用新序号“再测一次”；先查询 `GET_STATUS`，必要时使用 `READ_LAST_RESULT`。无法证明结果时记录 `UNKNOWN`。

设备一次只允许一个活动测量。事件可以在请求/响应之间出现，但帧本身不得交叉；上位机必须按 `message_class + sequence + measurement_id` 关联。

## 6. 设备状态机

| 值 | 状态 | 含义 | 射频输出 |
|---:|---|---|---|
| 0 | `BOOT` | 复位、时钟或链路初始化中 | 必须关闭 |
| 1 | `HOLD` | 安全保持；拒绝新测量 | 必须关闭 |
| 2 | `IDLE` | 硬件就绪，可接受测量 | 关闭 |
| 3 | `BUSY` | 正在建立频点、激励或采集 | 仅在有界测量窗内允许打开 |
| 4 | `RESULT_READY` | 最近结果已锁存，可读取或开始下一点 | 关闭 |
| 5 | `FAULT` | 设备故障，需要检查或清故障 | 必须关闭 |

设备上电后进入 `BOOT`，完成最低自检后进入 `HOLD`。`EXIT_HOLD` 只有在时钟、JESD 和数据通路满足设备实现要求时才能进入 `IDLE`。`START_POINT` 只允许从 `IDLE` 或 `RESULT_READY` 进入 `BUSY`。

`ENTER_HOLD` 是安全命令：若设备正在测量，先有界取消、关闭激励，再返回响应。`CANCEL` 只取消当前测量并回到 `IDLE`；响应必须在激励已关闭后发出。

## 7. 命令表

| Opcode | 名称 | 请求负载 | 成功响应 | V0.1 |
|---:|---|---:|---|---|
| `0x01` | `PING` | 0 | `OK`，0 字节 | 必须 |
| `0x02` | `GET_INFO` | 0 | `OK`，64 字节设备信息 | 必须 |
| `0x03` | `GET_STATUS` | 0 | `OK`，48 字节状态 | 必须 |
| `0x04` | `ENTER_HOLD` | 0 | `OK`，0 字节 | 必须 |
| `0x05` | `EXIT_HOLD` | 0 | `OK`，0 字节 | 必须 |
| `0x10` | `START_POINT` | 32 | `ACCEPTED`，8 字节回显 | 必须 |
| `0x11` | `READ_LAST_RESULT` | 0 | `OK`，80 字节点结果 | 必须 |
| `0x12` | `CANCEL` | 8 | `OK`，8 字节回显 | 必须 |
| `0x13` | `CLEAR_FAULT` | 0 | `OK`，0 字节 | 必须 |

未列出的 opcode 必须返回 `UNKNOWN_OPCODE`。V0.1 不提供任意寄存器读写命令，以免上位机绑定未冻结的板级寄存器表。

### 7.1 `GET_INFO` 响应负载（64 字节）

| 偏移 | 类型 | 字段 | 说明 |
|---:|---|---|---|
| 0 | `u8` | `protocol_major` | 0 |
| 1 | `u8` | `protocol_minor` | 1 |
| 2 | `u8` | `firmware_major` | 固件版本 |
| 3 | `u8` | `firmware_minor` | 固件版本 |
| 4 | `u16` | `firmware_patch` | 固件版本 |
| 6 | `u16` | `hardware_revision` | 0 表示尚未登记 |
| 8 | `u64` | `device_id` | 设备唯一号；验证样机可暂为 0 |
| 16 | `u32` | `fpga_build_id` | 可复现构建编号或提交短标识映射 |
| 20 | `u32` | `capabilities` | 能力位 |
| 24 | `u32` | `max_payload` | V0.1 不得大于 4096 |
| 28 | `u32` | `max_integration_count` | 最大积分样本数 |
| 32 | `u64` | `min_frequency_hz` | 当前实现可接受的最小值，不代表实测指标 |
| 40 | `u64` | `max_frequency_hz` | 当前实现可接受的最大值，不代表实测指标 |
| 48 | `u64` | `timebase_hz` | `fpga_timestamp_ticks` 的时钟频率 |
| 56 | `u32` | `min_settle_us` | 最小建立时间 |
| 60 | `u32` | `max_settle_us` | 最大建立时间 |

能力位：bit0 USB-UART；bit1 支持 921600；bit2 返回点级复数 R/A；bit3 软件单点；bit4 HOLD/CANCEL；bit5 提供硬件链路状态；bit8 外部单点触发（后续）；bit9 Ethernet（后续）；bit10 变频前端就绪证据（后续）；bit31 `SIMULATED`。真实 FPGA 不得置 bit31。

### 7.2 `GET_STATUS` 响应负载（48 字节）

| 偏移 | 类型 | 字段 | 说明 |
|---:|---|---|---|
| 0 | `u8` | `device_state` | 状态机值 |
| 1 | `u8` | `rf_output_enabled` | 0/1；非 BUSY 时必须为 0 |
| 2 | `u8` | `last_result_valid` | 0/1 |
| 3 | `u8` | `reserved` | 0 |
| 4 | `u32` | `link_flags` | bit0 时钟锁定；bit1 SYSREF 已见；bit2 ADC JESD 就绪；bit3 DAC JESD 就绪；bit4 数据通路就绪 |
| 8 | `u32` | `active_measurement_id` | 无活动测量时为 0 |
| 12 | `u32` | `active_point_index` | 无活动测量时为 `0xFFFFFFFF` |
| 16 | `u32` | `last_measurement_id` | 无结果时为 0 |
| 20 | `u32` | `last_point_index` | 无结果时为 `0xFFFFFFFF` |
| 24 | `u16` | `last_error` | 最近状态码 |
| 26 | `u16` | `reserved` | 0 |
| 28 | `u32` | `frames_rx` | 合法头帧接收计数，可回绕 |
| 32 | `u32` | `crc_errors` | CRC 错误计数，可回绕 |
| 36 | `u32` | `reserved` | 0 |
| 40 | `u64` | `uptime_ms` | 设备启动毫秒数，可回绕 |

`link_flags` 是真实证据，不允许为了 UI 友好而固定写 1。模拟器必须在上位机来源字段中标记 `SIMULATED`。

### 7.3 `START_POINT` 请求负载（32 字节）

| 偏移 | 类型 | 字段 | 说明 |
|---:|---|---|---|
| 0 | `u32` | `measurement_id` | 本次运行内非零且不重复 |
| 4 | `u32` | `point_index` | 从 0 开始 |
| 8 | `u64` | `frequency_hz` | 整数 Hz |
| 16 | `u16` | `stimulus_amplitude_q15` | `0..32767`；具体模拟幅度由硬件标定 |
| 18 | `u16` | `measure_flags` | bit0 `RESET_NCO_PHASE`；其余为 0 |
| 20 | `u32` | `settle_us` | 激励建立等待 |
| 24 | `u32` | `integration_count` | 非零；不得超过设备能力 |
| 28 | `u32` | `max_duration_ms` | 本点硬超时上限；非零 |

设备必须完整校验参数并拒绝越界，不能静默钳位。成功接受后立即返回 `ACCEPTED`，8 字节负载依次回显 `measurement_id` 和 `point_index`，随后进入 `BUSY`。最终只能产生一个 `POINT_RESULT` 或 `POINT_FAILED` 事件。

### 7.4 `CANCEL` 请求负载（8 字节）

负载依次为 `measurement_id:u32`、`point_index:u32`。只有与当前活动点完全匹配才执行；没有匹配活动点时返回 `RESULT_NOT_FOUND`。成功响应在安全关闭后回显相同 8 字节。

`READ_LAST_RESULT` 不清除结果。设备只允许在接受下一次 `START_POINT` 时覆盖最近结果，并应在复位后清除有效标志。

## 8. 点结果与事件

### 8.1 事件表

| Opcode | 名称 | 状态 | 负载 |
|---:|---|---|---:|
| `0x80` | `POINT_RESULT` | `OK` | 80 字节点结果 |
| `0x81` | `POINT_FAILED` | 失败状态码 | 16 字节失败信息 |
| `0x82` | `DEVICE_FAULT` | 失败状态码 | 8 字节故障信息 |

点事件的 `sequence` 必须等于对应 `START_POINT` 请求序号。无对应请求的设备故障事件使用序号 0。

### 8.2 `POINT_RESULT` / `READ_LAST_RESULT` 负载（80 字节）

| 偏移 | 类型 | 字段 | 说明 |
|---:|---|---|---|
| 0 | `u32` | `measurement_id` | 请求回显 |
| 4 | `u32` | `point_index` | 请求回显 |
| 8 | `u64` | `requested_frequency_hz` | 请求值 |
| 16 | `u64` | `actual_frequency_hz` | NCO/时钟量化后的实际值 |
| 24 | `s64` | `r_i_acc` | R 通道 I 积分结果 |
| 32 | `s64` | `r_q_acc` | R 通道 Q 积分结果 |
| 40 | `s64` | `a_i_acc` | A 通道 I 积分结果 |
| 48 | `s64` | `a_q_acc` | A 通道 Q 积分结果 |
| 56 | `u32` | `integration_count` | 实际积分数 |
| 60 | `u16` | `accumulator_right_shift` | 积分前/中为防溢出的公共右移位数 |
| 62 | `u16` | `result_flags` | 质量标志 |
| 64 | `u64` | `fpga_timestamp_ticks` | 结果锁存时刻 |
| 72 | `u32` | `duration_us` | 从接受到锁存的耗时 |
| 76 | `u32` | `reserved` | 0 |

点级复数值为积分器输出，不是 ADC 高速原始样本。主机可按 `x_avg = x_acc × 2^accumulator_right_shift / integration_count` 还原公共尺度平均值；`A/R` 的公共比例会相消。V0.1 要求 R、A 使用相同积分数和公共右移量。

`result_flags`：bit0 R 输入出现削顶；bit1 A 输入出现削顶；bit2 积分器饱和；bit3 测量窗内时钟失锁；bit4 JESD 错误；其余为 0。bit2～bit4 出现时设备应优先发送 `POINT_FAILED`，不得把明显无效数据当作成功点。

### 8.3 失败负载

`POINT_FAILED` 负载为 `measurement_id:u32`、`point_index:u32`、`stage:u16`、`error:u16`、`detail:u32`。阶段：1 频率设置；2 建立；3 激励；4 采集；5 积分；6 关闭。`DEVICE_FAULT` 为 `stage:u16`、`error:u16`、`detail:u32`。

## 9. 状态码

| 值 | 名称 | 主机处理 |
|---:|---|---|
| `0x0000` | `OK` | 操作已完成 |
| `0x0001` | `ACCEPTED` | 请求已接受，等待最终事件 |
| `0x0101` | `BAD_VERSION` | 停止兼容性协商 |
| `0x0102` | `BAD_LENGTH` | 修正实现，不重试变体 |
| `0x0103` | `BAD_FLAGS` | 修正保留位 |
| `0x0104` | `UNKNOWN_OPCODE` | 能力不支持 |
| `0x0105` | `INVALID_PARAM` | 用户参数错误 |
| `0x0106` | `INVALID_STATE` | 刷新状态后决定 |
| `0x0107` | `BUSY` | 不推进点索引 |
| `0x0108` | `NOT_READY` | 显示真实链路证据 |
| `0x0109` | `RESULT_NOT_FOUND` | 结果不存在或标识不匹配 |
| `0x0201` | `TIMEOUT` | 当前点失败，进入恢复 |
| `0x0202` | `CANCELLED` | 当前点未完成 |
| `0x0203` | `CLOCK_UNLOCKED` | HOLD，检查时钟 |
| `0x0204` | `JESD_NOT_READY` | HOLD，检查 JESD |
| `0x02FF` | `HARDWARE_FAULT` | 进入 FAULT，RF 必须关闭 |
| `0x0301` | `DUPLICATE_MISMATCH` | 协议错误，停止运行 |
| `0x03FF` | `INTERNAL_ERROR` | 保存证据，停止运行 |

主机状态 `UNKNOWN` 不在线上传输：当响应/事件丢失且查询也无法证明结果时，由 Python 服务记录。`UNKNOWN` 绝不能降级显示成已停止或已完成。

## 10. 标准工作流

### 10.1 连接与预检

1. 用户选择 `COM` 口和波特率并点击连接。
2. 主机发送 `PING → GET_INFO → GET_STATUS`。
3. 主机校验协议版本、能力和当前状态；真实设备必须确认关键 `link_flags`。
4. 只有用户开始测量后才发送 `EXIT_HOLD`；应用启动和仅查看历史数据不得打开测量能力。

### 10.2 单点与扫频

1. Python 服务分配 `measurement_id` 和从 0 开始的 `point_index`。
2. 发送 `START_POINT`，收到 `ACCEPTED` 只把点标为“正在测量”。
3. 收到并校验 `POINT_RESULT` 后保存原始 R/A，再计算 `m=A/R`。
4. 只有保存成功后，扫频完成点数才加 1。
5. 失败、缺失、重复、序号错、频率错或保存失败均不得推进点索引。
6. 扫频结束或异常退出时发送 `ENTER_HOLD` 并确认 RF 已关闭。

扫频主进度为 `validated_and_saved_points / expected_points`，不能把 ACK 或 UI 收到曲线更新当作完成。

### 10.3 SOL 校准

Open、Short、Load 必须使用完全相同的有序频率轴和采集参数。Python 服务保存每个标准的原始 R/A 与 `m=A/R`，求解一端口三项误差模型；校准结果和幅相是派生量，不能覆盖原始点。校准会话必须记录设备信息、FPGA 构建、频率轴、幅度、建立时间、积分数和采集时间。

### 10.4 超时、取消与恢复

- 请求响应超时：同一线上帧最多重发一次；`START_POINT` 不使用新序号重发。
- 结果事件超时：先 `GET_STATUS`；若结果已锁存则 `READ_LAST_RESULT`。
- 用户取消：发送匹配活动标识的 `CANCEL`；完成安全确认后停止推进。
- 串口断开：运行进入 `UNKNOWN`，重新连接后先预检状态和最近结果；不得自动续扫。
- 应用退出：优先发送 `ENTER_HOLD`；如果无法确认，明确提示“设备安全状态未知”。

## 11. 本机 REST API V1

Python 服务只绑定 `127.0.0.1`。Electron 主进程为每次启动生成随机访问令牌并传给服务；REST 使用 `Authorization: Bearer <token>`。React 只能通过 Electron 提供的受控配置获得地址和令牌，生产版本不得开放远程监听。

所有路径以 `/api/v1` 开头，JSON 包含 `schema_version: "pvna.api.v1"`。建议最小端点：

| 方法与路径 | 作用 | 第一里程碑 |
|---|---|---|
| `GET /health` | 服务存活与版本 | 必须 |
| `GET /device/ports` | 枚举串口，不自动打开 | 必须 |
| `POST /device/connect` | 显式连接 `simulated` 或 `serial` | 必须 |
| `POST /device/disconnect` | 安全断开 | 必须 |
| `GET /device/status` | 统一设备状态与证据 | 必须 |
| `POST /device/hold` | 安全 HOLD | 必须 |
| `POST /runs/sweeps` | 创建异步扫频运行，返回 `202 + run_id` | 必须 |
| `GET /runs/{run_id}` | 运行快照、进度和错误 | 必须 |
| `POST /runs/{run_id}/cancel` | 有界取消 | 必须 |
| `GET /runs/{run_id}/points` | 返回已确认点 | 必须 |
| `POST /calibrations` | 创建 SOL 会话 | 第二里程碑 |
| `POST /calibrations/{id}/standards/{standard}/capture` | 采集 O/S/L | 第二里程碑 |
| `POST /calibrations/{id}/solve` | 求解并校验 | 第二里程碑 |
| `POST /exports/touchstone` | 导出 `.s1p` | 第二里程碑 |

`POST /runs/sweeps` 示例：

```json
{
  "source": "simulated",
  "start_hz": 5000000,
  "stop_hz": 100000000,
  "points": 201,
  "spacing": "linear",
  "stimulus_amplitude_q15": 8192,
  "settle_us": 1000,
  "integration_count": 65536,
  "point_timeout_ms": 2000
}
```

服务必须冻结运行快照，开始后不得被 UI 当前输入静默修改。响应里的 64 位原始整数以十进制字符串传给 TypeScript，避免超过 JavaScript `Number` 的 53 位精度；派生的幅度、相位和复数浮点值可以使用 JSON number。

## 12. WebSocket 事件 V1

连接：`ws://127.0.0.1:<port>/api/v1/events?access_token=<token>`。令牌只存在内存，服务日志必须脱敏。事件统一格式：

```json
{
  "schema_version": "pvna.events.v1",
  "event_id": 42,
  "event": "point.confirmed",
  "timestamp_utc": "2026-08-12T12:00:00.000Z",
  "run_id": "run_...",
  "data": {}
}
```

必须支持 `device.status_changed`、`run.started`、`point.accepted`、`point.confirmed`、`run.progress`、`run.completed`、`run.failed`、`run.cancelled` 和 `service.log`。WebSocket 只用于通知；断线重连后 React 必须通过 REST 获取权威快照，不能依赖补发所有历史事件。

## 13. 上位机数据与证据边界

每个确认点至少保存：`run_id`、`measurement_id`、`point_index`、请求/实际频率、采集参数、原始 `R/A` 四个有符号积分值、积分数、右移量、质量标志、FPGA 时间戳、主机接收时间、设备/构建身份和来源 `SIMULATED` 或 `HARDWARE`。

派生数据包括复数 `A/R`、回波损耗、相位、S11 和 SOL 校准结果。来源必须在 UI、文件和导出元数据中可见；模拟结果不得被描述成真实板卡测试。第一阶段可以先采用简单的运行目录或 SQLite + 数据文件，但必须保证原始点不可被派生计算覆盖，并支持中途失败后的只读回放。

## 14. 第一阶段验收矩阵

| 编号 | 台架 | 验收重点 |
|---|---|---|
| H1 | 纯协议单元测试 | 帧编解码、CRC、大小端、边界长度、坏帧重同步 |
| H2 | Python 模拟设备 | 正常点、延迟、削顶、超时、故障、重复帧、结果重读 |
| H3 | Electron + React + Python | 显式连接、冻结计划、实时曲线、取消、HOLD、回放 |
| H4 | 串口环回/虚拟串口 | 分包、粘包、断开、重连、CRC 错误和吞吐 |
| H5 | FPGA 协议仿真 | opcode、状态机、重复请求去重、事件唯一性 |
| H6 | 实板数字链路 | 只证明 UART 与 FPGA 状态，不等同于 JESD/RF 通过 |
| H7 | 实板 R/A 闭环 | 5–100 MHz 逐点复数重复性、幅相稳定、削顶标志 |
| H8 | SOL + 负载验证 | O/S/L 数据完整、校准后已知负载结果、Touchstone 导出 |

完成 H1–H5 只能称为软件/离线闭环；H6 以后才是逐级真实硬件证据。任何仿真、综合或串口环回都不能替代真实 LMK/JESD/DAC/ADC/RF 验收。

## 15. 后续兼容预留

- 外部单点触发：后续增加 `CONFIG_POINT`、`ARM_EXTERNAL`、`DISARM_EXTERNAL` 和硬件触发电气定义；仍保持“一次有效触发恰好一个可关联频点结果”。
- RTC：由 host 侧 RTC adapter 组合 VNA，不让 FPGA 或 VNA 底层依赖 RTC V0.7。
- 上下变频：设备必须新增本振/合成器锁定、滤波器/通道稳定和 `frequency_settled` 证据；主机不得依靠固定 sleep 猜测完成。
- Ethernet：可以复用 PVNA-Link 帧到 TCP；能力协商后再启用，不改变点结果语义。
- 协议演进：V0.x 的不兼容变更提升 minor；稳定后发布 V1.0。未知保留位必须拒绝或忽略的策略以新版本文档为准，V0.1 一律要求为 0。

## 附录 A：实现检查清单

- [ ] 设备上电 RF 关闭并进入 HOLD。
- [ ] 主机启动不自动连接、不自动退出 HOLD。
- [ ] 固定头、little-endian、CRC32 与测试向量一致。
- [ ] START_POINT 的 ACK 与最终结果分开。
- [ ] 相同副作用请求不会执行两次。
- [ ] 每个成功点只产生一个最终结果并可重读。
- [ ] 失败或保存失败不推进扫频进度。
- [ ] UI 明确区分 SIMULATED、HARDWARE、FAULT 和 UNKNOWN。
- [ ] 原始 R/A 始终可回放和重新计算。
- [ ] 结束、取消和故障路径都确认 RF 关闭，不能确认时显示 UNKNOWN。

## 附录 B：版本冻结说明

本 V0.1 是第一阶段上位机和 FPGA 协作的初始契约，不是已完成硬件验收的声明。首次真实串口联调前允许通过评审修正字段，但已经进入代码的任何更改必须同步更新：Markdown 源、Word 发布版、Python 编解码测试向量和 FPGA 协议测试平台。

## 附录 C：PING 帧测试向量

条件：序号为 1，无负载。请求头和完整请求帧分别为：

```text
REQ_HEADER = 50 56 00 01 01 01 01 00 00 00 14 00 01 00 00 00 00 00 00 00
REQ_CRC32  = F6A80DCF
REQ_FRAME  = 50 56 00 01 01 01 01 00 00 00 14 00 01 00 00 00 00 00 00 00 CF 0D A8 F6
```

成功响应头和完整响应帧分别为：

```text
RSP_HEADER = 50 56 00 01 02 01 00 00 00 00 14 00 01 00 00 00 00 00 00 00
RSP_CRC32  = 426F584B
RSP_FRAME  = 50 56 00 01 02 01 00 00 00 00 14 00 01 00 00 00 00 00 00 00 4B 58 6F 42
```

`REQ_CRC32` 和 `RSP_CRC32` 是通常书写的 32 位数值；帧末四字节按 little-endian 排列。实现必须同时通过完整帧逐字节比较和独立 CRC 计算。
