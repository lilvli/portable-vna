# 便携式直采矢量网络分析仪 FPGA 工程

## 当前入口

- 编码规范：`FPGA_CODING_STANDARD.md`
- 实施路线：`SOFTWARE_IMPLEMENTATION_ROADMAP.md`
- SPI 模块说明：`rtl/control/SPI_WRITE_README.md`
- SPI 验证记录：`rtl/control/SPI_WRITE_VERIFICATION.md`
- 最小工程重建：`scripts/create_project.tcl`

## 重建最小工程

从项目根目录运行：

```powershell
vivado -mode batch -source software/FPGA/scripts/create_project.tcl
```

生成目录为 `software/FPGA/build/vivado/`。生成工程、日志和其他 Vivado
输出不进入版本控制。

## 当前实现状态

- Vivado 工程目标：`xcku5p-ffvb676-2-i`，Vivado 2025.2。
- 已新增 LMK04828、AD9250、AD9144 三套独立只写 SPI 主机、三套 XPM
  配置 ROM、顺序配置总控及自检仿真。
- 当前 `_example.mem` 是仿真占位数据，`CONFIG_DATA_VALID` 默认禁止误发送。
- `rtl/top.v` 仍保持空顶层；SPI 模块没有连接真实 FPGA 管脚。
- 未执行硬件下载、真实 SPI、时钟、JESD 或模拟验证。
