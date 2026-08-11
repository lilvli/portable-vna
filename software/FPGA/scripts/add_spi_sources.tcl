# ============================================================================
# File        : add_spi_sources.tcl
# Purpose     : 幂等地把三主机、三 ROM SPI RTL 与 testbench 加入现有工程。
# Input       : 当前受控源文件。
# Output      : 更新现有 Vivado 工程的 sources_1/sim_1 文件集。
# Boundary    : 不修改 top.v，不运行综合/实现，不生成 bitstream。
# ============================================================================

set script_dir   [file dirname [file normalize [info script]]]
set fpga_root    [file normalize [file join $script_dir ..]]
set project_file [file normalize [file join $fpga_root prj prj.xpr]]

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
set testbench      [file normalize [file join $fpga_root sim spi_write tb_spi_three_device_write_controller.sv]]

set lmk_rom    [file normalize [file join $fpga_root config spi lmk04828_config_example.mem]]
set ad9250_rom [file normalize [file join $fpga_root config spi ad9250_config_example.mem]]
set ad9144_rom [file normalize [file join $fpga_root config spi ad9144_config_example.mem]]

set rtl_files [list $master_rtl $rom_rtl $sender_rtl $controller_rtl]
set rom_files [list $lmk_rom $ad9250_rom $ad9144_rom]

foreach required_file [concat [list $project_file $testbench] $rtl_files $rom_files] {
    if {![file exists $required_file]} {
        error "Required project/source file is missing: $required_file"
    }
}

open_project $project_file
set_property XPM_LIBRARIES {XPM_MEMORY} [current_project]

foreach rtl_file $rtl_files {
    if {[llength [get_files -quiet $rtl_file]] == 0} {
        add_files -norecurse -fileset sources_1 $rtl_file
    }
}

foreach rom_file $rom_files {
    if {[llength [get_files -quiet $rom_file]] == 0} {
        add_files -norecurse -fileset sources_1 $rom_file
    }
}

if {[llength [get_files -quiet $testbench]] == 0} {
    add_files -norecurse -fileset sim_1 $testbench
}

set_property file_type SystemVerilog [get_files $testbench]
set_property top top [get_filesets sources_1]
set_property top tb_spi_three_device_write_controller [get_filesets sim_1]

update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

# Vivado project-mode 的 add_files/set_property 会原位持久化 XPR；正常 close_project
# 完成保存生命周期。Vivado 2025.2 不使用无参数 save_project 或同路径 save_project_as。
close_project

puts "SPI sources were added to prj/prj.xpr successfully."
