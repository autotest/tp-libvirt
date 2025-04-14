import logging as log
import os
import shutil
import time
import threading

from glob import glob

from avocado.utils import process
from avocado.utils import path as utils_path

from virttest import virsh
from virttest import utils_test
from virttest import utils_misc
from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_test import libvirt_device_utils
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virtiofs filesystem device:

    1.Start guest with 1/2 virtiofs filesystem devices.
    2.Start 2 guest with the same virtiofs filesystem device.
    3.Coldplug/Coldunplug virtiofs filesystem device
    4.Share data between guests and host.
    5.Lifecycle for guest with virtiofs filesystem device.
    """

    def generate_expected_process_options():
        """
        Generate expected virtiofsd process option
        """
        expected_results = []
        if cache_mode != "auto":
            if cache_mode == "none":
                expected_results.append("cache(\s|=)(none|never)")
            else:
                expected_results.append("cache(\s|=)%s" % cache_mode)
        if xattr == "on":
            expected_results.append("(\s--|,)xattr")
        elif xattr == "off" and not libvirt_version.version_compare(10, 5, 0):
            expected_results.append(",no_xattr")
        if thread_pool_size:
            # Even through there is a equal mark between --thread-pool-size and
            # its value for libvirt format. But there is no equal mark in
            # virtiofsd man pages. Add another kind of pattern here to avoid
            # possible change in the future again.
            expected_results.append("--thread-pool-size(\s|=)%s" % thread_pool_size)
        if openfiles:
            expected_results.append(" --rlimit-nofile(\s|=)%s" % open_files_max)
        if sandbox_mode and sandbox_mode == "namespace":
            expected_results.append(" --sandbox(\s|=)%s" % sandbox_mode)
        logging.debug(f"Expected qemu cmdline pattern {expected_results}")
        return expected_results

    def shared_data(vm_names, fs_devs):
        """
        Shared data between guests and host:
        1.Mount dir in guest;
        2.Write a file in guest;
        3.Check the md5sum value are the same in guests and host;
        """
        md5s = []
        for vm in vms:
            guest_index = vms.index(vm)
            session = vm.wait_for_login()
            fs_indexes = _fs_dev_indexes(guest_index)
            for fs_index in fs_indexes:
                fs_dev = fs_devs[fs_index]
                logging.debug(fs_dev)
                mount_dir = '/var/tmp/' + fs_dev.target['dir']
                session.cmd('rm -rf %s' % mount_dir, ignore_all_errors=False)
                session.cmd('mkdir -p %s' % mount_dir)
                logging.debug("mount virtiofs dir in guest")
                cmd = "mount -t virtiofs %s %s" % (fs_dev.target['dir'], mount_dir)
                status, output = session.cmd_status_output(cmd, timeout=300)
                if status != 0:
                    session.close()
                    test.fail("mount virtiofs dir failed: %s" % output)
                filename_guest = mount_dir + '/' + vms[0].name
                if vm == vms[0]:
                    cmd = "dd if=/dev/urandom of=%s bs=1M count=512 oflag=direct" % filename_guest
                    status, output = session.cmd_status_output(cmd, timeout=300)
                    if status != 0:
                        session.close()
                        test.fail("Write data failed: %s" % output)
                session.cmd_status_output(f"sync -d {mount_dir}")
                md5_value = session.cmd_status_output("md5sum %s" % filename_guest,
                                                      timeout=300)[1].strip().split()[0]
                md5s.append(md5_value)
                logging.debug(md5_value)
                md5_value = process.run("md5sum %s" % filename_guest).stdout_text.strip().split()[0]
                logging.debug(md5_value)
                md5s.append(md5_value)
            session.close()
        if len(set(md5s)) != fs_num:
            test.fail("The md5sum value are not the same in guests and host")

    def launch_externally_virtiofs(source_dir, source_socket):
        """
        Launch externally virtiofs

        :param source_dir:  the dir shared on host
        :param source_socket: the socket file listened on
        """
        process.run('chcon -t virtd_exec_t %s' % path, ignore_status=False, shell=True)
        cmd = "systemd-run %s --socket-path=%s -o source=%s" % (path, source_socket, source_dir)
        try:
            process.run(cmd, ignore_status=False, shell=True)
            # Make sure the socket is created
            utils_misc.wait_for(lambda: os.path.isdir(source_socket), timeout=3)
            process.run("chown qemu:qemu %s" % source_socket, ignore_status=False)
            process.run('chcon -t svirt_image_t %s' % source_socket, ignore_status=False, shell=True)
        except Exception as err:
            cmd = "pkill virtiofsd"
            process.run(cmd, shell=True)
            test.fail("{}".format(err))

    def prepare_stress_script(script_path, script_content):
        """
        Refer to xfstest generic/531. Create stress test script to create a lot of unlinked files.

        :param source_path: The path of script
        :param content: The content of stress script
        """
        logging.debug("stress script path: %s content: %s" % (script_path, script_content))
        script_lines = script_content.split(';')
        try:
            with open(script_path, 'w') as fd:
                fd.write('\n'.join(script_lines))
            os.chmod(script_path, 0o777)
        except Exception as e:
            test.error("Prepare the guest stress script failed %s" % e)

    def run_stress_script(session, script_path):
        """
        Run stress script in the guest

        :param session: guest session
        :param script_path: The path of script in the guest
        """
        # Set ULIMIT_NOFILE to increase the number of unlinked files
        session.cmd("ulimit -n 500000 && /usr/bin/python3 %s" % script_path, timeout=120)

    def umount_fs(guest_index):
        """
        Unmount the filesystem in guest

        :param guest_index: the guest's index in vms
        """
        fs_indexes = _fs_dev_indexes(guest_index)
        vm = vms[guest_index]
        if vm.is_alive():
            session = vm.wait_for_login()
            if lifecycle_scenario == "reboot":
                session.cmd("sed -i '$d' /etc/fstab", ignore_all_errors=True)
            for index in fs_indexes:
                mount_dir = '/var/tmp/' + fs_devs[index].target['dir']
                session.cmd('umount -f %s' % mount_dir, ignore_all_errors=True)
                session.cmd('rm -rf %s' % mount_dir, ignore_all_errors=True)
            session.close()

    def check_detached_xml(vm):
        """
        Check whether there is xml about the filesystem device
        in the vm xml

        :param vm: the vm to be checked
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        filesystems = vmxml.devices.by_device_tag('filesystem')
        if filesystems:
            test.fail("There should be no filesystem devices in guest "
                      "xml after hotunplug")

    def check_filesystem_in_guest(vm, fs_dev):
        """
        Check whether there is virtiofs in vm

        :param vm: the vm to be checked
        :param fs_dev: the virtiofs device to be checked
        """
        session = vm.wait_for_login()
        mount_dir = '/var/tmp/' + fs_dev.target['dir']
        cmd = "mkdir %s; mount -t virtiofs %s %s" % (mount_dir, fs_dev.target['dir'], mount_dir)
        status, output = session.cmd_status_output(cmd, timeout=300)
        session.cmd('rm -rf %s' % mount_dir, ignore_all_errors=True)
        if not status:
            test.fail("Mount virtiofs should failed after hotunplug device. %s" % output)
        session.close()

    def check_filesystem_hotplug_with_mem_setup():
        """
        Check libvirt can not identify shared memory after restarting
        virtqemud.
        Bug 2078693
        """
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        fs_dict = eval(params.get('fs_dict', '{}'))
        source_dir = params.get('source_dir')
        dev_type = params.get('dev_type')
        vm_name = vm_names[-1]

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name)
        vmxml.setup_attrs(**vm_attrs)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        libvirtd = utils_libvirtd.Libvirtd()
        libvirtd.restart()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.remove_all_device_by_type(dev_type)
        vmxml.sync()
        vm = env.get_vm(vm_name)
        vm.start()
        vm.wait_for_login(timeout=120)
        os.mkdir(source_dir)

        fs = libvirt_vmxml.create_vm_device_by_type(dev_type, fs_dict)
        virsh.attach_device(vm_name, fs.xml,
                            debug=True, ignore_status=False)

    start_vm = params.get("start_vm", "no")
    vm_names = params.get("vms", "avocado-vt-vm1").split()
    guest_num = len(vm_names)
    cache_mode = params.get("cache_mode", "none")
    sandbox_mode = params.get("sandbox_mode", "none")
    xattr = params.get("xattr", "on")
    path = params.get("virtiofsd_path", "/usr/libexec/virtiofsd")
    thread_pool_size = params.get("thread_pool_size")
    openfiles = params.get("openfiles", "no") == "yes"
    queue_size = params.get("queue_size", "512")
    driver_type = params.get("driver_type", "virtiofs")
    fs_num = int(params.get("fs_num", "1"))
    vcpus_per_cell = int(params.get("vcpus_per_cell", 2))
    dir_prefix = params.get("dir_prefix", "mount_tag")
    error_msg_start = params.get("error_msg_start", "")
    error_msg_save = params.get("error_msg_save", "")
    status_error = params.get("status_error", "no") == "yes"
    socket_file_checking = params.get("socket_file_checking", "no") == "yes"
    coldplug = params.get("coldplug", "no") == "yes"
    hotplug_unplug = params.get("hotplug_unplug", "no") == "yes"
    detach_device_alias = params.get("detach_device_alias", "no") == "yes"
    extra_hugepages = params.get_numeric("extra_hugepages")
    lifecycle_scenario = params.get("lifecycle_scenario", "")
    with_hugepages = params.get("with_hugepages", "yes") == "yes"
    with_numa = params.get("with_numa", "yes") == "yes"
    with_memfd = params.get("with_memfd", "no") == "yes"
    source_socket = params.get("source_socket", "/var/tmp/vm001.socket")
    launch_mode = params.get("launch_mode", "auto")
    bug_url = params.get("bug_url", "")
    script_content = params.get("stress_script", "")
    stdio_handler = params.get("stdio_handler", "")
    setup_mem = params.get("setup_mem", False)
    omit_dir_at_first = "yes" == params.get("omit_dir_at_first", "no")
    check_debug_log = "yes" == params.get("check_debug_log", "no")

    qemu_config = None
    fs_devs = []
    vms = []
    vmxml_backups = []
    expected_fails_msg = []
    backup_huge_pages_num = utils_memory.get_num_huge_pages()
    host_hp_size = utils_memory.get_huge_page_size()
    huge_pages_num = 0

    if hotplug_unplug and not utils_path.find_command("lsof", default=False):
        test.error("Lsof command is required to run test, but not installed")

    if not libvirt_version.version_compare(7, 0, 0) and not with_numa:
        test.cancel("Not supported without NUMA before 7.0.0")

    if not libvirt_version.version_compare(7, 6, 0) and lifecycle_scenario == "destroy_start":
        test.cancel("Bug %s is not fixed on current build" % bug_url)

    if not libvirt_version.version_compare(10, 0, 0) and lifecycle_scenario == "managed_save":
        test.cancel("Bug %s is not fixed on current build" % bug_url)

    try:
        if setup_mem:
            libvirt_version.is_libvirt_feature_supported(params)
            check_filesystem_hotplug_with_mem_setup()
            return

        if openfiles:
            with open('/proc/sys/fs/nr_open', 'r') as file:
                open_files_max = file.read().strip()
        else:
            open_files_max = None

        def _get_fs_dev_and_source_dir(fs_index, socket_index):
            """
            Returns the device XML for both internally or externally launched
            virtiofsd.

            :param fs_index: the index of the filesystem
            :param socket_index: the index of the socket for externally launched
                                 virtiofsd; this parameter is ignored for internally
                                 launched virtiofsd and must be different for each
                                 VM accessing the filesystem
            :return tuple: (fs_dev, source_dir) - the device xml but also the source
                           directory which is not part of the XML so the test can
                           launch the instance for it
            """
            driver = {'type': driver_type}
            if queue_size != "":
                driver['queue'] = queue_size
            source_dir = os.path.join('/var/tmp/', str(dir_prefix) + str(fs_index))
            logging.debug(f"This filesystem has source dir: {source_dir}")
            if not os.path.isdir(source_dir):
                if not (omit_dir_at_first and fs_index == 0):
                    os.mkdir(source_dir)
            target_dir = dir_prefix + str(fs_index)
            target = {'dir': target_dir}
            if launch_mode == "auto":
                binary_keys = ['path', 'cache_mode', 'xattr',
                               'thread_pool_size', "open_files_max", "sandbox_mode"]
                binary_values = [path, cache_mode, xattr,
                                 thread_pool_size, open_files_max, sandbox_mode]
                binary_dict = dict(zip(binary_keys, binary_values))
                source = {'dir': source_dir}
                accessmode = "passthrough"
                fsdev_keys = ['accessmode', 'driver', 'source', 'target', 'binary']
                fsdev_values = [accessmode, driver, source, target, binary_dict]
            else:
                source_socket = f"/var/tmp/vm_test{socket_index}.socket"
                source = {'socket': source_socket}
                fsdev_keys = ['driver', 'source', 'target']
                fsdev_values = [driver, source, target]
            fsdev_dict = dict(zip(fsdev_keys, fsdev_values))
            logging.debug(fsdev_dict)
            fs_dev = libvirt_device_utils.create_fs_xml(fsdev_dict, launch_mode)
            logging.debug(fs_dev)
            return fs_dev, source_dir

        def create_fs_devs():
            """
            Creates all fs devs necessary for the test setup and adds them
            to the list fs_devs.
            """
            with_sockets = launch_mode == "externally"

            def __indexes():
                """
                Helper function that creates input parameters for
                _get_fs_dev_and_source_dir. They are different for internally
                and externally launched virtiofsd, e.g. for 2 times 2:
                (fs_index, socket_index)
                internally: the same xml can be used for all guests:
                            (0, None), (1, None)
                externally: there must be 1 socket for each instance and guest:
                            (0, 0), (0, 1), (1, 2), (1, 3)
                """
                socket_number = -1
                for i in range(fs_num):
                    if not with_sockets:
                        yield (i, None)
                    else:
                        for j in range(guest_num):
                            socket_number += 1
                            yield (i, socket_number)

            for index in __indexes():
                fs_dev, source_dir = _get_fs_dev_and_source_dir(index[0], index[1])
                if with_sockets:
                    launch_externally_virtiofs(source_dir, fs_dev.source['socket'])
                fs_devs.append(fs_dev)

        def _fs_dev_indexes(guest_index):
            """
            Returns the list of indexes of all filesystem devices in fs_devs
            that correspond to guest_index, e.g. for 2 times 2:
            (fs_dev_index, guest_index)
            internally launched: no socket necessary, reuse file
                fs_dev with index 0, 1 can both be attached to guest 0, 1
            externally launched: can't reuse file
                fs_dev with index 0, 2 (with sockets 0, 2) are attached to guest 0
                fs_dev with index 1, 3 (with sockets 1, 3) are attached to guest 1

            :param guest_index: the index of the VM in vms list
            """
            if launch_mode == "externally":
                return range(guest_index, fs_num*guest_num, guest_num)
            else:
                return range(fs_num)

        def update_vm_with_fs_devs(guest_index, vmxml, attach):
            """
            Either updates the VM XML or attaches the device XML

            :param guest_index: the index of the guest in vms
            :param vmxml: VMXML instance for guest_index
            :param attach: if True uses `virsh attach` else it redefines the VM
            """
            fs_indexes = _fs_dev_indexes(guest_index)
            if attach:
                for fs_index in fs_indexes:
                    ret = virsh.attach_device(vms[guest_index].name, fs_devs[fs_index].xml,
                                              flagstr='--current', debug=True)
                    utils_test.libvirt.check_exit_status(ret, expect_error=False)
            else:
                for fs_index in fs_indexes:
                    vmxml.add_device(fs_devs[fs_index])
                vmxml.sync()
            logging.debug(f"VMXML after adding device: {vmxml}")

        def detach_fs_dev(guest_index):
            """
            The function uses the virsh detach command to detach
            all file systems from the guest determined by its index.

            :param guest_index: the index of the VM as given by the vms list
            """
            vm = vms[guest_index]
            fs_indexes = _fs_dev_indexes(guest_index)
            for fs_index in fs_indexes:
                fs_dev = fs_devs[fs_index]
                if detach_device_alias and launch_mode == "auto":
                    utils_package.package_install("lsof")
                    alias = fs_dev.alias['name']
                    cmd = 'lsof /var/log/libvirt/qemu/%s-%s-virtiofsd.log' % (vm.name, alias)
                    output = process.run(cmd).stdout_text.splitlines()
                    for item in output[1:]:
                        if stdio_handler == "file":
                            if item.split()[0] != "virtiofsd":
                                test.fail("When setting stdio_handler as file, the command"
                                          " to write log should be virtiofsd!")
                        elif stdio_handler == "logd":
                            if item.split()[0] != "virtlogd":
                                test.fail(" When setting stdio_handler as logd, the command"
                                          "to write log should be virtlogd!")
                    ret = virsh.detach_device_alias(vm.name, alias, ignore_status=True,
                                                    debug=True, wait_for_event=True,
                                                    event_timeout=10)
                else:
                    ret = virsh.detach_device(vm.name, fs_dev.xml, ignore_status=True,
                                              debug=True, wait_for_event=True)
                libvirt.check_exit_status(ret, status_error)
                check_filesystem_in_guest(vm, fs_dev)

        def set_up_and_start_vm(guest_index):
            """
            Updates the domain xml according to test scenario
            and starts it

            :param guest_index: the index of the guest to be handled
            :return end_of_test: True if test should terminate after VM
                                 can be started
            """
            vm = env.get_vm(vm_names[guest_index])
            logging.debug("prepare vm %s", vm.name)
            vms.append(vm)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            vmxml_backups.append(vmxml.copy())
            if vmxml.max_mem < 1024000:
                vmxml.max_mem = 1024000
            if with_hugepages:
                nonlocal huge_pages_num
                huge_pages_num += vmxml.max_mem // host_hp_size + extra_hugepages
                utils_memory.set_num_huge_pages(huge_pages_num)
            vmxml.remove_all_device_by_type('filesystem')
            vmxml.sync()
            numa_no = None
            if with_numa:
                numa_no = vmxml.vcpu // vcpus_per_cell if vmxml.vcpu != 1 else 1
            vm_xml.VMXML.set_vm_vcpus(vmxml.vm_name, vmxml.vcpu, numa_number=numa_no)
            vm_xml.VMXML.set_memoryBacking_tag(vmxml.vm_name, access_mode="shared",
                                               hpgs=with_hugepages, memfd=with_memfd)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            logging.debug(f"VMXML before adding device: {vmxml}")
            if coldplug:
                update_vm_with_fs_devs(guest_index, vmxml, attach=True)
            else:
                if not hotplug_unplug:
                    update_vm_with_fs_devs(guest_index, vmxml, attach=False)
            libvirt_pcicontr.reset_pci_num(vm.name)
            result = virsh.start(vm.name, debug=True)
            if omit_dir_at_first:
                expect_error = True
                libvirt.check_exit_status(result, expect_error)
                source_dir = os.path.join('/var/tmp/', str(dir_prefix) + str(0))
                os.mkdir(source_dir)
                result = virsh.start(vm.name, debug=True)
                libvirt.check_exit_status(result, not expect_error)
                return True
            if hotplug_unplug:
                update_vm_with_fs_devs(guest_index, vmxml, attach=True)
                if status_error:
                    return True

            if status_error and not lifecycle_scenario == "managedsave":
                expected_error = error_msg_start
                utils_test.libvirt.check_exit_status(result, expected_error)
                return True
            else:
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            if launch_mode == "auto":
                expected_results = generate_expected_process_options()
                cmd = 'ps aux | grep /usr/libexec/virtiofsd'
                utils_test.libvirt.check_cmd_output(cmd, content=expected_results)

        def check_qemu_cmdline():
            """
            Checks the qemu command line output
            At this point only queue size is checked.
            """
            if (
                queue_size
                and int(queue_size) > 0
                and not hotplug_unplug
            ):
                cmd = 'ps aux | grep qemu-kvm'
                content = [f"queue-size.{{1,3}}{queue_size}"]
                utils_test.libvirt.check_cmd_output(
                    cmd,
                    content=content
                )

        def check_file_exists(vm, filepath):
            """
            Confirm that the given file path exists.
            It should have been created earlier in the `shared_data`
            function

            :param vm: VM instance
            :param filepath: the filepath created in the shared directory
            :raise TestFail: file not found
            """
            session = vm.wait_for_login()
            status, output = session.cmd_status_output(f"ls {filepath}")
            if status:
                test.fail(f"{filepath} not found in the mount: {output}")

        def update_qemu_config():
            """
            Updates Libvirt's QEMU configuration if needed
            """
            if not any([stdio_handler, check_debug_log]):
                return
            nonlocal qemu_config
            qemu_config = LibvirtQemuConfig()
            if stdio_handler:
                qemu_config.stdio_handler = stdio_handler
            if check_debug_log:
                qemu_config.virtiofsd_debug = 1
            utils_libvirtd.Libvirtd().restart()

        update_qemu_config()
        create_fs_devs()

        for index in range(guest_num):
            end_test = set_up_and_start_vm(index)
            check_qemu_cmdline()
            if end_test:
                return

        shared_data(vm_names, fs_devs)

        if check_debug_log:
            alias = fs_devs[0].alias['name']
            file_name = '/var/log/libvirt/qemu/%s-%s-virtiofsd.log' % (vm_names[0], alias)
            try:
                with open(file_name, 'r') as f:
                    output = f.read()
                if "DEBUG" not in output:
                    shutil.copy(file_name, test.debugdir)
                    test.fail("Failed to find string in virtiofsd log.")
            except FileNotFoundError:
                test.fail("virtiofsd log not found")

        if lifecycle_scenario == "suspend_resume":
            virsh.suspend(vm_names[0], debug=True, ignore_status=False)
            time.sleep(30)
            virsh.resume(vm_names[0], debug=True, ignore_statue=False)
        elif lifecycle_scenario == "managedsave":
            virsh.managedsave(vm_names[0], ignore_status=True, debug=True)
            save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_names[0]
            if not os.path.exists(save_file):
                test.fail("guest is not manangedsaved")
            virsh.start(vm_names[0], ignore_status=True, debug=True)
            if os.path.exists(save_file):
                test.fail("guest is not restored from the managedsave file")
        elif lifecycle_scenario in [
                "destroy_start",
                "shutdown_start",
                "reboot"
        ]:
            if fs_num > 1 or guest_num > 1:
                test.error("some lifecycle tests are implemented for only"
                           " 1 guest and filesystem")
            session = vms[0].wait_for_login(timeout=120)
            session.cmd_status_output(
                "echo 'mount_tag0 /var/tmp/mount_tag0 "
                "virtiofs defaults 0 0' >> /etc/fstab"
            )
            if lifecycle_scenario == "destroy_start":
                # Prepare the guest test script
                _, source_dir = _get_fs_dev_and_source_dir(0, 0)
                script_path = os.path.join(source_dir, "test.py")
                script_content %= (source_dir, source_dir)
                prepare_stress_script(script_path, script_content)
                # Run guest stress script
                stress_script_thread = threading.Thread(target=run_stress_script,
                                                        args=(session, script_path))
                stress_script_thread.setDaemon(True)
                stress_script_thread.start()
                # Creates a lot of unlink files
                time.sleep(60)
                virsh.destroy(vm_names[0], debug=True, ignore_status=False)
                ret = virsh.start(vm_names[0], debug=True)
                libvirt.check_exit_status(ret)
            elif lifecycle_scenario == "shutdown_start":
                virsh.shutdown(
                    vm_names[0],
                    debug=True,
                    ignore_status=False
                )
                utils_misc.wait_for(vms[0].is_dead, timeout=60)
                ret = virsh.start(vm_names[0], debug=True)
                libvirt.check_exit_status(ret)
            elif lifecycle_scenario == "reboot":
                ret = virsh.reboot(vm_names[0], debug=True, ignore_status=False)
                libvirt.check_exit_status(ret)
            else:
                test.fail("Test case not implemented.")
        if lifecycle_scenario:
            check_file_exists(vms[0], "/var/tmp/mount_tag0/" + vm_names[0])
        elif lifecycle_scenario == "edit_start":
            vmxml_virtio_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[0])
            if vm.is_alive():
                virsh.destroy(vm_names[0])
                cmd = "virt-xml %s --edit --qemu-commandline '\-foo'" % vm_names[0]
                cmd_result = process.run(cmd, ignore_status=True, shell=True)
                logging.debug(virsh.dumpxml(vm_names[0]))
                if cmd_result.exit_status:
                    test.error("virt-xml edit guest failed: %s" % cmd_result)
                result = virsh.start(vm_names[0], ignore_status=True, debug=True)
                if error_msg_start:
                    expected_fails_msg.append(error_msg_start)
                utils_test.libvirt.check_result(result, expected_fails=expected_fails_msg)
                if not libvirt_version.version_compare(6, 10, 0):
                    # Because of bug #1897105, it was fixed in libvirt-6.10.0,
                    # before this version, need to recover the env manually.
                    cmd = "pkill virtiofsd"
                    process.run(cmd, shell=True)
                if not vm.is_alive():
                    # Restoring vm and check if vm can start successfully
                    vmxml_virtio_backup.sync()
                    virsh.start(vm_names[0], ignore_status=False, shell=True)
        elif socket_file_checking:
            result = virsh.domid(vm_names[0])
            domid = result.stdout.strip()
            domain_dir = "var/lib/libvirt/qemu/domain-" + domid + '-' + vm_names[0]
            if result.exit_status:
                test.fail("Get domid failed.")
                for fs_dev in fs_devs:
                    alias = fs_dev.alias['name']
                    expected_pid = domain_dir + alias + '-fs.pid'
                    expected_sock = alias + '-fs.sock'
                    status1 = process.run('ls -l %s' % expected_pid, shell=True).exit_status
                    status2 = process.run('ls -l %s' % expected_sock, shell=True).exit_status
                    if not (status1 and status2):
                        test.fail("The socket and pid file is not as expected")
        elif hotplug_unplug:
            for guest_index in range(guest_num):
                umount_fs(guest_index)
                vm = vms[guest_index]
                detach_fs_dev(guest_index)
                check_detached_xml(vms[guest_index])
    finally:
        for vm in vms:
            index = vms.index(vm)
            if vm.is_alive():
                umount_fs(index)
                vm.destroy(gracefully=False)
            virsh.managedsave_remove(vm.name, debug=True, ignore_status=True)
            vmxml_backups[index].sync()
        utils_memory.set_num_huge_pages(backup_huge_pages_num)
        if qemu_config:
            qemu_config.restore()
            utils_libvirtd.Libvirtd().restart()
        for path_pattern in [
                "/var/log/libvirt/qemu/*-virtiofsd.log'",
                "/var/tmp/vm_test*socket*",
        ]:
            for file in glob(path_pattern):
                os.remove(file)
        for folder in glob("/var/tmp/%s*" % str(dir_prefix)):
            shutil.rmtree(folder)
        if launch_mode == "externally":
            process.run('restorecon %s' % path, ignore_status=False, shell=True)
