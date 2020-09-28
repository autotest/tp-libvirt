import os
import shutil
import logging
import platform
import time

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import utils_misc
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test interafce xml options.

    1.Prepare test environment, destroy or suspend a VM.
    2.Perform test operation.
    3.Recover test environment.
    4.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    if (vm_name != "lxc_test_vm1"):
        vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': False}
    hook_file = params.get("hook_file", "/etc/libvirt/hooks/qemu")
    hook_log = params.get("hook_log", "/tmp/qemu.log")
    machine_type = params.get("machine_type", "")

    def prepare_hook_file(hook_op):
        """
        Create hook file.
        """
        logging.info("hook script: %s", hook_op)
        hook_lines = hook_op.split(';')
        hook_dir = os.path.dirname(hook_file)
        logging.info("hook script: %s", hook_op)
        if not os.path.exists(hook_dir):
            os.mkdir(hook_dir)
        with open(hook_file, 'w') as hf:
            hf.write('\n'.join(hook_lines))
        os.chmod(hook_file, 0o755)

        # restart libvirtd
        libvirtd.restart()

    def check_hooks(opt):
        """
        Check hook operations in log file
        """
        logging.debug("Trying to check the string '%s'"
                      " in logfile", opt)
        if not os.path.exists(hook_log):
            logging.debug("Log file doesn't exist")
            return False

        logs = None
        with open(hook_log, 'r') as lf:
            logs = lf.read()
        if not logs:
            return False

        logging.debug("Read from hook log file: %s", logs)
        if opt in logs:
            return True
        else:
            return False

    def start_stop_hook():
        """
        Do start/stop operation and check the results.
        """
        logging.info("Try to test start/stop hooks...")
        hook_para = "%s %s" % (hook_file, vm_name)
        prepare_hook_file(hook_script %
                          (vm_name, hook_log))
        vm.start()
        vm.wait_for_login().close()
        try:
            hook_str = hook_para + " prepare begin -"
            assert check_hooks(hook_str)
            hook_str = hook_para + " start begin -"
            assert check_hooks(hook_str)
            hook_str = hook_para + " started begin -"
            assert check_hooks(hook_str)
            # stop the vm
            vm.destroy()
            hook_str = hook_para + " stopped end -"
            assert check_hooks(hook_str)
            hook_str = hook_para + " release end -"
            assert check_hooks(hook_str)
        except AssertionError:
            utils_misc.log_last_traceback()
            test.fail("Failed to check start/stop hooks.")

    def save_restore_hook():
        """
        Do save/restore operation and check the results.
        """
        hook_para = "%s %s" % (hook_file, vm_name)
        save_file = os.path.join(data_dir.get_tmp_dir(),
                                 "%s.save" % vm_name)
        disk_src = vm.get_first_disk_devices()['source']
        if domainxml_test:
            disk_dist = "/tmp/%s.move" % vm_name
            shutil.copy(disk_src, disk_dist)
            script = (hook_script %
                      (vm_name, disk_src, disk_dist))
            prepare_hook_file(script)
        elif basic_test:
            prepare_hook_file(hook_script %
                              (vm_name, hook_log))
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if domainxml_test:
            disk_src_save = vm.get_first_disk_devices()['source']
            if disk_src != disk_src_save:
                test.fail("Failed to check hooks for save operation")
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if os.path.exists(save_file):
            os.remove(save_file)
        if domainxml_test:
            disk_src_restore = vm.get_first_disk_devices()['source']
            if disk_dist != disk_src_restore:
                test.fail("Failed to check hooks for restore operation")
            vm.destroy()
            if os.path.exists(disk_dist):
                os.remove(disk_dist)
            vmxml_backup.sync()
        if basic_test:
            hook_str = hook_para + " restore begin -"
            if not check_hooks(hook_str):
                test.fail("Failed to check restore hooks.")

    def managedsave_hook():
        """
        Do managedsave operation and check the results.
        """
        hook_para = "%s %s" % (hook_file, vm_name)
        save_file = os.path.join(data_dir.get_tmp_dir(),
                                 "%s.save" % vm_name)
        disk_src = vm.get_first_disk_devices()['source']
        if domainxml_test:
            disk_dist = "/tmp/%s.move" % vm_name
            shutil.copy(disk_src, disk_dist)
            script = (hook_script %
                      (vm_name, disk_src, disk_dist))
            prepare_hook_file(script)
        elif basic_test:
            prepare_hook_file(hook_script %
                              (vm_name, hook_log))
        ret = virsh.managedsave(vm_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if domainxml_test:
            disk_src_save = vm.get_first_disk_devices()['source']
            if disk_src != disk_src_save:
                test.fail("Failed to check hooks for"
                          " managedsave operation")
        vm.start()
        if os.path.exists(save_file):
            os.remove(save_file)
        if domainxml_test:
            disk_src_restore = vm.get_first_disk_devices()['source']
            if disk_dist != disk_src_restore:
                test.fail("Failed to check hooks for"
                          " managedsave operation")
            vm.destroy()
            if os.path.exists(disk_dist):
                os.remove(disk_dist)
            vmxml_backup.sync()

        if basic_test:
            hook_str = hook_para + " restore begin -"
            if not check_hooks(hook_str):
                test.fail("Failed to check managedsave hooks.")

    def libvirtd_hook():
        """
        Check the libvirtd hooks.
        """
        prepare_hook_file(hook_script % (vm_name, hook_log))
        hook_para = "%s %s" % (hook_file, vm_name)
        time.sleep(2)
        libvirtd.restart()
        try:
            hook_str = hook_para + " reconnect begin -"
            assert check_hooks(hook_str)
        except AssertionError:
            utils_misc.log_last_traceback()
            test.fail("Failed to check libvirtd hooks")

    def lxc_hook():
        """
        Check the lxc hooks.
        """

        if platform.platform().count('el8'):
            test.cancel("lxc is not supported in rhel8")
        test_xml = vm_xml.VMXML("lxc")

        root_dir = data_dir.get_root_dir()
        lxc_xml_related_path_file = params.get("lxc_xml_file")
        lxc_xml_path_file = os.path.join(root_dir, lxc_xml_related_path_file)
        with open(lxc_xml_path_file, 'r') as fd:
            test_xml.xml = fd.read()

        uri = "lxc:///"
        vm_name = "lxc_test_vm1"
        hook_para = "%s %s" % (hook_file, vm_name)
        prepare_hook_file(hook_script % hook_log)
        exit1 = params.get("exit1", "no")
        output = virsh.create(test_xml.xml, options="--console", uri=uri)

        if output.exit_status:
            logging.debug("output.stderr1: %s", output.stderr.lower())
            if (exit1 == "yes" and "hook script execution failed" in output.stderr.lower()):
                return True
            else:
                test.fail("Create %s domain failed:%s" %
                          ("lxc", output.stderr))
        logging.info("Domain %s created, will check with console", vm_name)

        hook_str = hook_para + " prepare begin -"
        if not check_hooks(hook_str):
            test.fail("Failed to check lxc hook string: %s" % hook_str)
        hook_str = hook_para + " start begin -"
        if not check_hooks(hook_str):
            test.fail("Failed to check lxc hook string: %s" % hook_str)

        virsh.destroy(vm_name, options="", uri=uri)

        hook_str = hook_para + " stopped end -"
        if not check_hooks(hook_str):
            test.fail("Failed to check lxc hook string: %s" % hook_str)
        hook_str = hook_para + " release end -"
        if not check_hooks(hook_str):
            test.fail("Failed to check lxc hook string: %s" % hook_str)

    def daemon_hook():
        """
        Check the libvirtd hooks.
        """
        # stop daemon first
        libvirtd.stop()
        prepare_hook_file(hook_script % hook_log)
        try:
            libvirtd.start()
            hook_str = hook_file + " - start - start"
            assert check_hooks(hook_str)
            # Restart libvirtd and test again
            if os.path.exists(hook_log):
                os.remove(hook_log)
            libvirtd.restart()
            hook_str = hook_file + " - shutdown - shutdown"
            assert check_hooks(hook_str)
            hook_str = hook_file + " - start - start"
            assert check_hooks(hook_str)

            # kill the daemon with SIGHUP
            if os.path.exists(hook_log):
                os.remove(hook_log)
            utils_misc.signal_program('libvirtd', 1,
                                      '/var/run')
            hook_str = hook_file + " - reload begin SIGHUP"
            assert check_hooks(hook_str)

        except AssertionError:
            utils_misc.log_last_traceback()
            test.fail("Failed to check daemon hooks")

    def attach_hook():
        """
        Check attach hooks.
        """
        # Start a domain with qemu command.
        disk_src = vm.get_first_disk_devices()['source']
        vm_test = "foo"
        prepare_hook_file(hook_script %
                          (vm_test, hook_log))
        qemu_bin = params.get("qemu_bin", "/usr/libexec/qemu-kvm")
        if "ppc" in platform.machine():
            qemu_cmd = ("%s -machine pseries"
                        " -drive file=%s,if=none,bus=0,unit=1"
                        " -monitor unix:/tmp/demo,"
                        "server,nowait -name %s" %
                        (qemu_bin, disk_src, vm_test))
        else:
            qemu_cmd = ("%s -drive file=%s,if=none,bus=0,unit=1"
                        " -monitor unix:/tmp/demo,"
                        "server,nowait -name %s" %
                        (qemu_bin, disk_src, vm_test))
        # After changed above command, qemu-attach failed
        os.system('%s &' % qemu_cmd)
        sta, pid = process.getstatusoutput("pgrep qemu-kvm")
        if not pid:
            test.fail("Cannot get pid of qemu command")
        try:
            ret = virsh.qemu_attach(pid, **virsh_dargs)
            if ret.exit_status:
                utils_misc.kill_process_tree(pid)
                test.fail("Cannot attach qemu process")
            else:
                virsh.destroy(vm_test)
        except Exception as detail:
            utils_misc.kill_process_tree(pid)
            test.fail("Failed to attach qemu process: %s" % str(detail))
        hook_str = hook_file + " " + vm_test + " attach begin -"
        if not check_hooks(hook_str):
            test.fail("Failed to check attach hooks")

    def edit_iface(net_name):
        """
        Edit interface options for vm.
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        iface_xml = vmxml.get_devices(device_type="interface")[0]
        vmxml.del_device(iface_xml)
        iface_xml.type_name = "network"
        iface_xml.source = {"network": net_name}
        del iface_xml.address
        vmxml.add_device(iface_xml)
        vmxml.sync()

    def network_hook():
        """
        Check network hooks.
        """
        # Set interface to use default network
        net_name = params.get("net_name", "default")
        edit_iface(net_name)
        prepare_hook_file(hook_script %
                          (net_name, hook_log))
        try:
            # destroy the network
            ret = virsh.net_destroy(net_name, **virsh_dargs)
            libvirt.check_exit_status(ret)
            hook_str = hook_file + " " + net_name + " stopped end -"
            assert check_hooks(hook_str)

            # start network
            ret = virsh.net_start(net_name, **virsh_dargs)
            libvirt.check_exit_status(ret)
            hook_str = hook_file + " " + net_name + " start begin -"
            assert check_hooks(hook_str)
            hook_str = hook_file + " " + net_name + " started begin -"
            assert check_hooks(hook_str)
            if vm.is_alive():
                vm.destroy(gracefully=False)

            # Remove all controllers, interfaces and addresses in vm dumpxml
            vm_inactive_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vm_inactive_xml.remove_all_device_by_type('controller')
            type_dict = {'address': '/devices/*/address'}
            try:
                for elem in vm_inactive_xml.xmltreefile.findall(type_dict['address']):
                    vm_inactive_xml.xmltreefile.remove(elem)
            except (AttributeError, TypeError) as details:
                test.fail("Fail to remove address.")
            vm_inactive_xml.xmltreefile.write()
            machine_list = vm_inactive_xml.os.machine.split("-")

            # Modify machine type according to the requirements and Add controllers to VM according to machine type

            def generate_controller(controller_dict):
                controller_xml = Controller("controller")
                controller_xml.model = controller_dict['model']
                controller_xml.type = controller_dict['type']
                controller_xml.index = controller_dict['index']
                return controller_xml

            if machine_type == 'pc':
                vm_inactive_xml.set_os_attrs(**{"machine": machine_list[0] + "-i440fx-" + machine_list[2]})
                pc_Dict0 = {'model': 'pci-root', 'type': 'pci', 'index': 0}
                pc_Dict1 = {'model': 'pci-bridge', 'type': 'pci', 'index': 1}
                vm_inactive_xml.add_device(generate_controller(pc_Dict0))
                vm_inactive_xml.add_device(generate_controller(pc_Dict1))
            elif machine_type == 'q35':
                vm_inactive_xml.set_os_attrs(**{"machine": machine_list[0] + "-q35-" + machine_list[2]})
                q35_Dict0 = {'model': 'pcie-root', 'type': 'pci', 'index': 0}
                q35_Dict1 = {'model': 'pcie-root-port', 'type': 'pci', 'index': 1}
                q35_Dict2 = {'model': 'pcie-to-pci-bridge', 'type': 'pci', 'index': 2}
                vm_inactive_xml.add_device(generate_controller(q35_Dict0))
                vm_inactive_xml.add_device(generate_controller(q35_Dict1))
                vm_inactive_xml.add_device(generate_controller(q35_Dict2))
            vm_inactive_xml.sync()

            # Plug a interface and Unplug the interface
            vm.start()
            vm.wait_for_login().close()
            interface_num = len(vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices("interface"))
            mac_addr = "52:54:00:9a:53:a9"
            logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))

            def is_attached_interface():
                return len(vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices("interface")) == interface_num + 1

            ret = virsh.attach_interface(vm_name,
                                         ("network %s --mac %s" % (net_name, mac_addr)))
            libvirt.check_exit_status(ret)
            if utils_misc.wait_for(is_attached_interface, timeout=20) is not True:
                test.fail("Attaching interface failed.")
            if libvirt_version.version_compare(6, 0, 0):
                hook_str = hook_file + " " + net_name + " port-created begin -"
            else:
                hook_str = hook_file + " " + net_name + " plugged begin -"
            assert check_hooks(hook_str)

            def is_detached_interface():
                return len(vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices("interface")) == interface_num

            ret = virsh.detach_interface(vm_name, "network --mac %s" % mac_addr)
            libvirt.check_exit_status(ret)
            utils_misc.wait_for(is_detached_interface, timeout=50)
            # Wait for timeout and if not succeeded, detach again (during testing, detaching interface failed from q35 VM for the first time when using this function)
            if len(vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices("interface")) != interface_num:
                ret = virsh.detach_interface(vm_name, "network --mac %s" % mac_addr)
                libvirt.check_exit_status(ret)
            if utils_misc.wait_for(is_detached_interface, timeout=50) is not True:
                test.fail("Detaching interface failed.")
            if libvirt_version.version_compare(6, 0, 0):
                hook_str = hook_file + " " + net_name + " port-deleted begin -"
            else:
                hook_str = hook_file + " " + net_name + " unplugged begin -"
            assert check_hooks(hook_str)
            # remove the log file
            if os.path.exists(hook_log):
                os.remove(hook_log)
            # destroy the domain
            vm.destroy()
            if libvirt_version.version_compare(6, 0, 0):
                hook_str = hook_file + " " + net_name + " port-deleted begin -"
            else:
                hook_str = hook_file + " " + net_name + " unplugged begin -"
            assert check_hooks(hook_str)
        except AssertionError:
            utils_misc.log_last_traceback()
            test.fail("Failed to check network hooks")

    def run_scale_test():
        """
        Try to start and stop domain many times.
        """
        prepare_hook_file(hook_script)
        loop_num = int(params.get("loop_num", 30))
        loop_timeout = int(params.get("loop_timeout", 600))
        cmd1 = ("for i in {1..%s};do echo $i 'start guest -';"
                "virsh start %s;sleep 1;echo $i 'stop guest -';"
                "virsh destroy %s;sleep 1;done;"
                % (loop_num, vm_name, vm_name))
        cmd2 = ("for i in {1..%s};do virsh list;sleep 1;done;"
                % loop_num * 2)
        utils_misc.run_parallel([cmd1, cmd2], timeout=loop_timeout)

    start_error = "yes" == params.get("start_error", "no")
    test_start_stop = "yes" == params.get("test_start_stop", "no")
    test_lxc = "yes" == params.get("test_lxc", "no")
    test_attach = "yes" == params.get("test_attach", "no")
    test_libvirtd = "yes" == params.get("test_libvirtd", "no")
    test_managedsave = "yes" == params.get("test_managedsave", "no")
    test_saverestore = "yes" == params.get("test_saverestore", "no")
    test_daemon = "yes" == params.get("test_daemon", "no")
    test_network = "yes" == params.get("test_network", "no")
    if not test_lxc:
        basic_test = "yes" == params.get("basic_test", "yes")
        scale_test = "yes" == params.get("scale_test", "yes")
    else:
        basic_test = "no" == params.get("basic_test", "yes")
        scale_test = "no" == params.get("scale_test", "yes")
    domainxml_test = "yes" == params.get("domainxml_test", "no")

    # The hook script is provided from config
    hook_script = params.get("hook_script")

    # Destroy VM first
    if vm_name != "lxc_test_vm1" and vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    if vm_name != "lxc_test_vm1":
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        try:
            if test_start_stop:
                start_stop_hook()
            elif test_attach:
                attach_hook()
            elif start_error:
                prepare_hook_file(hook_script %
                                  (vm_name, hook_log))
            elif test_daemon:
                daemon_hook()
            elif test_network:
                network_hook()
            elif scale_test:
                run_scale_test()
            # Start the domain
            if vm_name != "lxc_test_vm1" and vm.is_dead():
                vm.start()
                vm.wait_for_login().close()
            if test_libvirtd:
                libvirtd_hook()
            elif test_saverestore:
                save_restore_hook()
            elif test_managedsave:
                managedsave_hook()
            if test_lxc:
                lxc_hook()

        except virt_vm.VMStartError as e:
            logging.info(str(e))
            if start_error:
                pass
            else:
                test.fail('VM Failed to start for some reason!')
        else:
            if start_error:
                test.fail('VM started unexpected')

    finally:
        # Recover VM.
        logging.info("Restoring vm...")
        if test_managedsave:
            virsh.managedsave_remove(vm_name)
        if vm_name != "lxc_test_vm1" and vm.is_alive():
            vm.destroy(gracefully=False)
        if os.path.exists(hook_file):
            os.remove(hook_file)
        if os.path.exists(hook_log):
            os.remove(hook_log)
        libvirtd.restart()
        if vm_name != "lxc_test_vm1":
            vmxml_backup.sync()
