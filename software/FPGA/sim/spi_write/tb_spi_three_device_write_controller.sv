`timescale 1ns / 1ps
`default_nettype none

// ============================================================================
// File        : tb_spi_three_device_write_controller.sv
// Project     : Portable Direct-Sampling VNA
// Description : 三套独立 SPI 主机、三套 XPM ROM 和顺序配置总控的自检仿真。
//
// Coverage:
//   - 三颗器件各自从独立 ROM 读取四个完整 24-bit 帧；
//   - 严格验证 LMK04828 -> AD9250 -> AD9144 的整表发送顺序；
//   - 三套主机使用不同分频值，验证每颗器件的独立 SCLK；
//   - 验证 MSB first、每帧位数、ROM 地址递增、总完成状态和安全空闲态；
//   - 验证任意时刻最多只有一颗器件的 CS 为低。
//
// Boundary:
//   - ROM 内容是仿真示例，不是可用于真实器件的配置表。
//   - 本测试不验证 FPGA 管脚、电平、板级时序余量或芯片响应。
// ============================================================================

module tb_spi_three_device_write_controller;

    localparam integer FRAME_WIDTH = 24;
    localparam integer WORD_COUNT  = 4;
    localparam integer LMK_DIV     = 2;
    localparam integer AD9250_DIV  = 3;
    localparam integer AD9144_DIV  = 4;

    logic        clk_i;
    logic        reset_ni;
    logic        start_i;

    wire         ready_o;
    wire         busy_o;
    wire         done_o;
    wire         config_done_o;
    wire         error_o;
    wire [3:0]   error_code_o;
    wire [2:0]   spi_error_code_o;
    wire [1:0]   active_device_o;
    wire [15:0]  current_word_index_o;

    wire         lmk_spi_cs_no;
    wire         lmk_spi_sclk_o;
    wire         lmk_spi_sdio_o;
    wire         lmk_spi_sdio_oe_o;

    wire         ad9250_spi_cs_no;
    wire         ad9250_spi_sclk_o;
    wire         ad9250_spi_sdio_o;
    wire         ad9250_spi_sdio_oe_o;

    wire         ad9144_spi_cs_no;
    wire         ad9144_spi_sclk_o;
    wire         ad9144_spi_sdio_o;
    wire         ad9144_spi_sdio_oe_o;

    wire         guard_ready;
    wire         guard_busy;
    wire         guard_done;
    wire         guard_config_done;
    wire         guard_error;
    wire [3:0]   guard_error_code;

    integer      failure_count;
    integer      clk_cycle_count;
    integer      sequence_slot;
    integer      lmk_frame_count;
    integer      ad9250_frame_count;
    integer      ad9144_frame_count;
    integer      lmk_bit_count;
    integer      ad9250_bit_count;
    integer      ad9144_bit_count;
    integer      lmk_last_rise_cycle;
    integer      ad9250_last_rise_cycle;
    integer      ad9144_last_rise_cycle;

    logic        monitor_enable;
    logic [23:0] lmk_captured_frame;
    logic [23:0] ad9250_captured_frame;
    logic [23:0] ad9144_captured_frame;

    spi_three_device_write_controller #(
        .CONFIG_DATA_VALID          (1),
        .LMK_CLK_DIV                (LMK_DIV),
        .AD9250_CLK_DIV             (AD9250_DIV),
        .AD9144_CLK_DIV             (AD9144_DIV)
    ) u_dut (
        .clk_i                    (clk_i),
        .reset_ni                 (reset_ni),
        .start_i                  (start_i),
        .ready_o                  (ready_o),
        .busy_o                   (busy_o),
        .done_o                   (done_o),
        .config_done_o            (config_done_o),
        .error_o                  (error_o),
        .error_code_o             (error_code_o),
        .spi_error_code_o         (spi_error_code_o),
        .active_device_o          (active_device_o),
        .current_word_index_o     (current_word_index_o),
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

    // 第二个实例只验证默认安全门：占位 ROM 未确认时必须拒绝 start，且不产生
    // 任何 SPI 活动。物理输出不参与主功能监视，因此明确悬空。
    spi_three_device_write_controller #(
        .CONFIG_DATA_VALID (0)
    ) u_guard_dut (
        .clk_i                    (clk_i),
        .reset_ni                 (reset_ni),
        .start_i                  (start_i),
        .ready_o                  (guard_ready),
        .busy_o                   (guard_busy),
        .done_o                   (guard_done),
        .config_done_o            (guard_config_done),
        .error_o                  (guard_error),
        .error_code_o             (guard_error_code),
        .spi_error_code_o         (),
        .active_device_o          (),
        .current_word_index_o     (),
        .lmk_spi_cs_no            (),
        .lmk_spi_sclk_o           (),
        .lmk_spi_sdio_o           (),
        .lmk_spi_sdio_oe_o        (),
        .ad9250_spi_cs_no         (),
        .ad9250_spi_sclk_o        (),
        .ad9250_spi_sdio_o        (),
        .ad9250_spi_sdio_oe_o     (),
        .ad9144_spi_cs_no         (),
        .ad9144_spi_sclk_o        (),
        .ad9144_spi_sdio_o        (),
        .ad9144_spi_sdio_oe_o     ()
    );

    initial begin
        clk_i = 1'b0;
        forever #5 clk_i = ~clk_i;
    end

    function automatic logic [23:0] expected_lmk_frame(input integer index);
        begin
            case (index)
                0: expected_lmk_frame = 24'h12_3456;
                1: expected_lmk_frame = 24'hA5_C3E7;
                2: expected_lmk_frame = 24'h00_FF01;
                3: expected_lmk_frame = 24'h5A_A55A;
                default: expected_lmk_frame = 24'hXX_XXXX;
            endcase
        end
    endfunction

    function automatic logic [23:0] expected_ad9250_frame(input integer index);
        begin
            case (index)
                0: expected_ad9250_frame = 24'h80_A5C3;
                1: expected_ad9250_frame = 24'h00_1234;
                2: expected_ad9250_frame = 24'hFE_DCBA;
                3: expected_ad9250_frame = 24'h55_AA00;
                default: expected_ad9250_frame = 24'hXX_XXXX;
            endcase
        end
    endfunction

    function automatic logic [23:0] expected_ad9144_frame(input integer index);
        begin
            case (index)
                0: expected_ad9144_frame = 24'h89_ABCD;
                1: expected_ad9144_frame = 24'h10_2030;
                2: expected_ad9144_frame = 24'hC3_D4E5;
                3: expected_ad9144_frame = 24'hBA_DF00;
                default: expected_ad9144_frame = 24'hXX_XXXX;
            endcase
        end
    endfunction

    task automatic report_failure(input string message);
        begin
            failure_count = failure_count + 1;
            $display("[%0t] FAIL: %s", $time, message);
        end
    endtask

    // 管理时钟计数器同时检查三套物理 SPI 不会并行拉低片选。
    always @(posedge clk_i) begin
        clk_cycle_count = clk_cycle_count + 1;
        #1;

        if (monitor_enable) begin
            if ((!lmk_spi_cs_no + !ad9250_spi_cs_no + !ad9144_spi_cs_no) > 1)
                report_failure("More than one device CS is active");
        end
    end

    // 每一帧开始时检查器件顺序、活动器件和 ROM 地址。
    always @(negedge lmk_spi_cs_no) begin
        if (monitor_enable) begin
            if ((sequence_slot < 0) || (sequence_slot >= WORD_COUNT))
                report_failure("LMK frame appeared outside the LMK sequence range");
            if (active_device_o !== 2'd0)
                report_failure("active_device_o is not LMK during LMK transfer");
            if (current_word_index_o !== lmk_frame_count[15:0])
                report_failure("LMK ROM address does not match frame order");

            sequence_slot       = sequence_slot + 1;
            lmk_bit_count       = 0;
            lmk_captured_frame  = 24'd0;
            lmk_last_rise_cycle = -1;
        end
    end

    always @(negedge ad9250_spi_cs_no) begin
        if (monitor_enable) begin
            if ((sequence_slot < WORD_COUNT) || (sequence_slot >= (2 * WORD_COUNT)))
                report_failure("AD9250 frame appeared outside the AD9250 sequence range");
            if (active_device_o !== 2'd1)
                report_failure("active_device_o is not AD9250 during AD9250 transfer");
            if (current_word_index_o !== ad9250_frame_count[15:0])
                report_failure("AD9250 ROM address does not match frame order");

            sequence_slot          = sequence_slot + 1;
            ad9250_bit_count       = 0;
            ad9250_captured_frame  = 24'd0;
            ad9250_last_rise_cycle = -1;
        end
    end

    always @(negedge ad9144_spi_cs_no) begin
        if (monitor_enable) begin
            if ((sequence_slot < (2 * WORD_COUNT)) || (sequence_slot >= (3 * WORD_COUNT)))
                report_failure("AD9144 frame appeared outside the AD9144 sequence range");
            if (active_device_o !== 2'd2)
                report_failure("active_device_o is not AD9144 during AD9144 transfer");
            if (current_word_index_o !== ad9144_frame_count[15:0])
                report_failure("AD9144 ROM address does not match frame order");

            sequence_slot          = sequence_slot + 1;
            ad9144_bit_count       = 0;
            ad9144_captured_frame  = 24'd0;
            ad9144_last_rise_cycle = -1;
        end
    end

    // SPI Mode 0 在上升沿采样；同时检查独立分频值和 SDIO 输出使能。
    always @(posedge lmk_spi_sclk_o) begin
        if (monitor_enable) begin
            if ((lmk_spi_cs_no !== 1'b0) || (lmk_spi_sdio_oe_o !== 1'b1))
                report_failure("LMK SCLK edge occurred outside an active write");
            if ((lmk_last_rise_cycle >= 0) &&
                ((clk_cycle_count - lmk_last_rise_cycle) != (2 * LMK_DIV)))
                report_failure("LMK SCLK divider is incorrect");

            lmk_captured_frame  = {lmk_captured_frame[22:0], lmk_spi_sdio_o};
            lmk_bit_count       = lmk_bit_count + 1;
            lmk_last_rise_cycle = clk_cycle_count;
        end
    end

    always @(posedge ad9250_spi_sclk_o) begin
        if (monitor_enable) begin
            if ((ad9250_spi_cs_no !== 1'b0) || (ad9250_spi_sdio_oe_o !== 1'b1))
                report_failure("AD9250 SCLK edge occurred outside an active write");
            if ((ad9250_last_rise_cycle >= 0) &&
                ((clk_cycle_count - ad9250_last_rise_cycle) != (2 * AD9250_DIV)))
                report_failure("AD9250 SCLK divider is incorrect");

            ad9250_captured_frame  = {ad9250_captured_frame[22:0], ad9250_spi_sdio_o};
            ad9250_bit_count       = ad9250_bit_count + 1;
            ad9250_last_rise_cycle = clk_cycle_count;
        end
    end

    always @(posedge ad9144_spi_sclk_o) begin
        if (monitor_enable) begin
            if ((ad9144_spi_cs_no !== 1'b0) || (ad9144_spi_sdio_oe_o !== 1'b1))
                report_failure("AD9144 SCLK edge occurred outside an active write");
            if ((ad9144_last_rise_cycle >= 0) &&
                ((clk_cycle_count - ad9144_last_rise_cycle) != (2 * AD9144_DIV)))
                report_failure("AD9144 SCLK divider is incorrect");

            ad9144_captured_frame  = {ad9144_captured_frame[22:0], ad9144_spi_sdio_o};
            ad9144_bit_count       = ad9144_bit_count + 1;
            ad9144_last_rise_cycle = clk_cycle_count;
        end
    end

    // CS 上升沿结束一帧，比较完整 ROM 字和固定 24-bit 长度。
    always @(posedge lmk_spi_cs_no) begin
        if (monitor_enable && (lmk_bit_count > 0)) begin
            if (lmk_bit_count != FRAME_WIDTH)
                report_failure("LMK frame length is not 24 bits");
            if (lmk_captured_frame !== expected_lmk_frame(lmk_frame_count))
                report_failure("LMK transmitted frame does not match ROM data");
            lmk_frame_count = lmk_frame_count + 1;
        end
    end

    always @(posedge ad9250_spi_cs_no) begin
        if (monitor_enable && (ad9250_bit_count > 0)) begin
            if (ad9250_bit_count != FRAME_WIDTH)
                report_failure("AD9250 frame length is not 24 bits");
            if (ad9250_captured_frame !== expected_ad9250_frame(ad9250_frame_count))
                report_failure("AD9250 transmitted frame does not match ROM data");
            ad9250_frame_count = ad9250_frame_count + 1;
        end
    end

    always @(posedge ad9144_spi_cs_no) begin
        if (monitor_enable && (ad9144_bit_count > 0)) begin
            if (ad9144_bit_count != FRAME_WIDTH)
                report_failure("AD9144 frame length is not 24 bits");
            if (ad9144_captured_frame !== expected_ad9144_frame(ad9144_frame_count))
                report_failure("AD9144 transmitted frame does not match ROM data");
            ad9144_frame_count = ad9144_frame_count + 1;
        end
    end

    initial begin
        integer timeout_count;

        failure_count          = 0;
        clk_cycle_count        = 0;
        sequence_slot          = 0;
        lmk_frame_count        = 0;
        ad9250_frame_count     = 0;
        ad9144_frame_count     = 0;
        lmk_bit_count          = 0;
        ad9250_bit_count       = 0;
        ad9144_bit_count       = 0;
        lmk_last_rise_cycle    = -1;
        ad9250_last_rise_cycle = -1;
        ad9144_last_rise_cycle = -1;
        lmk_captured_frame     = 24'd0;
        ad9250_captured_frame  = 24'd0;
        ad9144_captured_frame  = 24'd0;
        monitor_enable         = 1'b0;
        reset_ni               = 1'b0;
        start_i                = 1'b0;

        repeat (5) @(posedge clk_i);
        #1;
        if ((lmk_spi_cs_no !== 1'b1) || (ad9250_spi_cs_no !== 1'b1) ||
            (ad9144_spi_cs_no !== 1'b1))
            report_failure("Reset did not place all CS outputs in the safe state");
        if ((lmk_spi_sclk_o !== 1'b0) || (ad9250_spi_sclk_o !== 1'b0) ||
            (ad9144_spi_sclk_o !== 1'b0))
            report_failure("Reset did not place all SCLK outputs in Mode-0 idle");

        @(negedge clk_i);
        reset_ni = 1'b1;
        repeat (3) @(posedge clk_i);
        #1;

        if ((ready_o !== 1'b1) || (busy_o !== 1'b0) ||
            (config_done_o !== 1'b0) || (error_o !== 1'b0))
            report_failure("Controller did not enter the documented idle state");

        monitor_enable = 1'b1;
        @(negedge clk_i);
        start_i = 1'b1;
        @(negedge clk_i);
        start_i = 1'b0;

        @(posedge clk_i);
        #1;
        if ((guard_ready !== 1'b1) || (guard_busy !== 1'b0) ||
            (guard_done !== 1'b0) || (guard_config_done !== 1'b0) ||
            (guard_error !== 1'b1) || (guard_error_code !== 4'd1))
            report_failure("CONFIG_DATA_VALID=0 did not reject the placeholder ROM");

        timeout_count = 0;
        while ((done_o !== 1'b1) && (timeout_count < 20000)) begin
            @(posedge clk_i);
            #1;
            timeout_count = timeout_count + 1;
        end

        if (timeout_count >= 20000)
            report_failure("Three-device ROM configuration timed out in the testbench");
        if (error_o !== 1'b0)
            report_failure("Valid ROM configuration raised error_o");
        if (error_code_o !== 4'd0)
            report_failure("Valid ROM configuration returned a nonzero controller error");
        if (spi_error_code_o !== 3'd0)
            report_failure("Valid ROM configuration returned a nonzero SPI error");
        if ((lmk_frame_count != WORD_COUNT) ||
            (ad9250_frame_count != WORD_COUNT) ||
            (ad9144_frame_count != WORD_COUNT))
            report_failure("One or more device ROM tables were not completely transmitted");
        if (sequence_slot != (3 * WORD_COUNT))
            report_failure("Total frame count or device ordering is incorrect");
        if ((config_done_o !== 1'b1) || (busy_o !== 1'b0) ||
            (ready_o !== 1'b1) || (active_device_o !== 2'd3))
            report_failure("Controller completion status is incorrect");

        @(posedge clk_i);
        #1;
        if (done_o !== 1'b0)
            report_failure("done_o is not a one-cycle pulse");

        monitor_enable = 1'b0;

        if (failure_count == 0) begin
            $display("THREE-DEVICE ROM SPI SELF-TEST: PASS");
            $finish;
        end else begin
            $fatal(1, "THREE-DEVICE ROM SPI SELF-TEST: FAIL (%0d failures)", failure_count);
        end
    end

endmodule

`default_nettype wire
