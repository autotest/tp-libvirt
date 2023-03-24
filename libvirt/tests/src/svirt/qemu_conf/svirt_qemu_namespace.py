import os

from avocado.utils import process

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def mount(params):
    """
    Mount filesystems.

    :param params: Test parameters
    """
    for f in [v for k, v in params.items() if k.startswith(('mount_src',
                                                           'mount_dst'))]:
        if not os.path.exists(f):
            open(f, 'a').close()
    for cmd in [v for k, v in params.items() if k.startswith('mount_cmd_')]:
        process.run(cmd, shell=True)


def umount(params):
    """
    Umount filesystems.

    :param params: Test parameters
    """
    umount_sources = [v for k, v in params.items() if k.startswith(('mount_dst'))]
    umount_sources.append(params.get("mount_dst"))
    for umount_src in umount_sources:
        process.run("umount %s" % umount_src, shell=True, ignore_status=True)
    for f in [v for k, v in params.items() if k.startswith(('mount_src_',
                                                           'mount_dst_'))]:
        if os.path.exists(f):
            os.unlink(f)


def check_mounting_fs_in_ns(qemu_pid, params):
    """
    Check the files mounted in qemu name spaces.

    :param qemu_pid: PID of qemu
    :param params: Test parameters
    """
    nsenter_cmds = [v for k, v in params.items() if k.startswith(
        ('nsenter_cmd_'))]
    for nsenter_cmd in nsenter_cmds:
        result = process.run(f"nsenter -t {qemu_pid} -m {nsenter_cmd}",
                             shell=True, verbose=True)
        libvirt.check_result(result, expected_match=nsenter_cmd.split()[-1])


def run(test, params, env):
    """Test namespace setting in qemu.conf."""

    # Get general variables.
    qemu_conf_1 = eval(params.get("qemu_conf_1", "{}"))
    qemu_conf_2 = eval(params.get("qemu_conf_2", "{}"))
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    expr_dev_context = params.get("expr_dev_context")
    expr_dev_context_ns = params.get("expr_dev_context_ns")
    qemu_conf_objs = []

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    try:
        test.log.info("TEST_STEP: Enable qemu namespaces in qemu.conf.")
        qemu_conf_objs.append(libvirt.customize_libvirt_config(qemu_conf_1,
                                                               "qemu"))

        test.log.info("TEST_STEP: Do nested mount; mount files under perserved "
                      "mount points.")
        mount(params)

        test.log.info("TEST_STEP: Start the VM.")
        img_path = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        disk_attrs.update({'source': {'attrs': {'dev': img_path}}})
        libvirt_vmxml.modify_vm_device(vmxml, "disk", disk_attrs, 1)
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP: Check the dac and selinux context.")
        label_output = process.run("ls -lZ %s" % img_path, shell=True)
        libvirt.check_result(label_output, expected_match=expr_dev_context)

        test.log.info("TEST_STEP: Check dac and selinux context in qemu namespaces.")
        qemu_pid = process.run("lsns|awk '/qemu.*%s/ {print $4}'" % vm.name,
                               shell=True, verbose=True).stdout_text.strip()
        result = process.run(f"nsenter -t {qemu_pid} -m ls -l {img_path} -Z",
                             shell=True, verbose=True)
        libvirt.check_result(result, expected_match=expr_dev_context_ns)

        test.log.info("TEST_STEP: Check the mounted files in qemu namespaces.")
        check_mounting_fs_in_ns(qemu_pid, params)

        test.log.info("TEST_STEP: Disable namespace in the qemu.conf.")
        qemu_conf_objs.append(libvirt.customize_libvirt_config(qemu_conf_2,
                                                               "qemu"))
        if not libvirt.check_vm_state(vm.name, state="running"):
            test.fail("VM should be running.")

        test.log.info("TEST_STEP: Destroy and start the VM and check qemu "
                      "namespaces.")
        vm.destroy()
        vm.start()
        result = process.run("lsns|grep 'qemu.*%s'" % vm.name, shell=True,
                             verbose=True, ignore_status=True)
        libvirt.check_exit_status(result, True)
    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        vm.destroy(gracefully=False)
        backup_xml.sync()

        umount(params)

        for qemu_conf_obj in qemu_conf_objs[::-1]:
            libvirt.customize_libvirt_config(
                None, "qemu", config_object=qemu_conf_obj,
                is_recover=True)
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
