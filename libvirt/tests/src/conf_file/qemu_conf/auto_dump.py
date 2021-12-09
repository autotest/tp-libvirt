import os
import logging
import shutil
import platform
import multiprocessing
import time

from aexpect import ShellTimeoutError

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic
from virttest import data_dir
from virttest import utils_package
from virttest import libvirt_version


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
    target_flags = int(params.get('target_flags', '0o40000'), 8)

    if panic_model and not libvirt_version.version_compare(1, 3, 1):
        test.cancel("panic device model attribute not supported"
                    "on current libvirt version")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    dump_path = os.path.join(data_dir.get_data_dir(), "dump")
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
            test.cancel("No 'panic' device in the guest, maybe "
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

        # Install lsof pkg if not installed
        if not utils_package.package_install("lsof"):
            test.cancel("Failed to install lsof in host\n")

        def get_flags(dump_path, result_dict):
            cmd = "lsof -w %s/* |awk '/libvirt_i/{print $2}'" % dump_path
            start_time = time.time()
            while (time.time() - start_time) < 30:
                ret = process.run(cmd, shell=True, ignore_status=True)
                status, iohelper_pid = ret.exit_status, ret.stdout_text.strip()
                if status:
                    time.sleep(0.1)
                    continue
                if not len(iohelper_pid):
                    continue
                else:
                    logging.info('pid: %s', iohelper_pid)
                    result_dict['pid'] = iohelper_pid
                    break

            # Get file open flags containing bypass cache information.
            with open('/proc/%s/fdinfo/1' % iohelper_pid, 'r') as fdinfo:
                flags = 0
                for line in fdinfo.readlines():
                    if line.startswith('flags:'):
                        flags = int(line.split()[1], 8)
                        logging.debug('file open flag is: %o', flags)
            result_dict['flags'] = flags
            with open('/proc/%s/cmdline' % iohelper_pid) as cmdinfo:
                cmdline = cmdinfo.readline()
                logging.debug(cmdline.split())

        session = vm.wait_for_login()
        result_dict = multiprocessing.Manager().dict()
        child_process = multiprocessing.Process(target=get_flags, args=(dump_path, result_dict))
        child_process.start()

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

        child_process.join(10)
        if child_process.is_alive():
            child_process.terminate()
        flags = result_dict['flags']
        iohelper_pid = result_dict['pid']

        # Kill core dump process to speed up test
        try:
            process.run('kill %s' % iohelper_pid)
        except process.CmdError as detail:
            logging.debug("Dump already done:\n%s", detail)

        arch = platform.machine()

        if arch in ['x86_64', 'ppc64le', 's390x']:
            # Check if bypass cache flag set or unset accordingly.
            cond1 = (flags & target_flags) and bypass_cache != '1'
            cond2 = not (flags & target_flags) and bypass_cache == '1'
            if cond1 or cond2:
                test.fail('auto_dump_bypass_cache is %s but flags '
                          'is %o' % (bypass_cache, flags))
        else:
            test.cancel("Unknown Arch. Do the necessary changes to"
                        " support")

    finally:
        backup_xml.sync()
        config.restore()
        libvirtd.restart()
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
