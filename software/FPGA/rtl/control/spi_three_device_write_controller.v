`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : spi_three_device_write_controller.v
// Project     : Portable Direct-Sampling VNA
// Module      : spi_three_device_write_controller
// Version     : 2.0.0
// Description : 三器件 ROM 配置总控。模块例化三套独立 spi_rom_device_sender，
//               因而包含三套独立 ROM 和三套独立 spi_write_master，并严格按
//               LMK04828 -> AD9250 -> AD9144 的顺序发送完整配置表。
//
// Clock domain:
//   - clk_i      : 唯一配置管理时钟域。
//
// Reset:
//   - reset_ni   : 低有效异步置位；释放前应在 clk_i 域外部同步。
//
// Start/status contract:
//   - start_i 只在 ready_o=1 时接受，建议保持一个 clk_i 周期。
//   - busy_o 覆盖三张 ROM 表；done_o 是全序列完成后的单拍脉冲。
//   - config_done_o/error_o 为粘性状态，新一轮合法 start 或复位时清除。
//   - active_device_o: 0=LMK04828，1=AD9250，2=AD9144，3=空闲/结束。
//
// Safety boundary:
//   - CONFIG_DATA_VALID 默认关闭。只有 ROM 已替换为评审过的真实配置表后，
//     才允许置 1；示例 ROM 绝不能直接用于真实硬件配置。
//   - 本模块不负责器件复位、芯片间延时、PLL lock、读回或寄存器校验。
//
// Verification:
//   - sim/spi_write/tb_spi_three_device_write_controller.sv
//   - 当前仅完成数字仿真和独立综合，未进行真实硬件 SPI 验证。
// ============================================================================

module spi_three_device_write_controller #(
    parameter integer CONFIG_DATA_VALID      = 0,

    parameter integer LMK_DATA_WIDTH         = 24,
    parameter integer LMK_ROM_DEPTH          = 4,
    parameter integer LMK_ROM_WORD_COUNT     = 4,
    parameter integer LMK_ROM_ADDRESS_WIDTH  = 2,
    parameter         LMK_ROM_INIT_FILE      = "lmk04828_config_example.mem",
    parameter integer LMK_CLK_DIV            = 5,

    parameter integer AD9250_DATA_WIDTH      = 24,
    parameter integer AD9250_ROM_DEPTH       = 4,
    parameter integer AD9250_ROM_WORD_COUNT  = 4,
    parameter integer AD9250_ROM_ADDRESS_WIDTH = 2,
    parameter         AD9250_ROM_INIT_FILE   = "ad9250_config_example.mem",
    parameter integer AD9250_CLK_DIV         = 5,

    parameter integer AD9144_DATA_WIDTH      = 24,
    parameter integer AD9144_ROM_DEPTH       = 4,
    parameter integer AD9144_ROM_WORD_COUNT  = 4,
    parameter integer AD9144_ROM_ADDRESS_WIDTH = 2,
    parameter         AD9144_ROM_INIT_FILE   = "ad9144_config_example.mem",
    parameter integer AD9144_CLK_DIV         = 5,

    parameter integer BIT_COUNT_WIDTH        = 7,
    parameter integer CLK_DIV_WIDTH          = 16
)(
    input  wire                         clk_i,
    input  wire                         reset_ni,

    input  wire                         start_i,
    output wire                         ready_o,
    output wire                         busy_o,
    output reg                          done_o,
    output reg                          config_done_o,
    output reg                          error_o,
    output reg  [3:0]                   error_code_o,
    output reg  [2:0]                   spi_error_code_o,
    output wire [1:0]                   active_device_o,
    output wire [15:0]                  current_word_index_o,

    output wire                         lmk_spi_cs_no,
    output wire                         lmk_spi_sclk_o,
    output wire                         lmk_spi_sdio_o,
    output wire                         lmk_spi_sdio_oe_o,

    output wire                         ad9250_spi_cs_no,
    output wire                         ad9250_spi_sclk_o,
    output wire                         ad9250_spi_sdio_o,
    output wire                         ad9250_spi_sdio_oe_o,

    output wire                         ad9144_spi_cs_no,
    output wire                         ad9144_spi_sclk_o,
    output wire                         ad9144_spi_sdio_o,
    output wire                         ad9144_spi_sdio_oe_o
);

    localparam [3:0] STATE_IDLE          = 4'd0;
    localparam [3:0] STATE_LMK_START     = 4'd1;
    localparam [3:0] STATE_LMK_WAIT      = 4'd2;
    localparam [3:0] STATE_AD9250_START  = 4'd3;
    localparam [3:0] STATE_AD9250_WAIT   = 4'd4;
    localparam [3:0] STATE_AD9144_START  = 4'd5;
    localparam [3:0] STATE_AD9144_WAIT   = 4'd6;
    localparam [3:0] STATE_COMPLETE      = 4'd7;
    localparam [3:0] STATE_FAILED        = 4'd8;

    localparam [3:0] ERROR_NONE                    = 4'd0;
    localparam [3:0] ERROR_CONFIG_DATA_NOT_VALID   = 4'd1;
    localparam [3:0] ERROR_LMK_SPI                  = 4'd2;
    localparam [3:0] ERROR_AD9250_SPI               = 4'd3;
    localparam [3:0] ERROR_AD9144_SPI               = 4'd4;
    localparam [3:0] ERROR_ILLEGAL_STATE            = 4'd5;

    reg  [3:0] state_reg;

    wire       lmk_start;
    wire       lmk_done;
    wire       lmk_error;
    wire [2:0] lmk_error_code;
    wire [15:0] lmk_word_index;

    wire       ad9250_start;
    wire       ad9250_done;
    wire       ad9250_error;
    wire [2:0] ad9250_error_code;
    wire [15:0] ad9250_word_index;

    wire       ad9144_start;
    wire       ad9144_done;
    wire       ad9144_error;
    wire [2:0] ad9144_error_code;
    wire [15:0] ad9144_word_index;

    assign lmk_start    = (state_reg == STATE_LMK_START);
    assign ad9250_start = (state_reg == STATE_AD9250_START);
    assign ad9144_start = (state_reg == STATE_AD9144_START);

    assign busy_o =
        (state_reg == STATE_LMK_START)    ||
        (state_reg == STATE_LMK_WAIT)     ||
        (state_reg == STATE_AD9250_START) ||
        (state_reg == STATE_AD9250_WAIT)  ||
        (state_reg == STATE_AD9144_START) ||
        (state_reg == STATE_AD9144_WAIT);

    assign ready_o = !busy_o;

    assign active_device_o =
        ((state_reg == STATE_LMK_START) || (state_reg == STATE_LMK_WAIT)) ? 2'd0 :
        ((state_reg == STATE_AD9250_START) || (state_reg == STATE_AD9250_WAIT)) ? 2'd1 :
        ((state_reg == STATE_AD9144_START) || (state_reg == STATE_AD9144_WAIT)) ? 2'd2 :
        2'd3;

    assign current_word_index_o =
        (active_device_o == 2'd0) ? lmk_word_index :
        (active_device_o == 2'd1) ? ad9250_word_index :
        (active_device_o == 2'd2) ? ad9144_word_index :
        16'd0;

    spi_rom_device_sender #(
        .DATA_WIDTH        (LMK_DATA_WIDTH),
        .ROM_DEPTH         (LMK_ROM_DEPTH),
        .ROM_WORD_COUNT    (LMK_ROM_WORD_COUNT),
        .ROM_ADDRESS_WIDTH (LMK_ROM_ADDRESS_WIDTH),
        .ROM_INIT_FILE     (LMK_ROM_INIT_FILE),
        .CLK_DIV           (LMK_CLK_DIV),
        .BIT_COUNT_WIDTH   (BIT_COUNT_WIDTH),
        .CLK_DIV_WIDTH     (CLK_DIV_WIDTH)
    ) u_lmk04828_sender (
        .clk_i                (clk_i),
        .reset_ni             (reset_ni),
        .start_i              (lmk_start),
        .ready_o              (),
        .busy_o               (),
        .done_o               (lmk_done),
        .error_o              (lmk_error),
        .error_code_o         (lmk_error_code),
        .current_word_index_o (lmk_word_index),
        .spi_cs_no            (lmk_spi_cs_no),
        .spi_sclk_o           (lmk_spi_sclk_o),
        .spi_sdio_o           (lmk_spi_sdio_o),
        .spi_sdio_oe_o        (lmk_spi_sdio_oe_o)
    );

    spi_rom_device_sender #(
        .DATA_WIDTH        (AD9250_DATA_WIDTH),
        .ROM_DEPTH         (AD9250_ROM_DEPTH),
        .ROM_WORD_COUNT    (AD9250_ROM_WORD_COUNT),
        .ROM_ADDRESS_WIDTH (AD9250_ROM_ADDRESS_WIDTH),
        .ROM_INIT_FILE     (AD9250_ROM_INIT_FILE),
        .CLK_DIV           (AD9250_CLK_DIV),
        .BIT_COUNT_WIDTH   (BIT_COUNT_WIDTH),
        .CLK_DIV_WIDTH     (CLK_DIV_WIDTH)
    ) u_ad9250_sender (
        .clk_i                (clk_i),
        .reset_ni             (reset_ni),
        .start_i              (ad9250_start),
        .ready_o              (),
        .busy_o               (),
        .done_o               (ad9250_done),
        .error_o              (ad9250_error),
        .error_code_o         (ad9250_error_code),
        .current_word_index_o (ad9250_word_index),
        .spi_cs_no            (ad9250_spi_cs_no),
        .spi_sclk_o           (ad9250_spi_sclk_o),
        .spi_sdio_o           (ad9250_spi_sdio_o),
        .spi_sdio_oe_o        (ad9250_spi_sdio_oe_o)
    );

    spi_rom_device_sender #(
        .DATA_WIDTH        (AD9144_DATA_WIDTH),
        .ROM_DEPTH         (AD9144_ROM_DEPTH),
        .ROM_WORD_COUNT    (AD9144_ROM_WORD_COUNT),
        .ROM_ADDRESS_WIDTH (AD9144_ROM_ADDRESS_WIDTH),
        .ROM_INIT_FILE     (AD9144_ROM_INIT_FILE),
        .CLK_DIV           (AD9144_CLK_DIV),
        .BIT_COUNT_WIDTH   (BIT_COUNT_WIDTH),
        .CLK_DIV_WIDTH     (CLK_DIV_WIDTH)
    ) u_ad9144_sender (
        .clk_i                (clk_i),
        .reset_ni             (reset_ni),
        .start_i              (ad9144_start),
        .ready_o              (),
        .busy_o               (),
        .done_o               (ad9144_done),
        .error_o              (ad9144_error),
        .error_code_o         (ad9144_error_code),
        .current_word_index_o (ad9144_word_index),
        .spi_cs_no            (ad9144_spi_cs_no),
        .spi_sclk_o           (ad9144_spi_sclk_o),
        .spi_sdio_o           (ad9144_spi_sdio_o),
        .spi_sdio_oe_o        (ad9144_spi_sdio_oe_o)
    );

    always @(posedge clk_i or negedge reset_ni) begin
        if (!reset_ni) begin
            state_reg        <= STATE_IDLE;
            done_o           <= 1'b0;
            config_done_o    <= 1'b0;
            error_o          <= 1'b0;
            error_code_o     <= ERROR_NONE;
            spi_error_code_o <= 3'd0;
        end else begin
            done_o <= 1'b0;

            // 只有 ready_o=1 才接受新一轮请求。允许从 COMPLETE/FAILED 直接重试；
            // 新请求会清除上一轮粘性状态。busy 期间的 start 被明确忽略。
            if (start_i && ready_o) begin
                config_done_o    <= 1'b0;
                error_o          <= 1'b0;
                error_code_o     <= ERROR_NONE;
                spi_error_code_o <= 3'd0;

                if (CONFIG_DATA_VALID == 0) begin
                    error_o      <= 1'b1;
                    error_code_o <= ERROR_CONFIG_DATA_NOT_VALID;
                    state_reg    <= STATE_FAILED;
                end else begin
                    state_reg <= STATE_LMK_START;
                end
            end else begin
                case (state_reg)
                    STATE_IDLE: begin
                        state_reg <= STATE_IDLE;
                    end

                    STATE_LMK_START: begin
                        state_reg <= STATE_LMK_WAIT;
                    end

                    STATE_LMK_WAIT: begin
                        if (lmk_error) begin
                            error_o          <= 1'b1;
                            error_code_o     <= ERROR_LMK_SPI;
                            spi_error_code_o <= lmk_error_code;
                            state_reg        <= STATE_FAILED;
                        end else if (lmk_done) begin
                            state_reg <= STATE_AD9250_START;
                        end
                    end

                    STATE_AD9250_START: begin
                        state_reg <= STATE_AD9250_WAIT;
                    end

                    STATE_AD9250_WAIT: begin
                        if (ad9250_error) begin
                            error_o          <= 1'b1;
                            error_code_o     <= ERROR_AD9250_SPI;
                            spi_error_code_o <= ad9250_error_code;
                            state_reg        <= STATE_FAILED;
                        end else if (ad9250_done) begin
                            state_reg <= STATE_AD9144_START;
                        end
                    end

                    STATE_AD9144_START: begin
                        state_reg <= STATE_AD9144_WAIT;
                    end

                    STATE_AD9144_WAIT: begin
                        if (ad9144_error) begin
                            error_o          <= 1'b1;
                            error_code_o     <= ERROR_AD9144_SPI;
                            spi_error_code_o <= ad9144_error_code;
                            state_reg        <= STATE_FAILED;
                        end else if (ad9144_done) begin
                            done_o        <= 1'b1;
                            config_done_o <= 1'b1;
                            state_reg     <= STATE_COMPLETE;
                        end
                    end

                    STATE_COMPLETE: begin
                        state_reg <= STATE_COMPLETE;
                    end

                    STATE_FAILED: begin
                        state_reg <= STATE_FAILED;
                    end

                    default: begin
                        error_o          <= 1'b1;
                        error_code_o     <= ERROR_ILLEGAL_STATE;
                        spi_error_code_o <= 3'd0;
                        state_reg        <= STATE_FAILED;
                    end
                endcase
            end
        end
    end

endmodule

`default_nettype wire
