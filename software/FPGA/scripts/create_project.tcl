# ============================================================================
# File        : create_project.tcl
# Project     : Portable Direct-Sampling VNA
# Description : Recreate the current minimal Vivado project from repository
#               sources. Generated project files are written under build/ and
#               are intentionally excluded from version control.
#
# Usage:
#   vivado -mode batch -source software/FPGA/scripts/create_project.tcl
#
# Verification boundary:
#   Successful project creation only proves that the controlled source list can
#   be loaded by Vivado. It is not synthesis, implementation, bitstream, or
#   hardware validation.
# ============================================================================

set script_path [file normalize [info script]]
set script_dir  [file dirname $script_path]
set fpga_root   [file normalize [file join $script_dir ".."]]
set build_dir   [file normalize [file join $fpga_root "build" "vivado"]]

file mkdir $build_dir

create_project portable_vna $build_dir \
    -part xcku5p-ffvb676-2-i \
    -force

set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

set rtl_files [list \
    [file join $fpga_root "rtl" "top.v"] \
    [file join $fpga_root "rtl" "control" "spi_write_master.v"] \
    [file join $fpga_root "rtl" "control" "spi_config_rom.v"] \
    [file join $fpga_root "rtl" "control" "spi_rom_device_sender.v"] \
    [file join $fpga_root "rtl" "control" "spi_three_device_write_controller.v"] \
]

foreach source_file $rtl_files {
    if {![file exists $source_file]} {
        error "Required RTL source is missing: $source_file"
    }
}

add_files -norecurse $rtl_files
set_property top top [get_filesets sources_1]

set simulation_file [file join $fpga_root \
    "sim" "spi_write" "tb_spi_three_device_write_controller.sv"]

if {![file exists $simulation_file]} {
    error "Required simulation source is missing: $simulation_file"
}

add_files -fileset sim_1 -norecurse $simulation_file
set_property top tb_spi_three_device_write_controller [get_filesets sim_1]

update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

puts "PORTABLE_VNA_PROJECT_CREATED"
puts "Project directory: $build_dir"
puts "Hardware validation: NOT PERFORMED"
