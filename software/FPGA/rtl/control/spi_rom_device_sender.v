`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : spi_rom_device_sender.v
// Project     : Portable Direct-Sampling VNA
// Module      : spi_rom_device_sender
// Version     : 1.0.0
// Description : 发送一颗器件的完整 ROM 配置表。模块内部包含一套独立
//               spi_write_master 和一套独立 spi_config_rom。
//
// Clock domain:
//   - clk_i      : 配置管理时钟域。
//
// Transaction contract:
//   - start_i 只在 ready_o=1 时接受，建议保持一个 clk_i 周期。
//   - ROM 地址从 0 递增到 ROM_WORD_COUNT-1，每个地址发送一次。
//   - 每个 ROM 字均作为完整 SPI 帧按 MSB first 发送，不解析地址或数据字段。
//   - done_o/error_o 为单拍脉冲；busy_o 覆盖整张 ROM 表的发送过程。
//
// Key assumptions:
//   - SPI 固定为 Mode 0；DATA_WIDTH 和 CLK_DIV 在例化时确定。
//   - ROM_WORD_COUNT 必须在 1..ROM_DEPTH 范围内。
//
// Verification:
//   - sim/spi_write/tb_spi_three_device_write_controller.sv
//   - 当前仅完成数字仿真和独立综合，未验证真实芯片响应。
// ============================================================================

module spi_rom_device_sender #(
    parameter integer DATA_WIDTH       = 24,
    parameter integer ROM_DEPTH        = 4,
    parameter integer ROM_WORD_COUNT   = 4,
    parameter integer ROM_ADDRESS_WIDTH = 2,
    parameter         ROM_INIT_FILE    = "none",
    parameter integer CLK_DIV          = 5,
    parameter integer BIT_COUNT_WIDTH  = 7,
    parameter integer CLK_DIV_WIDTH    = 16
)(
    input  wire                         clk_i,
    input  wire                         reset_ni,

    input  wire                         start_i,
    output wire                         ready_o,
    output wire                         busy_o,
    output reg                          done_o,
    output reg                          error_o,
    output reg  [2:0]                   error_code_o,
    output wire [15:0]                  current_word_index_o,

    output wire                         spi_cs_no,
    output wire                         spi_sclk_o,
    output wire                         spi_sdio_o,
    output wire                         spi_sdio_oe_o
);

    localparam [2:0] STATE_IDLE         = 3'd0;
    localparam [2:0] STATE_ROM_WAIT     = 3'd1;
    localparam [2:0] STATE_MASTER_START = 3'd2;
    localparam [2:0] STATE_MASTER_WAIT  = 3'd3;

    localparam [2:0] ERROR_NONE          = 3'd0;
    localparam [2:0] ERROR_BUSY_START    = 3'd3;
    localparam [2:0] ERROR_ILLEGAL_STATE = 3'd5;

    localparam [ROM_ADDRESS_WIDTH-1:0] LAST_ROM_ADDRESS = ROM_WORD_COUNT - 1;
    localparam [BIT_COUNT_WIDTH-1:0]   FRAME_BIT_COUNT  = DATA_WIDTH;
    localparam [CLK_DIV_WIDTH-1:0]     FRAME_CLK_DIV    = CLK_DIV;

    reg  [2:0]                     state_reg;
    reg  [ROM_ADDRESS_WIDTH-1:0]   rom_address_reg;

    wire [DATA_WIDTH-1:0]          rom_data;
    wire                           master_start;
    wire                           master_busy;
    wire                           master_done;
    wire                           master_error;
    wire [2:0]                     master_error_code;

    assign ready_o              = (state_reg == STATE_IDLE);
    assign busy_o               = (state_reg != STATE_IDLE);
    assign master_start         = (state_reg == STATE_MASTER_START);
    assign current_word_index_o = {{(16-ROM_ADDRESS_WIDTH){1'b0}}, rom_address_reg};

    // ROM 持续使能。地址改变后专门等待一个周期，再进入 MASTER_START，
    // 从而严格满足 XPM ROM 的一拍同步读取延迟。
    spi_config_rom #(
        .DATA_WIDTH    (DATA_WIDTH),
        .DEPTH         (ROM_DEPTH),
        .ADDRESS_WIDTH (ROM_ADDRESS_WIDTH),
        .INIT_FILE     (ROM_INIT_FILE)
    ) u_spi_config_rom (
        .clk_i         (clk_i),
        .enable_i      (1'b1),
        .address_i     (rom_address_reg),
        .data_o        (rom_data)
    );

    // 每个 spi_rom_device_sender 都拥有一套物理独立的 SPI 主机。
    // 上层三次例化本模块后，综合展开结果即为三套 spi_write_master。
    spi_write_master #(
        .MAX_DATA_WIDTH  (DATA_WIDTH),
        .BIT_COUNT_WIDTH (BIT_COUNT_WIDTH),
        .CLK_DIV_WIDTH   (CLK_DIV_WIDTH)
    ) u_spi_write_master (
        .clk_i           (clk_i),
        .reset_ni        (reset_ni),
        .start_i         (master_start),
        .tx_data_i       (rom_data),
        .tx_bit_count_i  (FRAME_BIT_COUNT),
        .clk_div_i       (FRAME_CLK_DIV),
        .ready_o         (),
        .busy_o          (master_busy),
        .done_o          (master_done),
        .error_o         (master_error),
        .error_code_o    (master_error_code),
        .spi_cs_no       (spi_cs_no),
        .spi_sclk_o      (spi_sclk_o),
        .spi_sdio_o      (spi_sdio_o),
        .spi_sdio_oe_o   (spi_sdio_oe_o)
    );

    // 参数检查仅用于仿真，防止 ROM 长度和地址宽度配置不一致。
    // synthesis translate_off
    initial begin : g_parameter_check
        if ((ROM_WORD_COUNT < 1) || (ROM_WORD_COUNT > ROM_DEPTH)) begin
            $display("ERROR(spi_rom_device_sender): ROM_WORD_COUNT must be within ROM_DEPTH");
            $finish;
        end

        if ((ROM_ADDRESS_WIDTH < 1) || (ROM_ADDRESS_WIDTH > 16)) begin
            $display("ERROR(spi_rom_device_sender): ROM_ADDRESS_WIDTH must be 1..16");
            $finish;
        end

        if ((1 << ROM_ADDRESS_WIDTH) < ROM_DEPTH) begin
            $display("ERROR(spi_rom_device_sender): ROM_ADDRESS_WIDTH cannot cover ROM_DEPTH");
            $finish;
        end

        if ((DATA_WIDTH < 1) || (DATA_WIDTH > ((1 << BIT_COUNT_WIDTH) - 1))) begin
            $display("ERROR(spi_rom_device_sender): DATA_WIDTH cannot be represented");
            $finish;
        end

        if (CLK_DIV < 1) begin
            $display("ERROR(spi_rom_device_sender): CLK_DIV must be at least 1");
            $finish;
        end
    end
    // synthesis translate_on

    always @(posedge clk_i or negedge reset_ni) begin
        if (!reset_ni) begin
            state_reg       <= STATE_IDLE;
            rom_address_reg <= {ROM_ADDRESS_WIDTH{1'b0}};
            done_o          <= 1'b0;
            error_o         <= 1'b0;
            error_code_o    <= ERROR_NONE;
        end else begin
            done_o       <= 1'b0;
            error_o      <= 1'b0;
            error_code_o <= ERROR_NONE;

            // 上层正常不会在 busy 期间重复启动；若接口被误用，仅报告单拍错误，
            // 当前 ROM 配置过程继续执行，不破坏正在发送的 SPI 帧。
            if (start_i && !ready_o) begin
                error_o      <= 1'b1;
                error_code_o <= ERROR_BUSY_START;
            end

            case (state_reg)
                STATE_IDLE: begin
                    if (start_i) begin
                        rom_address_reg <= {ROM_ADDRESS_WIDTH{1'b0}};
                        state_reg       <= STATE_ROM_WAIT;
                    end
                end

                STATE_ROM_WAIT: begin
                    state_reg <= STATE_MASTER_START;
                end

                STATE_MASTER_START: begin
                    // master_start 在本状态组合拉高一拍；ROM 数据已经稳定。
                    state_reg <= STATE_MASTER_WAIT;
                end

                STATE_MASTER_WAIT: begin
                    if (master_error) begin
                        error_o      <= 1'b1;
                        error_code_o <= master_error_code;
                        state_reg    <= STATE_IDLE;
                    end else if (master_done) begin
                        if (rom_address_reg == LAST_ROM_ADDRESS) begin
                            done_o    <= 1'b1;
                            state_reg <= STATE_IDLE;
                        end else begin
                            rom_address_reg <= rom_address_reg + 1'b1;
                            state_reg       <= STATE_ROM_WAIT;
                        end
                    end
                end

                default: begin
                    error_o      <= 1'b1;
                    error_code_o <= ERROR_ILLEGAL_STATE;
                    state_reg    <= STATE_IDLE;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
