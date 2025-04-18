from aexpect import ShellProcessTerminatedError
from aexpect import ShellTimeoutError

from avocado.utils import process

from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    1. Start a guest with pstore device
    2. Check qemu-cmdline
    3. Login to guest and change kernel parameters
    4. Trigger a crash
    5. Check the log in /var/lib/systemd/pstore
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pstore_dict = eval(params.get("pstore_dict", "{}"))
    qemu_cmdline_str = params.get("qemu_cmdline_str")
    pstore_path = params.get("pstore_path")

    libvirt_version.is_libvirt_feature_supported(params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        libvirt_vmxml.modify_vm_device(vmxml, 'pstore', pstore_dict)
        vm.start()

        # Check qemu-cmdline
        ret = process.run(f"ps axu | grep {vm_name} | grep erst", shell=True, verbose=True).stdout_text.strip()
        if qemu_cmdline_str not in ret:
            test.fail(f"Not found '{qemu_cmdline_str}' in qemu cmdline.")

        vm_session = vm.wait_for_login()
        # Cleanup pstore path
        vm_session.cmd(f"rm -rf {pstore_path}/*")
        # Set kernel parameters
        vm_session.cmd("echo Y > /sys/module/printk/parameters/always_kmsg_dump")
        vm_session.cmd("echo Y > /sys/module/kernel/parameters/crash_kexec_post_notifiers")

        try:
            # Trigger a crash
            vm_session.cmd("echo c > /proc/sysrq-trigger", timeout=3)
        except (ShellTimeoutError, ShellProcessTerminatedError):
            pass
        vm_session.close()

        # Check log
        vm.destroy()
        vm.start()
        new_session = vm.wait_for_login()
        ret = new_session.cmd_output(f"ll {pstore_path}")
        if "dmesg-erst" not in ret:
            test.fail("Not found dmesg file in {pstore_path}")
        new_session.close()

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
