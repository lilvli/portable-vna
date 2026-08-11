`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : spi_write_controller_example.v
// Description : 三器件 ROM 顺序配置控制器的最小例化示例。
// Boundary    : 示例 ROM 是仿真占位数据，因此 CONFIG_DATA_VALID 保持为 0。
//               替换并评审三份 ROM 后，系统顶层才可以把该参数改为 1。
// ============================================================================

module spi_write_controller_example (
    input  wire clk_mgmt_100m_i,
    input  wire rst_mgmt_n_i,
    input  wire config_start_i,

    output wire config_busy_o,
    output wire config_done_o,
    output wire config_error_o,

    output wire lmk_spi_cs_no,
    output wire lmk_spi_sclk_o,
    output wire lmk_spi_sdio_o,
    output wire lmk_spi_sdio_oe_o,

    output wire ad9250_spi_cs_no,
    output wire ad9250_spi_sclk_o,
    output wire ad9250_spi_sdio_o,
    output wire ad9250_spi_sdio_oe_o,

    output wire ad9144_spi_cs_no,
    output wire ad9144_spi_sclk_o,
    output wire ad9144_spi_sdio_o,
    output wire ad9144_spi_sdio_oe_o
);

    wire config_done_pulse;

    spi_three_device_write_controller #(
        .CONFIG_DATA_VALID (0),
        .LMK_CLK_DIV       (5),
        .AD9250_CLK_DIV    (5),
        .AD9144_CLK_DIV    (5)
    ) u_spi_three_device_write_controller (
        .clk_i                    (clk_mgmt_100m_i),
        .reset_ni                 (rst_mgmt_n_i),
        .start_i                  (config_start_i),
        .ready_o                  (),
        .busy_o                   (config_busy_o),
        .done_o                   (config_done_pulse),
        .config_done_o            (config_done_o),
        .error_o                  (config_error_o),
        .error_code_o             (),
        .spi_error_code_o         (),
        .active_device_o          (),
        .current_word_index_o     (),
        .lmk_spi_cs_no            (lmk_spi_cs_no),
        .lmk_spi_sclk_o           (lmk_spi_sclk_o),
        .lmk_spi_sdio_o           (lmk_spi_sdio_o),
        .lmk_spi_sdio_oe_o        (lmk_spi_sdio_oe_o),
        .ad9250_spi_cs_no         (ad9250_spi_cs_no),
        .ad9250_spi_sclk_o        (ad9250_spi_sclk_o),
        .ad9250_spi_sdio_o        (ad9250_spi_sdio_o),
        .ad9250_spi_sdio_oe_o     (ad9250_spi_sdio_oe_o),
        .ad9144_spi_cs_no         (ad9144_spi_cs_no),
        .ad9144_spi_sclk_o        (ad9144_spi_sclk_o),
        .ad9144_spi_sdio_o        (ad9144_spi_sdio_o),
        .ad9144_spi_sdio_oe_o     (ad9144_spi_sdio_oe_o)
    );

endmodule

`default_nettype wire
