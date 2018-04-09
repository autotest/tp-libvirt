import logging
import re

from avocado.utils import process

from virttest import ssh_key
from virttest import libvirt_vm
from virttest import utils_libvirtd
import virttest.utils_libguestfs as lgf


def login_to_check_foo_line(test, vm, file_ref, foo_line):
    """
    Login to check whether the foo line has been added to file.
    """

    if not vm.is_alive():
        vm.start()

    backup = "%s.bak" % file_ref

    try:
        session = vm.wait_for_login()
        cat_file = session.cmd_output("cat %s" % file_ref)
        logging.info("\n%s", cat_file)
        session.cmd("rm -f %s" % backup)
        session.cmd("cp -f %s %s" % (file_ref, backup))
        session.cmd("sed -e \'s/%s$//g\' %s > %s" %
                    (foo_line, backup, file_ref))
        session.cmd('rm -f %s' % backup)
        session.close()
    except Exception as detail:
        test.error("Cleanup failed:\n%s" % detail)

    vm.destroy()
    if not re.search(foo_line, cat_file):
        logging.info("Can not find %s in %s.", foo_line, file_ref)
        return False
    return True


def cleanup_file_in_vm(test, vm, file_path):
    """Remove backup file in vm"""
    if not vm.is_alive():
        vm.start()
    try:
        session = vm.wait_for_login()
        session.cmd("rm -f %s" % file_path)
        session.close()
    except Exception as detail:
        test.error("Cleanup failed:\n%s" % detail)
    vm.destroy()


def run(test, params, env):
    """
    Test of virt-edit.

    1) Get and init parameters for test.
    2) Prepare environment.
    3) Run virt-edit command and get result.
    5) Recover environment.
    6) Check result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    remote_host = params.get("virt_edit_remote_host", "HOST.EXAMPLE")
    remote_user = params.get("virt_edit_remote_user", "root")
    remote_passwd = params.get("virt_edit_remote_passwd", "PASSWD.EXAMPLE")
    connect_uri = params.get("virt_edit_connect_uri")
    if connect_uri is not None:
        uri = "qemu+ssh://%s@%s/system" % (remote_user, remote_host)
        if uri.count("EXAMPLE"):
            test.cancel("Please config host and passwd first.")
        # Config ssh autologin for it
        ssh_key.setup_ssh_key(remote_host, remote_user, remote_passwd, port=22)
    else:
        uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                          "default"))
    start_vm = params.get("start_vm", "no")
    vm_ref = params.get("virt_edit_vm_ref", vm_name)
    file_ref = params.get("virt_edit_file_ref", "/etc/hosts")
    created_img = params.get("virt_edit_created_img", "/tmp/foo.img")
    foo_line = params.get("foo_line", "")
    options = params.get("virt_edit_options")
    options_suffix = params.get("virt_edit_options_suffix")
    status_error = params.get("status_error", "no")
    backup_extension = params.get("virt_edit_backup_extension")
    test_format = params.get("virt_edit_format")

    # virt-edit should not be used when vm is running.
    # (for normal test)
    if vm.is_alive() and start_vm == "no":
        vm.destroy(gracefully=True)

    dom_disk_dict = vm.get_disk_devices()  # TODO
    dom_uuid = vm.get_uuid()
    # Disk format: raw or qcow2
    disk_format = None
    # If object is a disk file path
    is_disk = False

    if vm_ref == "domdisk":
        if len(dom_disk_dict) != 1:
            test.error("Only one disk device should exist on "
                       "%s:\n%s." % (vm_name, dom_disk_dict))
        disk_detail = list(dom_disk_dict.values())[0]
        vm_ref = disk_detail['source']
        logging.info("disk to be edit:%s", vm_ref)
        if test_format:
            # Get format:raw or qcow2
            info = process.run("qemu-img info %s" % vm_ref, shell=True).stdout_text
            for line in info.splitlines():
                comps = line.split(':')
                if comps[0].count("format"):
                    disk_format = comps[-1].strip()
                    break
            if disk_format is None:
                test.error("Cannot get disk format:%s" % info)
        is_disk = True
    elif vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domuuid":
        vm_ref = dom_uuid
    elif vm_ref == "createdimg":
        vm_ref = created_img
        process.run("dd if=/dev/zero of=%s bs=256M count=1" % created_img, shell=True)
        is_disk = True

    # Decide whether pass a exprt for virt-edit command.
    if foo_line != "":
        expr = "s/$/%s/" % foo_line
    else:
        expr = ""

    if backup_extension is not None:
        if options is None:
            options = ""
        options += " -b %s" % backup_extension

    # Stop libvirtd if test need.
    libvirtd = params.get("libvirtd", "on")
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # Run test
    result = lgf.virt_edit_cmd(vm_ref, file_ref, is_disk=is_disk,
                               disk_format=disk_format, options=options,
                               extra=options_suffix, expr=expr,
                               connect_uri=uri, debug=True)
    status = result.exit_status

    # Recover libvirtd.
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    process.run("rm -f %s" % created_img, shell=True)

    # Remove backup file in vm if it exists
    if backup_extension is not None:
        backup_file = file_ref + backup_extension
        cleanup_file_in_vm(test, vm, backup_file)

    status_error = (status_error == "yes")
    if status != 0:
        if not status_error:
            test.fail("Command executed failed.")
    else:
        if (expr != "" and
                (not login_to_check_foo_line(test, vm, file_ref, foo_line))):
            test.fail("Virt-edit to add %s in %s failed."
                      "Test failed." % (foo_line, file_ref))
