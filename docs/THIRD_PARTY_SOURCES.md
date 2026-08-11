# 第三方资料与工具获取说明

## 1. 分发原则

本仓库只提交项目原创代码、原创文档、必要的小型配置示例和可重复构建脚本。以下内容不直接放入 Git 仓库：

- 厂商提供的 `.exe`、`.msi` 安装程序；
- FMCADDA Demo 的 `.rar/.zip` 压缩包；
- 芯片数据手册、板卡原理图和封装库；
- Xilinx/AMD IP 输出文件、DCP、bitstream、仿真库和 Vivado 生成目录；
- 未确认允许再分发的第三方 Demo 源码。

Git LFS 只能解决大文件存储问题，不能自动获得第三方资料的再分发权。因此本项目优先提供型号和官方下载入口，不建立第三方文件镜像。

## 2. 开发工具

| 本地参考文件/用途 | 官方获取入口 | 说明 |
|---|---|---|
| Vivado 2025.2 | [AMD 2025.2 下载页](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2025-2.html) | 当前 FPGA 工具基线；许可范围由 AMD 条款决定 |
| `TICSPro_*.exe` | [TI TICS Pro](https://www.ti.com/tool/TICSPRO-SW) | 用于 LMK04828 时钟树与寄存器配置；仓库不保存安装程序 |
| `WV5Setup*.exe` | [ADI 评估硬件与软件](https://www.analog.com/en/resources/evaluation-hardware-and-software.html) | 本地文件属于旧版 WaveVision 5 工具；ADI 当前页面可能不再提供同版本直接下载 |

## 3. 关键器件资料

| 器件 | 官方产品页 |
|---|---|
| AD9250 双通道 ADC | [Analog Devices AD9250](https://www.analog.com/en/products/ad9250.html) |
| AD9144 DAC | [Analog Devices AD9144](https://www.analog.com/en/products/ad9144.html) |
| LMK04828 时钟芯片 | [Texas Instruments LMK04828](https://www.ti.com/product/LMK04828) |
| ADA4937 差分 ADC 驱动器 | [Analog Devices ADA4937-1](https://www.analog.com/en/products/ada4937-1.html) |

## 4. 板卡与 Demo 包

项目本地资料库中保存有 XCKU5P 载板、FMCADDA-9250-9144 子卡资料和多个厂商 Vivado Demo 压缩包。这些文件不进入公开仓库。

使用者应从所购板卡的原厂或授权销售渠道，按以下型号索取最新版资料：

- XCKU5P FPGA 载板原理图、FMC 管脚表和参考工程；
- FMCADDA-9250-9144 子卡用户手册、原理图和 Demo 工程；
- 对应板卡版本的 LMK04828、AD9250、AD9144 寄存器配置文件。

后续获得稳定的板卡原厂下载页后，可在此补充链接，但仍不把安装包和压缩包复制进仓库。

## 5. 版本和安全提示

- 下载第三方软件时核对厂商页面、版本、数字签名或 SHA-256。
- 不运行来源不明的安装程序或 Demo 脚本。
- 不把厂商 Demo 的 XDC、GT 参数、IP 输出或寄存器表未经核对直接当作本项目硬件事实。
- 项目中的示例配置值必须标明来源、器件后缀、板卡版本和验证状态。
