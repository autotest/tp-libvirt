import os
import logging as log
import time
import threading

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

    def generate_expected_process_option(expected_results):
        """
        Generate expected virtiofsd process option
        """
        if cache_mode != "auto":
            expected_results = "cache=%s" % cache_mode
        if xattr == "on":
            expected_results += ",xattr"
        elif xattr == "off":
            expected_results += ",no_xattr"
        if flock == "on":
            expected_results += ",flock"
        else:
            expected_results += ",no_flock"
        if lock_posix == "on":
            expected_results += ",posix_lock"
        else:
            expected_results += ",no_posix_lock"
        if thread_pool_size:
            expected_results += " --thread-pool-size=%s" % thread_pool_size
        logging.debug(expected_results)
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
            session = vm.wait_for_login()
            for fs_dev in fs_devs:
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
                if vm == vms[0]:
                    filename_guest = mount_dir + '/' + vm.name
                    cmd = "dd if=/dev/urandom of=%s bs=1M count=512 oflag=direct" % filename_guest
                    status, output = session.cmd_status_output(cmd, timeout=300)
                    if status != 0:
                        session.close()
                        test.fail("Write data failed: %s" % output)
                md5_value = session.cmd_status_output("md5sum %s" % filename_guest,
                                                      timeout=300)[1].strip().split()[0]
                md5s.append(md5_value)
                logging.debug(md5_value)
                md5_value = process.run("md5sum %s" % filename_guest).stdout_text.strip().split()[0]
                logging.debug(md5_value)
                md5s.append(md5_value)
            session.close()
        if len(set(md5s)) != len(fs_devs):
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

    def umount_fs(vm):
        """
        Unmount the filesystem in guest

        :param vm: filesystem in this vm that should be unmounted
        """
        if vm.is_alive():
            session = vm.wait_for_login()
            for fs_dev in fs_devs:
                mount_dir = '/var/tmp/' + fs_dev.target['dir']
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

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_names[int(guest_num) - 1])
        vmxml.setup_attrs(**vm_attrs)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        libvirtd = utils_libvirtd.Libvirtd()
        libvirtd.restart()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_names[int(guest_num) - 1])
        vmxml.remove_all_device_by_type(dev_type)
        vmxml.sync()
        vm = env.get_vm(vm_names[int(guest_num) - 1])
        vm.start()
        vm.wait_for_login(timeout=120)
        os.mkdir(source_dir)

        fs = libvirt_vmxml.create_vm_device_by_type(dev_type, fs_dict)
        virsh.attach_device(vm_names[int(guest_num) - 1], fs.xml,
                            debug=True, ignore_status=False)

    start_vm = params.get("start_vm", "no")
    vm_names = params.get("vms", "avocado-vt-vm1").split()
    cache_mode = params.get("cache_mode", "none")
    xattr = params.get("xattr", "on")
    lock_posix = params.get("lock_posix", "on")
    flock = params.get("flock", "on")
    xattr = params.get("xattr", "on")
    path = params.get("virtiofsd_path", "/usr/libexec/virtiofsd")
    thread_pool_size = params.get("thread_pool_size")
    queue_size = int(params.get("queue_size", "512"))
    driver_type = params.get("driver_type", "virtiofs")
    guest_num = int(params.get("guest_num", "1"))
    fs_num = int(params.get("fs_num", "1"))
    vcpus_per_cell = int(params.get("vcpus_per_cell", 2))
    dir_prefix = params.get("dir_prefix", "mount_tag")
    error_msg_start = params.get("error_msg_start", "")
    error_msg_save = params.get("error_msg_save", "")
    status_error = params.get("status_error", "no") == "yes"
    socket_file_checking = params.get("socket_file_checking", "no") == "yes"
    suspend_resume = params.get("suspend_resume", "no") == "yes"
    managedsave = params.get("managedsave", "no") == "yes"
    coldplug = params.get("coldplug", "no") == "yes"
    hotplug_unplug = params.get("hotplug_unplug", "no") == "yes"
    detach_device_alias = params.get("detach_device_alias", "no") == "yes"
    extra_hugepages = params.get_numeric("extra_hugepages")
    edit_start = params.get("edit_start", "no") == "yes"
    with_hugepages = params.get("with_hugepages", "yes") == "yes"
    with_numa = params.get("with_numa", "yes") == "yes"
    with_memfd = params.get("with_memfd", "no") == "yes"
    source_socket = params.get("source_socket", "/var/tmp/vm001.socket")
    launched_mode = params.get("launched_mode", "auto")
    destroy_start = params.get("destroy_start", "no") == "yes"
    bug_url = params.get("bug_url", "")
    script_content = params.get("stress_script", "")
    stdio_handler_file = "file" == params.get("stdio_handler")
    setup_mem = params.get("setup_mem", False)

    fs_devs = []
    vms = []
    vmxml_backups = []
    expected_fails_msg = []
    expected_results = ""
    host_hp_size = utils_memory.get_huge_page_size()
    backup_huge_pages_num = utils_memory.get_num_huge_pages()
    huge_pages_num = 0

    if hotplug_unplug and not utils_path.find_command("lsof", default=False):
        test.cancel("Lsof command is required to run test, but not installed")

    if len(vm_names) != guest_num:
        test.cancel("This test needs exactly %d vms." % guest_num)

    if not libvirt_version.version_compare(7, 0, 0) and not with_numa:
        test.cancel("Not supported without NUMA before 7.0.0")

    if not libvirt_version.version_compare(7, 6, 0) and destroy_start:
        test.cancel("Bug %s is not fixed on current build" % bug_url)

    try:
        if setup_mem:
            libvirt_version.is_libvirt_feature_supported(params)
            check_filesystem_hotplug_with_mem_setup()
            return
        # Define filesystem device xml
        for index in range(fs_num):
            driver = {'type': driver_type, 'queue': queue_size}
            source_dir = os.path.join('/var/tmp/', str(dir_prefix) + str(index))
            logging.debug(source_dir)
            not os.path.isdir(source_dir) and os.mkdir(source_dir)
            target_dir = dir_prefix + str(index)
            source = {'socket': source_socket}
            target = {'dir': target_dir}
            if launched_mode == "auto":
                binary_keys = ['path', 'cache_mode', 'xattr', 'lock_posix',
                               'flock', 'thread_pool_size']
                binary_values = [path, cache_mode, xattr, lock_posix,
                                 flock, thread_pool_size]
                binary_dict = dict(zip(binary_keys, binary_values))
                source = {'dir': source_dir}
                accessmode = "passthrough"
                fsdev_keys = ['accessmode', 'driver', 'source', 'target', 'binary']
                fsdev_values = [accessmode, driver, source, target, binary_dict]
            else:
                fsdev_keys = ['driver', 'source', 'target']
                fsdev_values = [driver, source, target]
            fsdev_dict = dict(zip(fsdev_keys, fsdev_values))
            logging.debug(fsdev_dict)
            fs_dev = libvirt_device_utils.create_fs_xml(fsdev_dict, launched_mode)
            logging.debug(fs_dev)
            fs_devs.append(fs_dev)

        #Start guest with virtiofs filesystem device
        for index in range(guest_num):
            logging.debug("prepare vm %s", vm_names[index])
            vm = env.get_vm(vm_names[index])
            vms.append(vm)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[index])
            vmxml_backup = vmxml.copy()
            vmxml_backups.append(vmxml_backup)
            if vmxml.max_mem < 1024000:
                vmxml.max_mem = 1024000
            if with_hugepages:
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
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[index])
            logging.debug(vmxml)
            if launched_mode == "externally":
                launch_externally_virtiofs(source_dir, source_socket)
            if coldplug:
                ret = virsh.attach_device(vm_names[index], fs_devs[0].xml,
                                          flagstr='--config', debug=True)
                utils_test.libvirt.check_exit_status(ret, expect_error=False)
            else:
                if not hotplug_unplug:
                    for fs in fs_devs:
                        vmxml.add_device(fs)
                        vmxml.sync()
            logging.debug(vmxml)
            libvirt_pcicontr.reset_pci_num(vm_names[index])
            result = virsh.start(vm_names[index], debug=True)
            if hotplug_unplug:
                if stdio_handler_file:
                    qemu_config = LibvirtQemuConfig()
                    qemu_config.stdio_handler = "file"
                    utils_libvirtd.Libvirtd().restart()
                for fs_dev in fs_devs:
                    ret = virsh.attach_device(vm_names[index], fs_dev.xml,
                                              ignore_status=True, debug=True)
                    libvirt.check_exit_status(ret, status_error)

                if status_error:
                    return
            if status_error and not managedsave:
                expected_error = error_msg_start
                utils_test.libvirt.check_exit_status(result, expected_error)
                return
            else:
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            expected_results = generate_expected_process_option(expected_results)
            if launched_mode == "auto":
                cmd = 'ps aux | grep /usr/libexec/virtiofsd'
                utils_test.libvirt.check_cmd_output(cmd, content=expected_results)

        if managedsave:
            expected_error = error_msg_save
            result = virsh.managedsave(vm_names[0], ignore_status=True, debug=True)
            utils_test.libvirt.check_exit_status(result, expected_error)
        else:
            shared_data(vm_names, fs_devs)
            if suspend_resume:
                virsh.suspend(vm_names[0], debug=True, ignore_status=False)
                time.sleep(30)
                virsh.resume(vm_names[0], debug=True, ignore_statue=False)
            elif destroy_start:
                session = vm.wait_for_login(timeout=120)
                # Prepare the guest test script
                script_path = os.path.join(fs_devs[0].source["dir"], "test.py")
                script_content %= (fs_devs[0].source["dir"], fs_devs[0].source["dir"])
                prepare_stress_script(script_path, script_content)
                # Run guest stress script
                stress_script_thread = threading.Thread(target=run_stress_script,
                                                        args=(session, script_path))
                stress_script_thread.setDaemon(True)
                stress_script_thread.start()
                # Create a lot of unlink files
                time.sleep(60)
                virsh.destroy(vm_names[0], debug=True, ignore_status=False)
                ret = virsh.start(vm_names[0], debug=True)
                libvirt.check_exit_status(ret)
            elif edit_start:
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
                for vm in vms:
                    umount_fs(vm)
                    for fs_dev in fs_devs:
                        if detach_device_alias:
                            utils_package.package_install("lsof")
                            alias = fs_dev.alias['name']
                            cmd = 'lsof /var/log/libvirt/qemu/%s-%s-virtiofsd.log' % (vm.name, alias)
                            output = process.run(cmd).stdout_text.splitlines()
                            for item in output[1:]:
                                if stdio_handler_file:
                                    if item.split()[0] != "virtiofsd":
                                        test.fail("When setting stdio_handler as file, the command"
                                                  "to write log should be virtiofsd!")
                                else:
                                    if item.split()[0] != "virtlogd":
                                        test.fail("When setting stdio_handler as logd, the command"
                                                  "to write log should be virtlogd!")
                            ret = virsh.detach_device_alias(vm.name, alias, ignore_status=True,
                                                            debug=True, wait_for_event=True,
                                                            event_timeout=10)
                        else:
                            ret = virsh.detach_device(vm.name, fs_dev.xml, ignore_status=True,
                                                      debug=True, wait_for_event=True)
                        libvirt.check_exit_status(ret, status_error)
                        check_filesystem_in_guest(vm, fs_dev)
                    check_detached_xml(vm)
    finally:
        for vm in vms:
            alias = fs_dev.alias['name']
            process.run('rm -f /var/log/libvirt/qemu/%s-%s-virtiofsd.log' % (vm.name, alias))
            if vm.is_alive():
                umount_fs(vm)
                vm.destroy(gracefully=False)
        for vmxml_backup in vmxml_backups:
            vmxml_backup.sync()
        for index in range(fs_num):
            process.run('rm -rf %s' % '/var/tmp/' + str(dir_prefix) + str(index), ignore_status=False)
            process.run('rm -rf %s' % source_socket, ignore_status=False, shell=True)
        if launched_mode == "externally":
            process.run('restorecon %s' % path, ignore_status=False, shell=True)
        utils_memory.set_num_huge_pages(backup_huge_pages_num)
        if stdio_handler_file:
            qemu_config.restore()
            utils_libvirtd.Libvirtd().restart()
