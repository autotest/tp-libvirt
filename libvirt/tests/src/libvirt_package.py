import logging

from avocado.utils import process
from avocado.utils import software_manager

from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test packages to check file permissions.

    1) Check libvirtd status;
    2) Reinstall packages;
    3) Verify packages;
    4) Clean up.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    libvirtd_state = params.get("libvirtd", "on")
    pkg_list = eval(params.get("package_list", "[]"))
    logging.info("package list: %s" % pkg_list)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    if not len(pkg_list):
        test.cancel("Please specify libvirt package.")
    try:
        if libvirtd_state == "on":
            utils_libvirtd.libvirtd_start()
        elif libvirtd_state == "off":
            utils_libvirtd.libvirtd_stop()
        # if package exist, remove it
        sm = software_manager.SoftwareManager()
        pkg_exist = []
        for item in pkg_list:
            if sm.check_installed(item):
                pkg_exist.append(item)
                sm.remove(item)
            # install package
            sm.install(item)
            # verify package
            cmd = "rpm -V %s" % item
            ret = process.run("rpm -V %s" % item, shell=True)
            if ret.exit_status:
                test.fail("Check %s failed." % item)
            if ret.stdout_text.strip():
                test.fail("Verify %s failed: %s" % (item, ret.stdout_text.strip()))
    finally:
        vmxml_backup.sync()
        for item in pkg_list:
            if item in pkg_exist:
                break
            else:
                process.run("rpm -e %s" % item, shell=True, ignore_status=True)
