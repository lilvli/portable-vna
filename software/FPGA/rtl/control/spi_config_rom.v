`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : spi_config_rom.v
// Project     : Portable Direct-Sampling VNA
// Module      : spi_config_rom
// Version     : 1.0.0
// Description : 基于 AMD/Xilinx XPM 的同步只读配置存储器。ROM 中每个地址保存
//               一帧已经完整打包的 SPI 写数据，本模块不解释帧内字段。
//
// Clock domain:
//   - clk_i      : 配置管理时钟域。
//
// Read contract:
//   - enable_i=1 时，在 clk_i 上升沿采样 address_i。
//   - data_o 在一个 clk_i 周期后更新，对应 READ_LATENCY_A=1。
//
// Key assumptions:
//   - INIT_FILE 必须加入 Vivado sources_1，且每行是一帧十六进制数据。
//   - ADDRESS_WIDTH 必须能够覆盖 DEPTH；参数合法性在仿真时检查。
//
// Verification:
//   - sim/spi_write/tb_spi_three_device_write_controller.sv
//   - 当前仅完成数字仿真和独立综合，未验证真实器件寄存器表。
// ============================================================================

module spi_config_rom #(
    parameter integer DATA_WIDTH    = 24,
    parameter integer DEPTH         = 4,
    parameter integer ADDRESS_WIDTH = 2,
    parameter         INIT_FILE     = "none"
)(
    input  wire                         clk_i,
    input  wire                         enable_i,
    input  wire [ADDRESS_WIDTH-1:0]     address_i,
    output wire [DATA_WIDTH-1:0]        data_o
);

    // 参数检查仅服务仿真和代码评审，不参与综合后的硬件逻辑。
    // synthesis translate_off
    initial begin : g_parameter_check
        if (DATA_WIDTH < 1) begin
            $display("ERROR(spi_config_rom): DATA_WIDTH must be at least 1");
            $finish;
        end

        if (DEPTH < 1) begin
            $display("ERROR(spi_config_rom): DEPTH must be at least 1");
            $finish;
        end

        if ((1 << ADDRESS_WIDTH) < DEPTH) begin
            $display("ERROR(spi_config_rom): ADDRESS_WIDTH cannot cover DEPTH");
            $finish;
        end

        if (INIT_FILE == "none") begin
            $display("ERROR(spi_config_rom): INIT_FILE must name a reviewed memory file");
            $finish;
        end
    end
    // synthesis translate_on

    // 使用 auto 允许当前小表映射为 LUT，并允许未来较大的正式配置表自动转入
    // BRAM；不在可复用公共模块中硬编码某一种存储资源。
    xpm_memory_sprom #(
        .ADDR_WIDTH_A        (ADDRESS_WIDTH),
        .AUTO_SLEEP_TIME     (0),
        .CASCADE_HEIGHT      (0),
        .ECC_MODE            ("no_ecc"),
        .MEMORY_INIT_FILE    (INIT_FILE),
        .MEMORY_INIT_PARAM   (""),
        .MEMORY_OPTIMIZATION ("true"),
        .MEMORY_PRIMITIVE    ("auto"),
        .MEMORY_SIZE         (DATA_WIDTH * DEPTH),
        .MESSAGE_CONTROL     (0),
        .READ_DATA_WIDTH_A   (DATA_WIDTH),
        .READ_LATENCY_A      (1),
        .READ_RESET_VALUE_A  ("0"),
        .RST_MODE_A          ("SYNC"),
        .SIM_ASSERT_CHK      (1),
        .USE_MEM_INIT        (1),
        .WAKEUP_TIME         ("disable_sleep")
    ) u_xpm_memory_sprom (
        .dbiterra            (),
        .douta               (data_o),
        .sbiterra            (),
        .addra               (address_i),
        .clka                (clk_i),
        .ena                 (enable_i),
        .injectdbiterra      (1'b0),
        .injectsbiterra      (1'b0),
        .regcea              (1'b1),
        .rsta                (1'b0),
        .sleep               (1'b0)
    );

endmodule

`default_nettype wire
