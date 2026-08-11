# 三器件 ROM SPI 写控制器验证记录

- 模块版本：2.0.0
- 日期：2026-08-11
- 工具：Vivado 2025.2 / XSim
- 目标器件：`xcku5p-ffvb676-2-i`

## 已执行验证

| 验证项 | 入口 | 结果 |
|---|---|---|
| SystemVerilog 自检仿真 | `scripts/run_spi_write_sim.tcl` | PASS |
| 100 MHz OOC 综合与时序 | `scripts/synth_spi_write_ooc.tcl` | PASS |
| 加入现有 Vivado 工程 | `scripts/add_spi_sources.tcl` | PASS |
| 真实器件 SPI 波形与寄存器响应 | 后续硬件任务 | 未执行 |

## 结果摘要

- XSim 标记：`THREE-DEVICE ROM SPI SELF-TEST: PASS`。
- 仿真结束时间：18156 ns。
- 三份 XPM ROM 初始化文件均被 XSim 和综合成功读取。
- 综合展开确认三套 `spi_write_master` 和三套 `xpm_memory_sprom`。
- 三份当前测试 ROM 均为4×24 bit；XPM 采用 `auto` 资源选择，当前小表未使用 BRAM。
- 综合：0 error、0 critical warning、0 warning。
- 资源：188 CLB LUT、229 FF、0 DSP、0 BRAM。
- 100 MHz OOC 时序：WNS=+8.651 ns、WHS=+0.073 ns、TNS/THS=0。
- 现有 XPR 的综合顶层仍为 `top`，仿真顶层为 `tb_spi_three_device_write_controller`。
- `rtl/top.v` 的大小和时间戳保持不变，未接入板级端口。

证据位置：

- `output/spi_rom_write_sim/spi_rom_write_sim.sim/sim_1/behav/xsim/simulate.log`
- `output/spi_rom_write_ooc/utilization.rpt`
- `output/spi_rom_write_ooc/timing_summary.rpt`
- `output/spi_rom_write_ooc/cdc.rpt`
- `output/spi_rom_write_ooc/spi_three_device_write_controller.dcp`
- `prj/prj.xpr`

## 自检覆盖

- LMK04828、AD9250、AD9144 各自独立的 ROM、SPI 主机和物理输出；
- 每颗器件四个24-bit完整帧，总计12帧；
- ROM 地址从0递增到3，发送数据与每个 ROM 地址逐项一致；
- 严格的 LMK04828→AD9250→AD9144 整表顺序；
- 三套不同 SCLK 分频值2、3、4及其实际周期；
- Mode 0、MSB-first、SDIO 输出使能和每帧位数；
- 任意时刻最多一个 CS 有效；
- `CONFIG_DATA_VALID=0` 时拒绝占位 ROM，返回错误码1且不启动 SPI；
- 复位安全态、总完成粘性状态和 `done_o` 单拍语义。

## 已分类工具提示

- XPM SPROM 的公共底层含未使用的写端口，触发 `Synth 8-7129`；OOC 顶层16-bit调试索引高位固定为0，触发 `Synth 8-3917`。两类消息均已审查，只在独立综合脚本进程内降为 INFO。
- OOC timing 提示 `HD.CLK_SRC` 未设置。模块尚未接入最终时钟缓冲和位置约束，因此不虚构 BUFG 位置；系统顶层集成时由正式 XDC 解决。
- 当前工作站的用户 Tcl Store 损坏和 `commands.paini` 迁移失败属于工具环境提示。脚本只在当前 Vivado 进程内回退到安装目录，不修改用户或系统配置。
- 打开原空工程时出现与其他 Board Store 条目有关的 warning；项目 part 仍为 `xcku5p-ffvb676-2-i`，且 BoardPart 为空，这些提示不来自 SPI RTL。

## 验证边界

以上结果只证明数字 RTL 行为、ROM 初始化和目标器件可综合性。它们不证明真实 SPI 电平、时序裕量、寄存器写入、器件读回、LMK锁定、复位恢复、JESD建链或模拟链路有效。
