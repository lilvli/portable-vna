`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : spi_write_master.v
// Project     : Portable Direct-Sampling VNA
// Module      : spi_write_master
// Version     : 1.0.0
// Description : 通用、只写、SPI Mode 0 主机。上层提供完整发送帧，本模块不解释
//               地址、读写位或数据字段，只负责按指定长度和速率发送。
//
// Clock domain:
//   - clk_i      : 唯一管理时钟域。
//
// Reset:
//   - reset_ni   : 低有效异步置位；释放前应在 clk_i 域外部同步。
//
// Transfer contract:
//   - start_i 只在 ready_o=1 时接受，建议保持一个 clk_i 周期。
//   - tx_data_i[tx_bit_count_i-1:0] 是有效帧，最高有效位先发送。
//   - clk_div_i 表示一个 SCLK 半周期包含多少个 clk_i 周期：
//         f_spi_sclk = f_clk_i / (2 * clk_div_i)
//   - SPI 固定为 Mode 0：SCLK 空闲为低，器件在上升沿采样，下降沿换数据。
//   - CS 建立时间和保持时间各为一个 SCLK 半周期。
//
// Error behavior:
//   - done_o/error_o 均为单个 clk_i 周期脉冲。
//   - 非法位宽、零分频或 busy 期间重复 start 不会破坏当前事务。
//
// Scope boundary:
//   - 不实现 SPI 读回、CPOL/CPHA 选择、器件寄存器解释或配置序列。
//   - 板级 IOBUF、IOSTANDARD 和管脚约束由系统顶层负责。
//
// Verification:
//   - sim/spi_write/tb_spi_three_device_write_controller.sv
//   - scripts/run_spi_write_sim.tcl
//   - scripts/synth_spi_write_ooc.tcl
//   - 当前验证仅覆盖数字仿真和独立综合，不代表真实器件 SPI 已验证。
// ============================================================================

module spi_write_master #(
    parameter integer MAX_DATA_WIDTH   = 64,
    parameter integer BIT_COUNT_WIDTH  = 7,
    parameter integer CLK_DIV_WIDTH    = 16
)(
    // 管理时钟和复位。
    input  wire                             clk_i,
    input  wire                             reset_ni,

    // 上层事务请求。请求参数在合法 start 时一次性锁存，busy 期间允许变化。
    input  wire                             start_i,
    input  wire [MAX_DATA_WIDTH-1:0]        tx_data_i,
    input  wire [BIT_COUNT_WIDTH-1:0]       tx_bit_count_i,
    input  wire [CLK_DIV_WIDTH-1:0]         clk_div_i,

    // 事务状态。ready 与 busy 互斥；done/error 是单拍事件。
    output wire                             ready_o,
    output wire                             busy_o,
    output reg                              done_o,
    output reg                              error_o,
    output reg  [2:0]                       error_code_o,

    // 逻辑 SPI 引脚。sdio_oe_o=0 时，系统顶层应将物理 SDIO 置为高阻。
    output wire                             spi_cs_no,
    output reg                              spi_sclk_o,
    output reg                              spi_sdio_o,
    output wire                             spi_sdio_oe_o
);

    // 状态机保持三段清晰时序：发送、CS 保持、空闲。
    localparam [1:0] STATE_IDLE     = 2'd0;
    localparam [1:0] STATE_TRANSFER = 2'd1;
    localparam [1:0] STATE_CS_HOLD  = 2'd2;

    // error_code_o 只在 error_o=1 的周期有效。
    localparam [2:0] ERROR_NONE          = 3'd0;
    localparam [2:0] ERROR_INVALID_WIDTH = 3'd1;
    localparam [2:0] ERROR_INVALID_DIV   = 3'd2;
    localparam [2:0] ERROR_BUSY_START    = 3'd3;
    localparam [2:0] ERROR_ILLEGAL_STATE = 3'd5;

    localparam [BIT_COUNT_WIDTH-1:0] MAX_DATA_WIDTH_VALUE = MAX_DATA_WIDTH;

    reg [1:0]                       state_reg;
    reg [MAX_DATA_WIDTH-1:0]        tx_data_reg;
    reg [BIT_COUNT_WIDTH-1:0]       bit_index_reg;
    reg [CLK_DIV_WIDTH-1:0]         clk_div_reg;
    reg [CLK_DIV_WIDTH-1:0]         half_period_count_reg;

    wire request_width_valid;
    wire request_divider_valid;

    assign request_width_valid =
        (tx_bit_count_i != {BIT_COUNT_WIDTH{1'b0}}) &&
        (tx_bit_count_i <= MAX_DATA_WIDTH_VALUE);

    assign request_divider_valid =
        (clk_div_i != {CLK_DIV_WIDTH{1'b0}});

    assign ready_o       = (state_reg == STATE_IDLE);
    assign busy_o        = (state_reg != STATE_IDLE);
    assign spi_cs_no     = ~busy_o;
    assign spi_sdio_oe_o = busy_o;

    // 参数错误属于模块集成错误，只在仿真中立即停止；综合逻辑仍保持纯 Verilog-2001。
    // synthesis translate_off
    initial begin
        if (MAX_DATA_WIDTH < 1) begin
            $display("SPI_WRITE_MASTER PARAMETER ERROR: MAX_DATA_WIDTH must be >= 1");
            $finish;
        end
        if ((1 << BIT_COUNT_WIDTH) <= MAX_DATA_WIDTH) begin
            $display("SPI_WRITE_MASTER PARAMETER ERROR: BIT_COUNT_WIDTH is too small");
            $finish;
        end
        if (CLK_DIV_WIDTH < 1) begin
            $display("SPI_WRITE_MASTER PARAMETER ERROR: CLK_DIV_WIDTH must be >= 1");
            $finish;
        end
    end
    // synthesis translate_on

    always @(posedge clk_i or negedge reset_ni) begin
        if (!reset_ni) begin
            state_reg               <= STATE_IDLE;
            tx_data_reg             <= {MAX_DATA_WIDTH{1'b0}};
            bit_index_reg           <= {BIT_COUNT_WIDTH{1'b0}};
            clk_div_reg             <= {{(CLK_DIV_WIDTH-1){1'b0}}, 1'b1};
            half_period_count_reg   <= {CLK_DIV_WIDTH{1'b0}};
            done_o                  <= 1'b0;
            error_o                 <= 1'b0;
            error_code_o            <= ERROR_NONE;
            spi_sclk_o              <= 1'b0;
            spi_sdio_o              <= 1'b0;
        end else begin
            // 事件输出默认为零，只有发生事件的当前周期被置位。
            done_o       <= 1'b0;
            error_o      <= 1'b0;
            error_code_o <= ERROR_NONE;

            // busy 期间的新请求被明确拒绝，但当前发送过程继续运行。
            if ((state_reg != STATE_IDLE) && start_i) begin
                error_o      <= 1'b1;
                error_code_o <= ERROR_BUSY_START;
            end

            case (state_reg)
                STATE_IDLE: begin
                    spi_sclk_o <= 1'b0;
                    spi_sdio_o <= 1'b0;

                    if (start_i) begin
                        if (!request_width_valid) begin
                            error_o      <= 1'b1;
                            error_code_o <= ERROR_INVALID_WIDTH;
                        end else if (!request_divider_valid) begin
                            error_o      <= 1'b1;
                            error_code_o <= ERROR_INVALID_DIV;
                        end else begin
                            // 在 CS 拉低前准备首个数据位。CS 由 state_reg 组合产生，
                            // 因而本时钟沿后同时进入低有效；首个 SCLK 上升沿还需等待
                            // 一个完整半周期，满足固定的 CS setup 时间。
                            tx_data_reg           <= tx_data_i;
                            bit_index_reg         <= tx_bit_count_i - 1'b1;
                            clk_div_reg           <= clk_div_i;
                            half_period_count_reg <= clk_div_i - 1'b1;
                            spi_sdio_o            <= tx_data_i[tx_bit_count_i - 1'b1];
                            state_reg             <= STATE_TRANSFER;
                        end
                    end
                end

                STATE_TRANSFER: begin
                    if (half_period_count_reg != {CLK_DIV_WIDTH{1'b0}}) begin
                        half_period_count_reg <= half_period_count_reg - 1'b1;
                    end else if (!spi_sclk_o) begin
                        // Mode 0 上升沿：外部器件在此采样已经稳定的数据位。
                        spi_sclk_o            <= 1'b1;
                        half_period_count_reg <= clk_div_reg - 1'b1;
                    end else begin
                        // Mode 0 下降沿：完成当前 bit，并准备下一 bit。
                        spi_sclk_o <= 1'b0;

                        if (bit_index_reg == {BIT_COUNT_WIDTH{1'b0}}) begin
                            // 最后一位已经在前一个上升沿被采样。继续保持 CS 低一个
                            // 半周期，再结束事务，避免 CS 与末个 SCLK 边沿同时变化。
                            half_period_count_reg <= clk_div_reg - 1'b1;
                            state_reg             <= STATE_CS_HOLD;
                        end else begin
                            bit_index_reg         <= bit_index_reg - 1'b1;
                            spi_sdio_o            <= tx_data_reg[bit_index_reg - 1'b1];
                            half_period_count_reg <= clk_div_reg - 1'b1;
                        end
                    end
                end

                STATE_CS_HOLD: begin
                    spi_sclk_o <= 1'b0;

                    if (half_period_count_reg != {CLK_DIV_WIDTH{1'b0}}) begin
                        half_period_count_reg <= half_period_count_reg - 1'b1;
                    end else begin
                        // 回到 IDLE 后 CS 自动拉高、SDIO 自动三态；done 保持一拍。
                        spi_sdio_o <= 1'b0;
                        done_o     <= 1'b1;
                        state_reg  <= STATE_IDLE;
                    end
                end

                default: begin
                    // 非法状态立即回到无片选、SCLK 低的安全态。
                    state_reg    <= STATE_IDLE;
                    spi_sclk_o   <= 1'b0;
                    spi_sdio_o   <= 1'b0;
                    error_o      <= 1'b1;
                    error_code_o <= ERROR_ILLEGAL_STATE;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
