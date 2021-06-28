import os
import re
import time
import logging

from avocado.utils import process
from avocado.utils import software_manager

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import utils_misc
from virttest import utils_libguestfs
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging.service import Factory
from virttest.staging.utils_memory import drop_caches


def run(test, params, env):
    """
    Test command: virsh managedsave.

    This command can save and destroy a
    running domain, so it can be restarted
    from the same state at a later time.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
    shutdown_timeout = int(params.get('shutdown_timeout', 60))

    # define function
    def vm_recover_check(option, libvirtd, check_shutdown=False):
        """
        Check if the vm can be recovered correctly.

        :param guest_name : Checked vm's name.
        :param option : managedsave command option.
        """
        # This time vm not be shut down
        if vm.is_alive():
            test.fail("Guest should be inactive")
        # Check vm managed save state.
        ret = virsh.dom_list("--managed-save --inactive", debug=True)
        vm_state1 = re.findall(r".*%s.*" % vm_name,
                               ret.stdout.strip())[0].split()[2]
        ret = virsh.dom_list("--managed-save --all", debug=True)
        vm_state2 = re.findall(r".*%s.*" % vm_name,
                               ret.stdout.strip())[0].split()[2]
        if vm_state1 != "saved" or vm_state2 != "saved":
            test.fail("Guest state should be saved")

        virsh.start(vm_name, debug=True)
        # This time vm should be in the list
        if vm.is_dead():
            test.fail("Guest should be active")
        # Restart libvirtd and check vm status again.
        libvirtd.restart()
        if vm.is_dead():
            test.fail("Guest should be active after"
                      " restarting libvirtd")
        # Check managed save file:
        if os.path.exists(managed_save_file):
            test.fail("Managed save image exist "
                      "after starting the domain")
        if option:
            if option.count("running"):
                if vm.is_dead() or vm.is_paused():
                    test.fail("Guest state should be"
                              " running after started"
                              " because of '--running' option")
            elif option.count("paused"):
                if not vm.is_paused():
                    test.fail("Guest state should be"
                              " paused after started"
                              " because of '--paused' option")
        else:
            if params.get("paused_after_start_vm") == "yes":
                if not vm.is_paused():
                    test.fail("Guest state should be"
                              " paused after started"
                              " because of initia guest state")
        if check_shutdown:
            # Resume the domain.
            if vm.is_paused():
                vm.resume()
            vm.wait_for_login()
            # Shutdown and start the domain,
            # it should be in runing state and can be login.
            vm.shutdown()
            if not vm.wait_for_shutdown(shutdown_timeout):
                test.fail('VM failed to shutdown')
            vm.start()
            vm.wait_for_login()

    def vm_undefine_check(vm_name):
        """
        Check if vm can be undefined with manage-save option
        """
        #backup xml file
        xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if not os.path.exists(managed_save_file):
            test.fail("Can't find managed save image")
        #undefine domain with no options.
        if not virsh.undefine(vm_name, options=None,
                              ignore_status=True).exit_status:
            test.fail("Guest shouldn't be undefined"
                      "while domain managed save image exists")
        #undefine domain with managed-save option.
        if virsh.undefine(vm_name, options="--managed-save",
                          ignore_status=True).exit_status:
            test.fail("Guest can't be undefine with "
                      "managed-save option")

        if os.path.exists(managed_save_file):
            test.fail("Managed save image exists"
                      " after undefining vm")
        #restore and start the vm.
        xml_backup.define()
        vm.start()

    def check_flags_parallel(virsh_cmd, bash_cmd, flags):
        """
        Run the commands parallel and check the output.
        """
        cmd = ("%s & %s" % (virsh_cmd, bash_cmd))
        ret = process.run(cmd, ignore_status=True, shell=True,
                          ignore_bg_processes=True)
        output = ret.stdout_text.strip()
        logging.debug("check flags output: %s" % output)
        lines = re.findall(r"flags:.(\d+)", output, re.M)
        logging.debug("Find all fdinfo flags: %s" % lines)
        lines = [int(i, 8) & flags for i in lines]
        if flags not in lines:
            test.fail("Checking flags %s failed" % flags)

        return ret

    def check_multi_guests(guests, start_delay, libvirt_guests):
        """
        Check start_delay option for multiple guests.
        """
        # Destroy vm first
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Clone given number of guests
        timeout = params.get("clone_timeout", 360)
        for i in range(int(guests)):
            dst_vm = "%s_%s" % (vm_name, i)
            utils_libguestfs.virt_clone_cmd(vm_name, dst_vm,
                                            True, timeout=timeout)
            virsh.start(dst_vm, debug=True)

        # Wait 10 seconds for vm to start
        time.sleep(10)
        is_systemd = process.run("cat /proc/1/comm", shell=True).stdout_text.count("systemd")
        if is_systemd:
            libvirt_guests.restart()
            pattern = r'(.+ \d\d:\d\d:\d\d).+: Resuming guest .*?[\n]*.*done'
        else:
            ret = process.run("service libvirt-guests restart | \
                              awk '{ print strftime(\"%b %y %H:%M:%S\"), \
                              $0; fflush(); }'", shell=True)
            pattern = r'(.+ \d\d:\d\d:\d\d)+ Resuming guest.+done'

        # libvirt-guests status command read messages from systemd
        # journal, in cases of messages are not ready in time,
        # add a time wait here.
        def wait_func():
            return libvirt_guests.raw_status().stdout.count("Resuming guest")

        utils_misc.wait_for(wait_func, 5)
        if is_systemd:
            ret = libvirt_guests.raw_status()
        logging.info("status output: %s", ret.stdout_text)
        resume_time = re.findall(pattern, ret.stdout_text, re.M)
        if not resume_time:
            test.fail("Can't see messages of resuming guest")

        # Convert time string to int
        resume_seconds = [time.mktime(time.strptime(
            tm, "%b %y %H:%M:%S")) for tm in resume_time]
        logging.info("Resume time in seconds: %s", resume_seconds)
        # Check if start_delay take effect
        for i in range(len(resume_seconds)-1):
            if resume_seconds[i+1] - resume_seconds[i] < int(start_delay):
                test.fail("Checking start_delay failed")

    def wait_for_state(vm_state):
        """
        Wait for vm state is ready.
        """
        utils_misc.wait_for(lambda: vm.state() == vm_state, 10)

    def check_guest_flags(bash_cmd, flags):
        """
        Check bypass_cache option for single guest.
        """
        # Drop caches.
        drop_caches()
        # form proper parallel command based on if systemd is used or not
        is_systemd = process.run("cat /proc/1/comm", shell=True).stdout_text.count("systemd")
        if is_systemd:
            virsh_cmd_stop = "systemctl stop libvirt-guests"
            virsh_cmd_start = "systemctl start libvirt-guests"
        else:
            virsh_cmd_stop = "service libvirt-guests stop"
            virsh_cmd_start = "service libvirt-guests start"

        ret = check_flags_parallel(virsh_cmd_stop, bash_cmd %
                                   (managed_save_file, managed_save_file,
                                    "1"), flags)
        if is_systemd:
            ret = libvirt_guests.raw_status()
        logging.info("status output: %s", ret.stdout_text)
        if all(["Suspending %s" % vm_name not in ret.stdout_text,
                "stopped, with saved guests" not in ret.stdout_text]):
            test.fail("Can't see messages of suspending vm")
        # status command should return 3.
        if not is_systemd:
            ret = libvirt_guests.raw_status()
        if ret.exit_status != 3:
            test.fail("The exit code %s for libvirt-guests"
                      " status is not correct" % ret)

        # Wait for VM in shut off state
        wait_for_state("shut off")
        check_flags_parallel(virsh_cmd_start, bash_cmd %
                             (managed_save_file, managed_save_file,
                              "0"), flags)
        # Wait for VM in running state
        wait_for_state("running")

    def vm_msave_remove_check(vm_name):
        """
        Check managed save remove command.
        """
        if not os.path.exists(managed_save_file) and case not in ['not_saved_without_file', 'saved_without_file']:
            test.fail("Can't find managed save image")
        ret = virsh.managedsave_remove(vm_name, debug=True)
        libvirt.check_exit_status(ret, msave_rm_error)
        if os.path.exists(managed_save_file):
            test.fail("Managed save image still exists")
        virsh.start(vm_name, debug=True)
        # The domain state should be running
        if vm.state() != "running":
            test.fail("Guest state should be"
                      " running after started")

    def vm_managedsave_loop(vm_name, loop_range, libvirtd):
        """
        Run a loop of managedsave command and check its result.
        """
        if vm.is_dead():
            virsh.start(vm_name, debug=True)
        for i in range(int(loop_range)):
            logging.debug("Test loop: %s" % i)
            virsh.managedsave(vm_name, debug=True)
            virsh.start(vm_name, debug=True)
        # Check libvirtd status.
        if not libvirtd.is_running():
            test.fail("libvirtd is stopped after cmd")
        # Check vm status.
        if vm.state() != "running":
            test.fail("Guest isn't in running state")

    def build_vm_xml(vm_name, **dargs):
        """
        Build the new domain xml and define it.
        """
        try:
            # stop vm before doing any change to xml
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            if dargs.get("cpu_mode"):
                if "cpu" in vmxml:
                    del vmxml.cpu
                cpuxml = vm_xml.VMCPUXML()
                cpuxml.mode = params.get("cpu_mode", "host-model")
                cpuxml.match = params.get("cpu_match", "exact")
                cpuxml.fallback = params.get("cpu_fallback", "forbid")
                cpu_topology = {}
                cpu_topology_sockets = params.get("cpu_topology_sockets")
                if cpu_topology_sockets:
                    cpu_topology["sockets"] = cpu_topology_sockets
                cpu_topology_cores = params.get("cpu_topology_cores")
                if cpu_topology_cores:
                    cpu_topology["cores"] = cpu_topology_cores
                cpu_topology_threads = params.get("cpu_topology_threads")
                if cpu_topology_threads:
                    cpu_topology["threads"] = cpu_topology_threads
                if cpu_topology:
                    cpuxml.topology = cpu_topology
                vmxml.cpu = cpuxml
                vmxml.vcpu = int(params.get("vcpu_nums"))
            if dargs.get("sec_driver"):
                seclabel_dict = {"type": "dynamic", "model": "selinux",
                                 "relabel": "yes"}
                vmxml.set_seclabel([seclabel_dict])

            vmxml.sync()
            vm.start()
        except Exception as e:
            logging.error(str(e))
            test.cancel("Build domain xml failed")

    status_error = ("yes" == params.get("status_error", "no"))
    vm_ref = params.get("managedsave_vm_ref", "name")
    libvirtd_state = params.get("libvirtd", "on")
    extra_param = params.get("managedsave_extra_param", "")
    progress = ("yes" == params.get("managedsave_progress", "no"))
    cpu_mode = "yes" == params.get("managedsave_cpumode", "no")
    test_undefine = "yes" == params.get("managedsave_undefine", "no")
    test_bypass_cache = "yes" == params.get("test_bypass_cache", "no")
    autostart_bypass_cache = params.get("autostart_bypass_cache", "")
    multi_guests = params.get("multi_guests", "")
    test_libvirt_guests = params.get("test_libvirt_guests", "")
    check_flags = "yes" == params.get("check_flags", "no")
    security_driver = params.get("security_driver", "")
    remove_after_cmd = "yes" == params.get("remove_after_cmd", "no")
    option = params.get("managedsave_option", "")
    check_shutdown = "yes" == params.get("shutdown_after_cmd", "no")
    pre_vm_state = params.get("pre_vm_state", "")
    move_saved_file = "yes" == params.get("move_saved_file", "no")
    test_loop_cmd = "yes" == params.get("test_loop_cmd", "no")
    remove_test = 'yes' == params.get('remove_test', 'no')
    case = params.get('case', '')
    msave_rm_error = "yes" == params.get("msave_rm_error", "no")
    if option:
        if not virsh.has_command_help_match('managedsave', option):
            # Older libvirt does not have this option
            test.cancel("Older libvirt does not"
                        " handle arguments consistently")

    # Backup xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Get the libvirtd service
    libvirtd = utils_libvirtd.Libvirtd()
    # Get config files.
    qemu_config = utils_config.LibvirtQemuConfig()
    libvirt_guests_config = utils_config.LibvirtGuestsConfig()
    # Get libvirt-guests service
    libvirt_guests = Factory.create_service("libvirt-guests")

    try:
        # Destroy vm first for setting configuration file
        if vm.state() == "running":
            vm.destroy(gracefully=False)
        # Prepare test environment.
        if libvirtd_state == "off":
            libvirtd.stop()
        if autostart_bypass_cache:
            ret = virsh.autostart(vm_name, "", ignore_status=True, debug=True)
            libvirt.check_exit_status(ret)
            qemu_config.auto_start_bypass_cache = autostart_bypass_cache
            libvirtd.restart()
        if security_driver:
            qemu_config.security_driver = [security_driver]
        if test_libvirt_guests:
            if multi_guests:
                start_delay = params.get("start_delay", "20")
                libvirt_guests_config.START_DELAY = start_delay
            if check_flags:
                libvirt_guests_config.BYPASS_CACHE = "1"
            # The config file format should be "x=y" instead of "x = y"
            process.run("sed -i -e 's/ = /=/g' "
                        "/etc/sysconfig/libvirt-guests",
                        shell=True)
            libvirt_guests.restart()

        # Change domain xml.
        if cpu_mode:
            build_vm_xml(vm_name, cpu_mode=True)
        if security_driver:
            build_vm_xml(vm_name, sec_driver=True)

        # Turn VM into certain state.
        if pre_vm_state == "transient":
            logging.info("Creating %s..." % vm_name)
            vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            # Wait for VM to be in shut off state
            utils_misc.wait_for(lambda: vm.state() == "shut off", 10)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, ignore_status=True,
                            debug=True).exit_status:
                vmxml_backup.define()
                test.cancel("Cann't create the domain")

        # Wait for vm in stable state
        if params.get("start_vm") == "yes":
            if vm.state() == "shut off":
                vm.start()
                vm.wait_for_login()

        # run test case
        domid = vm.get_id()
        domuuid = vm.get_uuid()
        if vm_ref == "id":
            vm_ref = domid
        elif vm_ref == "uuid":
            vm_ref = domuuid
        elif vm_ref == "hex_id":
            vm_ref = hex(int(domid))
        elif vm_ref.count("invalid"):
            vm_ref = params.get(vm_ref)
        elif vm_ref == "name":
            vm_ref = vm_name

        # Ignore exception with "ignore_status=True"
        if progress:
            option += " --verbose"
        option += extra_param

        # For bypass_cache test. Run a shell command to check fd flags while
        # excuting managedsave command
        software_mgr = software_manager.SoftwareManager()
        if not software_mgr.check_installed('lsof'):
            logging.info('Installing lsof package:')
            software_mgr.install('lsof')
        bash_cmd = ("let i=1; while((i++<400)); do if [ -e %s ]; then (cat /proc"
                    "/$(lsof -w %s|awk '/libvirt_i/{print $2}')/fdinfo/%s |"
                    "grep 'flags:.*') && break; else sleep 0.05; fi; done;")
        # Flags to check bypass cache take effect
        flags = os.O_DIRECT
        if test_bypass_cache:
            # Drop caches.
            drop_caches()
            virsh_cmd = "virsh managedsave %s %s" % (option, vm_name)
            check_flags_parallel(virsh_cmd, bash_cmd %
                                 (managed_save_file, managed_save_file,
                                  "1"), flags)
            # Wait for VM in shut off state
            wait_for_state("shut off")
            virsh_cmd = "virsh start %s %s" % (option, vm_name)
            check_flags_parallel(virsh_cmd, bash_cmd %
                                 (managed_save_file, managed_save_file,
                                  "0"), flags)
            # Wait for VM in running state
            wait_for_state("running")
        elif test_libvirt_guests:
            logging.debug("libvirt-guests status: %s", libvirt_guests.status())
            if multi_guests:
                check_multi_guests(multi_guests,
                                   start_delay, libvirt_guests)

            if check_flags:
                check_guest_flags(bash_cmd, flags)
        elif remove_test:
            if case == 'not_saved_with_file':
                process.run('touch %s' % managed_save_file, shell=True, verbose=True)
            vm_msave_remove_check(vm_name)

        else:
            # Ensure VM is running
            utils_misc.wait_for(lambda: vm.state() == "running", 10)
            ret = virsh.managedsave(vm_ref, options=option,
                                    ignore_status=True, debug=True)
            status = ret.exit_status
            # The progress information outputed in error message
            error_msg = ret.stderr.strip()
            if move_saved_file:
                cmd = "echo > %s" % managed_save_file
                process.run(cmd, shell=True)
            if case == 'saved_without_file':
                os.remove(managed_save_file)

            # recover libvirtd service start
            if libvirtd_state == "off":
                libvirtd.start()

            if status_error:
                if not status:
                    if libvirtd_state == "off" and libvirt_version.version_compare(5, 6, 0):
                        logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                                     "and command should succeed")
                    else:
                        test.fail("Run successfully with wrong command!")
            else:
                if status:
                    test.fail("Run failed with right command")
                if progress:
                    if not error_msg.count("Managedsave:"):
                        test.fail("Got invalid progress output")
                if remove_after_cmd or case == 'saved_without_file':
                    vm_msave_remove_check(vm_name)
                elif test_undefine:
                    vm_undefine_check(vm_name)
                elif autostart_bypass_cache:
                    # rhbz#1755303
                    if libvirt_version.version_compare(5, 6, 0):
                        os.remove("/run/libvirt/qemu/autostarted")
                    libvirtd.stop()
                    virsh_cmd = ("(service libvirtd start)")
                    check_flags_parallel(virsh_cmd, bash_cmd %
                                         (managed_save_file, managed_save_file,
                                          "0"), flags)
                elif test_loop_cmd:
                    loop_range = params.get("loop_range", "20")
                    vm_managedsave_loop(vm_name, loop_range, libvirtd)
                else:
                    vm_recover_check(option, libvirtd, check_shutdown)
    finally:
        # Restore test environment.
        # Restart libvirtd.service
        qemu_config.restore()
        libvirt_guests_config.restore()
        libvirtd.restart()
        if autostart_bypass_cache:
            virsh.autostart(vm_name, "--disable",
                            ignore_status=True, debug=True)
        vm.destroy(gracefully=False)
        virsh.managedsave_remove(vm_name, debug=True)
        vmxml_backup.sync()
        if multi_guests:
            for i in range(int(multi_guests)):
                virsh.remove_domain("%s_%s" % (vm_name, i),
                                    "--remove-all-storage",
                                    debug=True)
