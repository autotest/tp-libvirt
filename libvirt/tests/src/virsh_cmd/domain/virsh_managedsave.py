import os
import re
import logging
import shutil
from autotest.client import utils
from autotest.client.shared import error
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_config
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


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

    # define function
    def vm_recover_check(guest_name, option, libvirtd):
        """
        Check if the vm can be recovered correctly.

        :param guest_name : Checked vm's name.
        :param option : managedsave command option.
        """
        # This time vm not be shut down
        if vm.is_alive():
            raise error.TestFail("Guest should be inactive")
        # Check vm managed save state.
        ret = virsh.dom_list("--managed-save --inactive")
        vm_state1 = re.findall(r".*%s.*" % guest_name,
                               ret.stdout.strip())[0].split()[2]
        ret = virsh.dom_list("--managed-save --all")
        vm_state2 = re.findall(r".*%s.*" % guest_name,
                               ret.stdout.strip())[0].split()[2]
        if vm_state1 != "saved" or vm_state2 != "saved":
            raise error.TestFail("Guest state should be saved")

        virsh.start(guest_name)
        # This time vm should be in the list
        if vm.is_dead():
            raise error.TestFail("Guest should be active")
        # Restart libvirtd and check vm status again.
        libvirtd.restart()
        if vm.is_dead():
            raise error.TestFail("Guest should be active after"
                                 " restarting libvirtd")
        if option:
            if option.count("running"):
                if vm.is_dead() or vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " running after started"
                                         " because of '--running' option")
            elif option.count("paused"):
                if not vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " paused after started"
                                         " because of '--paused' option")
        else:
            if params.get("paused_after_start_vm") == "yes":
                if not vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " paused after started"
                                         " because of initia guest state")

    def vm_undefine_check(vm_name):
        """
        Check if vm can be undefined with manage-save option
        """
        #backup xml file
        xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if not os.path.exists(managed_save_file):
            raise error.TestFail("Can't find managed save image")
        #undefine domain with no options.
        if not virsh.undefine(vm_name, options=None,
                              ignore_status=True).exit_status:
            xml_backup.define()
            raise error.TestFail("Guest shouldn't be undefined"
                                 "while domain managed save image exists")
        #undefine domain with managed-save option.
        if virsh.undefine(vm_name, options="--managed-save",
                          ignore_status=True).exit_status:
            raise error.TestFail("Guest can't be undefine with "
                                 "managed-save option")

        if os.path.exists(managed_save_file):
            raise error.TestFail("Managed save image exists after undefining vm")
        #restore and start the vm.
        xml_backup.define()
        vm.start()

    def vm_autostart_bypass_check(vm_name):
        """
        Check if autostart bypass cache take effect.
        """
        # The third place of fdinfo flags is '4', that means bypass-cache works
        # check all flags info '014000' here
        cmd = ("service libvirtd stop; service libvirtd start; (while true;"
               " do [ -e %s ]; if [ $? -eq 0 ];then (cat /proc/`lsof -w %s |"
               " awk '/libvirt_i/{print $2}'`/fdinfo/*0* | grep 'flags:.*014000'"
               " && break); else break; fi;  done;) & sleep 2;virsh start %s"
               % (managed_save_file, managed_save_file, vm_name))
        output = utils.run(cmd, ignore_status=True).stdout.strip()
        logging.debug("output: %s" % output)
        lines = re.findall(r"^flags:.+014000", output, re.M)
        if not lines:
            raise error.TestFail("Check autostart bypass cache failed")

    def vm_msave_remove_check(vm_name):
        """
        Check managed save remove command.
        """
        if not os.path.exists(managed_save_file):
            raise error.TestFail("Can't find managed save image")
        virsh.managedsave_remove(vm_name)
        if os.path.exists(managed_save_file):
            raise error.TestFail("Managed save image still exists")
        virsh.start(vm_name)
        # The domain state should be running
        if vm.state() != "running":
            raise error.TestFail("Guest state should be"
                                 " running after started")

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
            if dargs.get("sec_driver"):
                seclabel_dict = {"type": "dynamic", "model": "selinux",
                                 "relabel": "yes"}
                vmxml.set_seclabel(seclabel_dict)

            vmxml.sync()
            vm.start()
        except Exception, e:
            logging.error(str(e))
            raise error.TestNAError("Build domain xml failed")

    domid = vm.get_id()
    domuuid = vm.get_uuid()
    status_error = ("yes" == params.get("status_error", "no"))
    vm_ref = params.get("managedsave_vm_ref", "name")
    libvirtd_state = params.get("libvirtd", "on")
    extra_param = params.get("managedsave_extra_param", "")
    progress = ("yes" == params.get("managedsave_progress", "no"))
    cpu_mode = "yes" == params.get("managedsave_cpumode", "no")
    test_undefine = "yes" == params.get("managedsave_undefine", "no")
    auto_start_bypass_cache = params.get("autostart_bypass_cache", "")
    security_driver = params.get("security_driver", "")
    remove_after_cmd = "yes" == params.get("remove_after_cmd", "no")
    option = params.get("managedsave_option", "")
    if option:
        if not virsh.has_command_help_match('managedsave', option):
            # Older libvirt does not have this option
            raise error.TestNAError("Older libvirt does not"
                                    " handle arguments consistently")

    # run test case
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

    # Backup xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # stop the libvirtd service
    libvirtd = utils_libvirtd.Libvirtd()
    # Get qemu config.
    config = utils_config.LibvirtQemuConfig()

    try:
        # Prepare test environment.
        if libvirtd_state == "off":
            libvirtd.stop()
        if auto_start_bypass_cache:
            ret = virsh.autostart(vm_name, "", ignore_status=True)
            libvirt.check_exit_status(ret)
            config.auto_start_bypass_cache = auto_start_bypass_cache
        if security_driver:
            config.security_driver = [security_driver]

        # Change domain xml.
        if cpu_mode:
            build_vm_xml(vm_name, cpu_mode=True)
        if security_driver:
            build_vm_xml(vm_name, sec_driver=True)

        # Ignore exception with "ignore_status=True"
        if progress:
            option += " --verbose"
        option += extra_param
        ret = virsh.managedsave(vm_ref, options=option, ignore_status=True)
        status = ret.exit_status
        # The progress information outputed in error message
        error_msg = ret.stderr.strip()

        # recover libvirtd service start
        if libvirtd_state == "off":
            libvirtd.start()

        if status_error:
            if not status:
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status:
                raise error.TestFail("Run failed with right command")
            if progress:
                if not error_msg.count("Managedsave:"):
                    raise error.TestFail("Got invalid progress output")
            if remove_after_cmd:
                vm_msave_remove_check(vm_name)
            elif test_undefine:
                vm_undefine_check(vm_name)
            elif auto_start_bypass_cache:
                # check if autostart bypass cache take effect.
                vm_autostart_bypass_check(vm_name)
            else:
                vm_recover_check(vm_name, option, libvirtd)
    finally:
        # Restore test environment.
        if auto_start_bypass_cache:
            virsh.autostart(vm_name, "--disable", ignore_status=True)
        if vm.is_paused():
            virsh.resume(vm_name)
        if vm.is_dead():
            vm.start()
        vmxml_backup.sync()
        config.restore()
        libvirtd.restart()
