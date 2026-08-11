# 三器件独立 ROM SPI 写控制器

- 模块版本：2.0.0
- Vivado：2025.2
- 目标器件：`xcku5p-ffvb676-2-i`
- RTL：Verilog-2001；ROM：AMD/Xilinx XPM Memory
- 状态：数字自检与目标器件独立综合已通过；尚未进行真实硬件 SPI 验证

## 1. 设计结论

LMK04828、AD9250 和 AD9144 各自拥有一套独立 SPI 主机和一份独立配置 ROM。总控只负责按以下顺序启动三套发送器：

```text
start
  -> LMK04828 ROM[0 .. N-1]
  -> AD9250   ROM[0 .. N-1]
  -> AD9144   ROM[0 .. N-1]
  -> config_done
```

RTL 分为四层：

1. `spi_write_master.v`：只处理一帧 SPI Mode 0、MSB-first 串行发送。
2. `spi_config_rom.v`：基于 `xpm_memory_sprom` 的同步 ROM，每个地址保存一帧完整数据。
3. `spi_rom_device_sender.v`：一颗器件的 ROM 地址执行器，内部包含一个 ROM 和一个 SPI 主机。
4. `spi_three_device_write_controller.v`：三次例化发送器，顺序启动 LMK、ADC、DAC。

因此综合展开结果是三套 `spi_write_master`，而不是一套主机加三路复用器。三套物理 SPI 可以使用不同的发送时钟，但本总控不会并发启动它们。

## 2. ROM 数据格式

三份示例文件位于 `config/spi/`：

- `lmk04828_config_example.mem`
- `ad9250_config_example.mem`
- `ad9144_config_example.mem`

每行对应一个 ROM 地址，当前默认每行是一个 24-bit 十六进制完整帧：

```text
123456    # ROM[0]，发送顺序 bit23 -> bit0
A5C3E7    # ROM[1]
00FF01    # ROM[2]
```

正式 `.mem` 文件建议只保留十六进制值，一行一帧。地址、读写位、寄存器地址和有效数据如何排列，全部由 ROM 生成者负责；SPI 模块不解析任何字段。

当前三份 `_example.mem` 是数字仿真占位数据，不是芯片配置表。`CONFIG_DATA_VALID` 默认值为 0；直接启动只会返回错误码，不会发送这些示例帧。

替换正式 ROM 时必须同步修改以下参数：

| 参数 | 含义 |
|---|---|
| `*_DATA_WIDTH` | 每个 ROM 字和 SPI 帧的位宽，当前均为24 |
| `*_ROM_DEPTH` | ROM 实际深度 |
| `*_ROM_WORD_COUNT` | 本轮需要发送的有效字数 |
| `*_ROM_ADDRESS_WIDTH` | 至少为 `ceil(log2(ROM_DEPTH))`，最小取1 |
| `*_ROM_INIT_FILE` | 已加入 Vivado 工程的 `.mem` 文件名 |
| `CONFIG_DATA_VALID` | 真实配置表完成评审后才允许设为1 |

正式项目建议由 CSV/Excel/ADI 导出文件生成 `.mem`，同时保存源文件、生成脚本和摘要，避免手工维护大表；当前版本没有把未经确认的寄存器表复制进工程。

## 3. SPI 位宽与发送时钟

每颗器件分别由以下参数控制：

- `LMK_DATA_WIDTH`、`AD9250_DATA_WIDTH`、`AD9144_DATA_WIDTH`
- `LMK_CLK_DIV`、`AD9250_CLK_DIV`、`AD9144_CLK_DIV`

发送时钟公式：

```text
f_spi_sclk = f_clk_i / (2 * CLK_DIV)
```

100 MHz 管理时钟示例：

| `CLK_DIV` | SCLK |
|---:|---:|
| 5 | 10 MHz |
| 10 | 5 MHz |
| 20 | 2.5 MHz |

本地资料给出的最大 SCLK 是 LMK04828 20 MHz、AD9250 25 MHz、AD9144 10 MHz。
当前默认三颗器件均使用分频5，即100 MHz管理时钟下10 MHz；最终速率仍需结合
真实管理时钟、原理图和示波器波形确认。

## 4. 控制与状态接口

- `start_i`：只在 `ready_o=1` 时接受，建议保持一个 `clk_i` 周期。
- `busy_o`：从 LMK 开始到 AD9144 最后一帧结束保持为1。
- `done_o`：三颗器件整表发送完成后的单拍脉冲。
- `config_done_o`：完成粘性状态，在下一次启动或复位时清除。
- `error_o`：失败粘性状态，在下一次启动或复位时清除。
- `active_device_o`：0=LMK04828，1=AD9250，2=AD9144，3=空闲或结束。
- `current_word_index_o`：当前器件正在发送的 ROM 地址，便于 ILA 定位。

总控错误码：

| 值 | 含义 |
|---:|---|
| 0 | 无错误 |
| 1 | `CONFIG_DATA_VALID=0`，禁止发送占位 ROM |
| 2 | LMK04828 发送器错误 |
| 3 | AD9250 发送器错误 |
| 4 | AD9144 发送器错误 |
| 5 | 总控非法状态，已进入失败安全态 |

底层详细错误通过 `spi_error_code_o` 保留。

## 5. 当前边界

- 固定 SPI Mode 0、MSB first、只写。
- 三颗器件按整表顺序发送，不并发。
- 不实现芯片硬复位、AD9250复位后500 us等待、LMK锁定等待、SYNC/SYSREF、读回、轮询或寄存器校验。
- `config_done_o=1` 只表示三份数字位流已经发送完毕，不代表器件配置成功。
- 板级 IOBUF、管脚、电平和 XDC 由后续系统顶层负责。

真实上电流程仍应由更高层配置管理器组织复位、规定延时、状态读取和失败处理。

## 6. 可重复验证入口

```powershell
vivado.bat -mode batch -source scripts/run_spi_write_sim.tcl
vivado.bat -mode batch -source scripts/synth_spi_write_ooc.tcl
vivado.bat -mode batch -source scripts/add_spi_sources.tcl
```

最小例化见 `examples/spi_write_controller_example.v`，验证记录见 `SPI_WRITE_VERIFICATION.md`。
