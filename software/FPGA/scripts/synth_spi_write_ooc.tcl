# ============================================================================
# File        : synth_spi_write_ooc.tcl
# Purpose     : 对三主机、三 ROM 顺序配置控制器执行独立综合和 100 MHz 时序检查。
# Input       : 四个受控 Verilog-2001 RTL 文件和三份示例 ROM。
# Output      : output/spi_write_ooc/ 下的 DCP、资源和时序报告。
# Failure     : 读取、综合或检查失败时 Vivado 返回失败。
# Boundary    : 不修改 prj/prj.xpr，不生成 bitstream，不下载 FPGA。
# ============================================================================

set script_dir [file dirname [file normalize [info script]]]
set fpga_root  [file normalize [file join $script_dir ..]]
set build_dir  [file normalize [file join $fpga_root output spi_rom_write_ooc]]

# 当前工作站的用户 Tcl Store 可能损坏。仅在本 Vivado 进程中回退到安装目录。
if {[info exists ::env(XILINX_VIVADO)]} {
    set tcl_store_path [file join $::env(XILINX_VIVADO) data XilinxTclStore]
    set appinit_path [file join $tcl_store_path support appinit]
    if {[file isdirectory $appinit_path]} {
        lappend auto_path $appinit_path
    }
    set tclapp_repo [file join $tcl_store_path tclapp]
    if {[file isdirectory $tclapp_repo]} {
        set ::env(XILINX_TCLAPP_REPO) $tclapp_repo
        foreach app_path [glob -nocomplain -types d [file join $tclapp_repo * *]] {
            lappend auto_path $app_path
        }
    }
}

set master_rtl     [file normalize [file join $fpga_root rtl control spi_write_master.v]]
set rom_rtl        [file normalize [file join $fpga_root rtl control spi_config_rom.v]]
set sender_rtl     [file normalize [file join $fpga_root rtl control spi_rom_device_sender.v]]
set controller_rtl [file normalize [file join $fpga_root rtl control spi_three_device_write_controller.v]]

set lmk_rom    [file normalize [file join $fpga_root config spi lmk04828_config_example.mem]]
set ad9250_rom [file normalize [file join $fpga_root config spi ad9250_config_example.mem]]
set ad9144_rom [file normalize [file join $fpga_root config spi ad9144_config_example.mem]]

set rtl_files [list $master_rtl $rom_rtl $sender_rtl $controller_rtl]
set rom_files [list $lmk_rom $ad9250_rom $ad9144_rom]

foreach required_file [concat $rtl_files $rom_files] {
    if {![file exists $required_file]} {
        error "Required SPI source is missing: $required_file"
    }
}

create_project spi_rom_write_ooc $build_dir -part xcku5p-ffvb676-2-i -force

set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]
add_files -norecurse -fileset sources_1 $rtl_files
add_files -norecurse -fileset sources_1 $rom_files
update_compile_order -fileset sources_1

# XPM SPROM 的公共底层包含未使用的写端口，Synth 8-7129 对这些固定空闲端口
# 逐 bit 报告；OOC 顶层的 16-bit 调试索引高位固定为 0，触发 Synth 8-3917。
# 两类消息均已审查且不影响功能，只在本独立综合进程内降为 INFO。
set_msg_config -id {Synth 8-7129} -new_severity INFO
set_msg_config -id {Synth 8-3917} -new_severity INFO

synth_design \
    -top spi_three_device_write_controller \
    -part xcku5p-ffvb676-2-i \
    -mode out_of_context \
    -generic CONFIG_DATA_VALID=1

create_clock -name clk_i -period 10.000 [get_ports clk_i]

report_utilization \
    -file [file join $build_dir utilization.rpt]
report_timing_summary \
    -delay_type min_max \
    -max_paths 10 \
    -file [file join $build_dir timing_summary.rpt]
report_cdc \
    -file [file join $build_dir cdc.rpt]

write_checkpoint -force [file join $build_dir spi_three_device_write_controller.dcp]

set timing_paths [get_timing_paths -max_paths 1 -nworst 1]
if {[llength $timing_paths] == 0} {
    error "No timing path was found after SPI OOC synthesis"
}

set worst_slack [get_property SLACK [lindex $timing_paths 0]]
if {$worst_slack < 0.0} {
    error "SPI OOC timing failed: worst slack is $worst_slack ns"
}

puts "SPI WRITE OOC SYNTHESIS: PASS; worst slack = $worst_slack ns"

close_project
