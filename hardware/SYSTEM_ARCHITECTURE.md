# 直采便携矢网原型：整体结构规划

## 1. 第一阶段目标

- 一端口复数反射测量：S11。
- 采用直采结构，不配置上下变频。
- 一个 DAC 通道产生扫频激励。
- ADC 通道 1 作为参考通道 R。
- ADC 通道 2 作为反射测量通道 A。
- 先完成系统结构、接口和信号流定义，不进入 RTL、软件或 PCB 实现。

建议首版工作范围暂定为约 0.5 MHz 至 350 MHz。350 MHz 至 400 MHz
作为后续实测评估区间，不在结构规划阶段承诺指标。

## 2. 整体结构框图

```mermaid
flowchart LR
    subgraph CLOCK[统一时钟与同步]
        TCXO[板载或外部参考时钟]
        LMK[LMK04828\n器件时钟 / JESD 时钟 / SYSREF]
        TCXO --> LMK
    end

    subgraph DIGITAL[数字处理与控制]
        CTRL[扫频与状态控制]
        NCO[公共扫频 NCO]
        TXDSP[DAC 波形生成]
        RXDSP[R、A 同步 I/Q 解调与积分]
        CAL[复数比值 A/R\nSOL 校准\nS11 输出]
        UI[上位机显示与数据导出]

        CTRL --> NCO
        NCO --> TXDSP
        NCO -. 同一相位基准 .-> RXDSP
        RXDSP --> CAL --> UI
    end

    subgraph ADDA[FMCADDA-9250-9144]
        DAC[AD9144\n激励 DAC]
        ADCR[AD9250 通道 1\n参考 R]
        ADCA[AD9250 通道 2\n反射 A]
    end

    subgraph RF[直采射频前端]
        SPLIT[宽带功分器]
        REF[参考取样链路 R]
        COUPLER[反射分离器件\n定向耦合器或定向电桥]
        DUT[端口 1 / DUT\nSOL 校准参考面]
        RXA[反射接收链路 A\n增益 / 衰减 / 保护]
    end

    LMK --> DAC
    LMK --> ADCR
    LMK --> ADCA
    LMK --> NCO

    TXDSP --> DAC
    DAC --> SPLIT
    SPLIT --> REF --> ADCR
    SPLIT --> COUPLER --> DUT
    DUT -. 反射波 .-> COUPLER
    COUPLER --> RXA --> ADCA
    ADCR --> RXDSP
    ADCA --> RXDSP
```

## 3. 各部分职责

### 激励链路

公共 NCO 决定扫频点，AD9144 输出单音。射频前端把激励分成两路：一路送
参考通道 R，另一路通过反射分离器件送到 DUT。

### 参考通道 R

参考点放在功分器之后，使 R 能观察实际送入射频前端的源幅度和相位。它用于
逐频点归一化，避免把 DAC 幅频响应和公共相位漂移误认为 DUT 特性。

### 反射通道 A

定向耦合器或定向电桥分离 DUT 返回的反射波。A 链路预留增益、衰减和输入
保护，但本阶段不确定具体器件和增益数值。

### 数字处理

R、A 两路必须同步采集，并使用与激励同源的相位基准分别得到复数 I/Q。
数字处理先形成原始复数比值 `m = A/R`，再通过 Open、Short、Load 三项校准
得到 S11。

### 控制与显示

上位机负责扫频参数、校准流程、曲线显示、标记读取以及后续 Touchstone 数据
导出。是否采用板载软核或外部 PC，留到接口规划阶段决定。

## 4. 当前保留的设计选择

1. 反射分离采用定向耦合器，还是宽带定向电桥。
2. 是否在 DAC 输出侧增加可调衰减和源放大。
3. A 通道需要多少接收增益，以及是否分档。
4. 控制接口采用千兆以太网、USB，还是先用调试接口。
5. 首版只做 S11，还是为下一阶段 S21 预留端口和开关位置。

## 5. 下一步规划顺序

1. 冻结首版频段和目标动态范围。
2. 比较定向耦合器与定向电桥方案。
3. 做 R、A 两条通道的功率预算和 ADC 裕量预算。
4. 再确定射频前端器件与原理图。
5. 硬件结构冻结后才进入 FPGA 和软件实现。
