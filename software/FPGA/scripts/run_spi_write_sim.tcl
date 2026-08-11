# ============================================================================
# File        : run_spi_write_sim.tcl
# Purpose     : 在独立输出目录中创建临时 Vivado 工程并运行三器件 ROM SPI 自检。
# Input       : SPI RTL、三份示例 ROM 与 SystemVerilog testbench。
# Output      : output/spi_write_sim/ 下的可删除仿真生成物。
# Failure     : 编译、展开或 testbench 自检失败时 Vivado 返回失败。
# Boundary    : 不修改 prj/prj.xpr，不下载 FPGA，不访问真实器件。
# ============================================================================

set script_dir [file dirname [file normalize [info script]]]
set fpga_root  [file normalize [file join $script_dir ..]]
set build_dir  [file normalize [file join $fpga_root output spi_rom_write_sim]]

# 当前工作站的用户 Tcl Store 可能损坏。仅在本 Vivado 进程中回退到安装目录，
# 不修改用户目录或系统配置；其他环境中若目录不存在则保持默认行为。
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
set testbench      [file normalize [file join $fpga_root sim spi_write tb_spi_three_device_write_controller.sv]]

set lmk_rom    [file normalize [file join $fpga_root config spi lmk04828_config_example.mem]]
set ad9250_rom [file normalize [file join $fpga_root config spi ad9250_config_example.mem]]
set ad9144_rom [file normalize [file join $fpga_root config spi ad9144_config_example.mem]]

set rtl_files [list $master_rtl $rom_rtl $sender_rtl $controller_rtl]
set rom_files [list $lmk_rom $ad9250_rom $ad9144_rom]

foreach required_file [concat $rtl_files $rom_files [list $testbench]] {
    if {![file exists $required_file]} {
        error "Required SPI source is missing: $required_file"
    }
}

file mkdir [file dirname $build_dir]
create_project spi_rom_write_sim $build_dir -part xcku5p-ffvb676-2-i -force

set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]

add_files -norecurse -fileset sources_1 $rtl_files
add_files -norecurse -fileset sources_1 $rom_files
add_files -norecurse -fileset sim_1 $testbench
set_property file_type SystemVerilog [get_files $testbench]
set_property top tb_spi_three_device_write_controller [get_filesets sim_1]

update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

launch_simulation -mode behavioral
run all
close_sim
close_project
