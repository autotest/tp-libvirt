import os
import logging
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic
from virttest.aexpect import ShellTimeoutError
from autotest.client.shared import error
from autotest.client.shared import utils


def run(test, params, env):
    """
    Test auto_dump_* parameter in qemu.conf.

    1) Change auto_dump_* in qemu.conf;
    2) Restart libvirt daemon;
    4) Check if file open state changed accordingly.
    """
    vm_name = params.get("main_vm", "virt-tests-vm1")
    bypass_cache = params.get("auto_dump_bypass_cache", "not_set")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # Set panic device
        panic_dev = Panic()
        panic_dev.addr_type = "isa"
        panic_dev.addr_iobase = "0x505"
        vmxml.add_device(panic_dev)
        vmxml.on_crash = "coredump-restart"
        vmxml.sync()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if not vmxml.xmltreefile.find('devices').findall('panic'):
            raise error.TestNAError("No 'panic' device in the guest, maybe "
                                    "your libvirt version doesn't support it")

        # Setup qemu.conf
        if bypass_cache == 'not_set':
            del config.auto_dump_bypass_cache
        else:
            config.auto_dump_bypass_cache = bypass_cache

        dump_path = os.path.join(test.tmpdir, "dump")
        config.auto_dump_path = dump_path
        os.mkdir(dump_path)

        # Restart libvirtd to make change valid.
        libvirtd.restart()

        # Restart VM to create a new qemu process.
        if vm.is_alive():
            vm.destroy()
        vm.start()

        session = vm.wait_for_login()
        # Stop kdump in the guest
        session.cmd("service kdump stop", ignore_all_errors=True)
        # Enable sysRq
        session.cmd("echo 1 > /proc/sys/kernel/sysrq")
        try:
            # Crash the guest
            session.cmd("echo c > /proc/sysrq-trigger", timeout=1)
        except ShellTimeoutError:
            pass
        session.close()

        iohelper_pid = utils.run('pgrep -f %s' % dump_path).stdout.strip()
        logging.error('%s', iohelper_pid)

        # Get file open flags containing bypass cache information.
        fdinfo = open('/proc/%s/fdinfo/1' % iohelper_pid, 'r')
        flags = 0
        for line in fdinfo.readlines():
            if line.startswith('flags:'):
                flags = int(line.split()[1], 8)
                logging.debug('File open flag is: %o', flags)
        fdinfo.close()

        cmdline = open('/proc/%s/cmdline' % iohelper_pid).readline()
        logging.debug(cmdline.split())

        # Kill core dump process to speed up test
        utils.run('kill %s' % iohelper_pid)

        # Check if bypass cache flag set or unset accordingly.
        if (flags & 040000) and bypass_cache != '1':
            raise error.TestFail('auto_dump_bypass_cache is %s but flags '
                                 'is %o' % (bypass_cache, flags))
        if not (flags & 040000) and bypass_cache == '1':
            raise error.TestFail('auto_dump_bypass_cache is %s but flags '
                                 'is %o' % (bypass_cache, flags))
    finally:
        backup_xml.sync()
        config.restore()
        libvirtd.restart()
