import os
import logging
import shutil

from aexpect import ShellTimeoutError

from autotest.client.shared import error
from autotest.client.shared import utils

from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic

from provider import libvirt_version


def run(test, params, env):
    """
    Test auto_dump_* parameter in qemu.conf.

    1) Change auto_dump_* in qemu.conf;
    2) Restart libvirt daemon;
    4) Check if file open state changed accordingly.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    bypass_cache = params.get("auto_dump_bypass_cache", "not_set")
    panic_model = params.get("panic_model")
    addr_type = params.get("addr_type")
    addr_iobase = params.get("addr_iobase")
    vm = env.get_vm(vm_name)

    if panic_model and not libvirt_version.version_compare(1, 3, 1):
        raise error.TestNAError("panic device model attribute not supported"
                                " on current libvirt version")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    dump_path = os.path.join(test.tmpdir, "dump")
    try:
        if not vmxml.xmltreefile.find('devices').findall('panic'):
            # Set panic device
            panic_dev = Panic()
            if panic_model:
                panic_dev.model = panic_model
            if addr_type:
                panic_dev.addr_type = addr_type
            if addr_iobase:
                panic_dev.addr_iobase = addr_iobase
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

        config.auto_dump_path = dump_path
        if os.path.exists(dump_path):
            os.rmdir(dump_path)
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
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
