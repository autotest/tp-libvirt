import re
import os
import logging
import time

from virttest import virsh
from virttest import utils_libvirtd
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def manipulate_domain(test, vm_name, action, recover=False):
    """
    Save/managedsave/S3/S4 domain or recover it.
    """
    tmp_dir = data_dir.get_tmp_dir()
    save_file = os.path.join(tmp_dir, vm_name + ".save")
    if not recover:
        if action == "save":
            save_option = ""
            result = virsh.save(vm_name, save_file, save_option,
                                ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        elif action == "managedsave":
            managedsave_option = ""
            result = virsh.managedsave(vm_name, managedsave_option,
                                       ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        elif action == "s3":
            suspend_target = "mem"
            result = virsh.dompmsuspend(vm_name, suspend_target,
                                        ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        elif action == "s4":
            suspend_target = "disk"
            result = virsh.dompmsuspend(vm_name, suspend_target,
                                        ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
            # Wait domain state change: 'in shutdown' -> 'shut off'
            utils_misc.wait_for(lambda: virsh.is_dead(vm_name), 5)
        else:
            logging.debug("No operation for the domain")

    else:
        if action == "save":
            if os.path.exists(save_file):
                result = virsh.restore(save_file, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                os.remove(save_file)
            else:
                test.error("No save file for domain restore")
        elif action in ["managedsave", "s4"]:
            result = virsh.start(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        elif action == "s3":
            suspend_target = "mem"
            result = virsh.dompmwakeup(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        else:
            logging.debug("No need recover the domain")


def run(test, params, env):
    """
    Test command: virsh setmem.

    1) Prepare vm environment.
    2) Handle params
    3) Prepare libvirtd status.
    4) Run test command and wait for current memory's stable.
    5) Recover environment.
    4) Check result.
    """

    def get_vm_usable_mem(session):
        """
        Get total usable RAM from /proc/meminfo
        """
        cmd = "cat /proc/meminfo"
        proc_mem = session.cmd_output(cmd)
        total_usable_mem = re.search(r'MemTotal:\s+(\d+)\s+[kK]B',
                                     proc_mem).group(1)
        return int(total_usable_mem)

    def vm_unusable_mem(session):
        """
        Get the unusable RAM of the VM.
        """
        # Get total physical memory from dmidecode
        cmd = "dmidecode -t 17"
        dmi_mem = session.cmd_output(cmd)
        dmi_mem_size = re.findall(r'Size:\s(\d+\s+[K|M|G]B)', dmi_mem)
        if not dmi_mem_size:
            test.fail("Cannot get memory size info inside VM.")
        total_physical_mem = 0
        for size_info in dmi_mem_size:
            mem_size = int(size_info.split()[0].strip())
            mem_unit = size_info.split()[1].strip()
            if mem_unit.lower() == 'kb':
                total_physical_mem += mem_size
            elif mem_unit.lower() == 'mb':
                total_physical_mem += mem_size * 1024
            elif mem_unit.lower() == 'gb':
                total_physical_mem += mem_size * 1048576
        return total_physical_mem - get_vm_usable_mem(session)

    def make_domref(domarg, vm_ref, domid, vm_name, domuuid):
        """
        Create domain options of command
        """
        # Specify domain as argument or parameter
        if domarg == "yes":
            dom_darg_key = "domainarg"
        else:
            dom_darg_key = "domain"

        # How to reference domain
        if vm_ref == "domid":
            dom_darg_value = domid
        elif vm_ref == "domname":
            dom_darg_value = vm_name
        elif vm_ref == "domuuid":
            dom_darg_value = domuuid
        elif vm_ref == "none":
            dom_darg_value = None
        elif vm_ref == "emptystring":
            dom_darg_value = '""'
        else:  # stick in value directly
            dom_darg_value = vm_ref

        return {dom_darg_key: dom_darg_value}

    def make_sizeref(sizearg, mem_ref, original_mem):
        """
        Create size options of command
        """
        if sizearg == "yes":
            size_darg_key = "sizearg"
        else:
            size_darg_key = "size"

        if mem_ref == "halfless":
            size_darg_value = "%d" % (original_mem // 2)
        elif mem_ref == "halfmore":
            size_darg_value = "%d" % int(original_mem * 1.5)  # no fraction
        elif mem_ref == "same":
            size_darg_value = "%d" % original_mem
        elif mem_ref == "emptystring":
            size_darg_value = '""'
        elif mem_ref == "zero":
            size_darg_value = "0"
        elif mem_ref == "toosmall":
            size_darg_value = "1024"
        elif mem_ref == "toobig":
            size_darg_value = "1099511627776"  # (KiB) One Petabyte
        elif mem_ref == "none":
            size_darg_value = None
        else:  # stick in value directly
            size_darg_value = mem_ref

        return {size_darg_key: size_darg_value}

    def cal_deviation(actual, expected):
        """
        Calculate deviation of actual result and expected result
        """
        numerator = float(actual)
        denominator = float(expected)
        if numerator > denominator:
            numerator = denominator
            denominator = float(actual)
        return 100 - (100 * (numerator / denominator))

    def is_old_libvirt():
        """
        Check if libvirt is old version
        """
        regex = r'\s+\[--size\]\s+'
        return bool(not virsh.has_command_help_match('setmem', regex))

    def print_debug_stats(original_inside_mem, original_outside_mem,
                          test_inside_mem, test_outside_mem,
                          expected_outside_mem, expected_inside_mem,
                          delta_percentage, unusable_mem):
        """
        Print debug message for test
        """
        # Calculate deviation
        inside_deviation = cal_deviation(test_inside_mem, expected_inside_mem)
        outside_deviation = cal_deviation(test_outside_mem, expected_outside_mem)
        dbgmsg = ("Unusable memory of VM   : %d KiB\n"
                  "Original inside memory  : %d KiB\n"
                  "Expected inside memory  : %d KiB\n"
                  "Actual inside memory    : %d KiB\n"
                  "Inside memory deviation : %0.2f%%\n"
                  "Original outside memory : %d KiB\n"
                  "Expected outside memory : %d KiB\n"
                  "Actual outside memory   : %d KiB\n"
                  "Outside memory deviation: %0.2f%%\n"
                  "Acceptable deviation    : %0.2f%%" % (
                      unusable_mem,
                      original_inside_mem,
                      expected_inside_mem,
                      test_inside_mem,
                      inside_deviation,
                      original_outside_mem,
                      expected_outside_mem,
                      test_outside_mem,
                      outside_deviation,
                      delta_percentage))
        for dbgline in dbgmsg.splitlines():
            logging.debug(dbgline)

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_ref = params.get("setmem_vm_ref", "")
    mem_ref = params.get("setmem_mem_ref", "")
    flags = params.get("setmem_flags", "")
    status_error = "yes" == params.get("status_error", "no")
    old_libvirt_fail = "yes" == params.get("setmem_old_libvirt_fail", "no")
    quiesce_delay = int(params.get("setmem_quiesce_delay", "1"))
    domarg = params.get("setmem_domarg", "no")
    sizearg = params.get("setmem_sizearg", "no")
    libvirt_status = params.get("libvirt", "on")
    delta_percentage = float(params.get("setmem_delta_per", "10"))
    start_vm = "yes" == params.get("start_vm", "yes")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    paused_after_start_vm = "yes" == params.get("paused_after_start_vm", "no")
    manipulate_dom_before_setmem = "yes" == params.get(
        "manipulate_dom_before_setmem", "no")
    manipulate_dom_after_setmem = "yes" == params.get(
        "manipulate_dom_after_setmem", "no")
    manipulate_action = params.get("manipulate_action", "")
    readonly = "yes" == params.get("setmem_readonly", "no")
    expect_msg = params.get("setmem_err_msg")
    driver_packed = params.get("driver_packed", "on")
    with_packed = "yes" == params.get("with_packed", "no")
    expect_xml_line = params.get("expect_xml_line")
    expect_qemu_line = params.get("expect_qemu_line")

    vm = env.get_vm(vm_name)
    # Back up domain XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    if with_packed and not libvirt_version.version_compare(6, 3, 0):
        test.cancel("The virtio packed attribute is not supported in"
                    " current libvirt version.")

    vmosxml = vmxml.os
    need_mkswap = False
    if manipulate_action in ['s3', 's4']:
        vm.destroy()
        BIOS_BIN = "/usr/share/seabios/bios.bin"
        if os.path.isfile(BIOS_BIN):
            vmosxml.loader = BIOS_BIN
            vmxml.os = vmosxml
            vmxml.sync()
        else:
            logging.error("Not find %s on host", BIOS_BIN)
        vmxml.set_pm_suspend(vm_name, "yes", "yes")
        vm.prepare_guest_agent()
        if manipulate_action == "s4":
            need_mkswap = not vm.has_swap()
        if need_mkswap:
            logging.debug("Creating swap partition")
            vm.create_swap_partition()

    memballoon_model = params.get("memballoon_model", "")
    if memballoon_model:
        vm.destroy()
        vmxml.del_device('memballoon', by_tag=True)
        memballoon_xml = vmxml.get_device_class('memballoon')()
        memballoon_xml.model = memballoon_model
        if with_packed:
            memballoon_xml.driver_packed = driver_packed
        vmxml.add_device(memballoon_xml)
        logging.info(memballoon_xml)
        vmxml.sync()
        vm.start()
    # Check guest dumpxml and qemu command in with_packed attribute
    if with_packed:
        expect_xml_line = expect_xml_line % driver_packed
        expect_qemu_line = expect_qemu_line % driver_packed
        libvirt.check_dumpxml(vm, expect_xml_line)
        libvirt.check_qemu_cmd_line(expect_qemu_line)

    remove_balloon_driver = "yes" == params.get("remove_balloon_driver", "no")
    if remove_balloon_driver:
        if not vm.is_alive():
            logging.error("Can't remove module as guest not running")
        else:
            session = vm.wait_for_login()
            cmd = "rmmod virtio_balloon"
            s_rmmod, o_rmmod = session.cmd_status_output(cmd)
            if s_rmmod != 0:
                logging.error("Fail to remove module virtio_balloon in guest:\n%s",
                              o_rmmod)
            session.close()
    # Get original data
    domid = vm.get_id()
    domuuid = vm.get_uuid()
    uri = vm.connect_uri
    if not vm.is_alive():
        vm.start()
    session = vm.wait_for_login()
    if session.cmd_status('dmidecode'):
        # The physical memory size is in vm xml, use it when dmideode not
        # supported
        unusable_mem = int(vmxml.max_mem) - get_vm_usable_mem(session)
    else:
        unusable_mem = vm_unusable_mem(session)
    original_outside_mem = vm.get_used_mem()
    original_inside_mem = get_vm_usable_mem(session)
    session.close()
    # Prepare VM state
    if not start_vm:
        vm.destroy()
    else:
        if paused_after_start_vm:
            vm.pause()
    old_libvirt = is_old_libvirt()
    if old_libvirt:
        logging.info("Running test on older libvirt")
        use_kilobytes = True
    else:
        logging.info("Running test on newer libvirt")
        use_kilobytes = False

    # Argument pattern is complex, build with dargs
    dargs = {'flagstr': flags,
             'use_kilobytes': use_kilobytes,
             'uri': uri, 'ignore_status': True, "debug": True,
             'readonly': readonly}
    dargs.update(make_domref(domarg, vm_ref, domid, vm_name, domuuid))
    dargs.update(make_sizeref(sizearg, mem_ref, original_outside_mem))

    # Prepare libvirtd status
    libvirtd = utils_libvirtd.Libvirtd()
    if libvirt_status == "off":
        libvirtd.stop()
    else:
        if not libvirtd.is_running():
            libvirtd.start()

    if status_error or (old_libvirt_fail & old_libvirt):
        logging.info("Error Test: Expecting an error to occur!")

    try:
        memory_change = True
        if manipulate_dom_before_setmem:
            manipulate_domain(test, vm_name, manipulate_action)
            if manipulate_action in ['save', 'managedsave', 's4']:
                memory_change = False

        result = virsh.setmem(**dargs)
        status = result.exit_status

        if status is 0:
            logging.info(
                "Waiting %d seconds for VM memory to settle", quiesce_delay)
            # It takes time for kernel to settle on new memory
            # and current clean pages is not predictable. Therefor,
            # extremely difficult to determine quiescence, so
            # sleep one second per error percent is reasonable option.
            time.sleep(quiesce_delay)

        if manipulate_dom_before_setmem:
            manipulate_domain(test, vm_name, manipulate_action, True)
        if manipulate_dom_after_setmem:
            manipulate_domain(test, vm_name, manipulate_action)
            manipulate_domain(test, vm_name, manipulate_action, True)

        # Recover libvirtd status
        if libvirt_status == "off":
            libvirtd.start()

        # Gather stats if not running error test
        if not status_error and not old_libvirt_fail:
            # Expected results for both inside and outside
            if remove_balloon_driver:
                expected_mem = original_outside_mem
            else:
                if not memory_change:
                    expected_mem = original_inside_mem
                elif sizearg == "yes":
                    expected_mem = int(dargs["sizearg"])
                else:
                    expected_mem = int(dargs["size"])
            if memory_change:
                # Should minus unusable memory for inside memory check
                expected_inside_mem = expected_mem - unusable_mem
                expected_outside_mem = expected_mem
            else:
                expected_inside_mem = expected_mem
                expected_outside_mem = original_outside_mem

            def get_vm_mem():
                """
                Test results for both inside and outside
                :return: Get vm memory for both inside and outside
                """
                if not memory_change:
                    test_inside_mem = original_inside_mem
                    test_outside_mem = original_outside_mem
                else:
                    if vm.state() == "shut off":
                        vm.start()
                    elif vm.state() == "paused":
                        # Make sure it's never paused
                        vm.resume()
                    session = vm.wait_for_login()

                    # Actual results
                    test_inside_mem = get_vm_usable_mem(session)
                    session.close()
                    test_outside_mem = vm.get_used_mem()
                return (test_inside_mem, test_outside_mem)

            # Don't care about memory comparison on error test
            def verify_outside_result():
                _, test_outside_mem = get_vm_mem()
                return(cal_deviation(test_outside_mem, expected_outside_mem) <= delta_percentage)

            def verify_inside_result():
                test_inside_mem, _ = get_vm_mem()
                return(cal_deviation(test_inside_mem, expected_inside_mem) <= delta_percentage)

            msg = "test conditions not met: "
            error_flag = 0
            if status is not 0:
                error_flag = 1
                msg += "Non-zero virsh setmem exit code. "
            if not utils_misc.wait_for(verify_outside_result, timeout=240):
                error_flag = 1
                msg += "Outside memory deviated. "
            if not utils_misc.wait_for(verify_inside_result, timeout=240):
                error_flag = 1
                msg += "Inside memory deviated. "

            test_inside_mem, test_outside_mem = get_vm_mem()
            print_debug_stats(original_inside_mem, original_outside_mem,
                              test_inside_mem, test_outside_mem,
                              expected_outside_mem, expected_inside_mem,
                              delta_percentage, unusable_mem)
            if error_flag:
                test.fail(msg)
        elif not status_error and old_libvirt_fail:
            if status is 0:
                if old_libvirt:
                    test.fail("Error test did not result in an error")
            else:
                if not old_libvirt:
                    test.fail("Newer libvirt failed when it should not")
        else:  # Verify an error test resulted in error
            if status is 0:
                test.fail("Error test did not result in an error")
            if expect_msg:
                libvirt.check_result(result, expect_msg.split(';'))
    finally:
        if need_mkswap:
            vm.cleanup_swap()
        vm.destroy()
        backup_xml.sync()
