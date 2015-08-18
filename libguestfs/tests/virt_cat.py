import re
from autotest.client.shared import error
import virttest.utils_libguestfs as lgf


def login_to_add_file(vm, file_ref, content):
    """
    Login to add file.
    """

    if not vm.is_alive():
        vm.start()
    try:
        session = vm.wait_for_login()
        session.cmd("echo %s > %s" % (content, file_ref))
        session.close()
    except Exception, detail:
        raise error.TestError("login_to_add_file failed:\n%s" % detail)
    vm.destroy()


def cleanup_file_in_vm(vm, file_ref):
    """
    Remove tmp file in vm.
    """

    if not vm.is_alive():
        vm.start()
    try:
        session = vm.wait_for_login()
        session.cmd("rm -f %s" % file_ref)
        session.close()
    except Exception, detail:
        raise error.TestError("Cleanup failed:\n%s" % detail)
    vm.destroy()


def run(test, params, env):
    """
    Test of virt-cat.

    1) Write a tmp file to guest
    2) Run virt-cat command and get result.
    3) Check result
    4) Delete tmp file
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get("status_error", "no")
    vm_ref = params.get("virt_cat_vm_ref")
    options = params.get("virt_cat_options")

    content = "This is file for test of virt-cat."
    file_ref = params.get("file_ref", "/tmp/virt-cat-test")
    login_to_add_file(vm, file_ref, content)

    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domdisk":
        dom_disk_dict = vm.get_disk_devices()
        if len(dom_disk_dict) != 1:
            raise error.TestError("Only one disk device should exist on "
                                  "%s:\n%s." % (vm_name, dom_disk_dict))
        disk_detail = dom_disk_dict.values()[0]
        vm_ref = disk_detail['source']

    # Run test
    result = lgf.virt_cat_cmd(vm_ref, file_ref, options, debug=True)

    # Check result
    if status_error:
        if not result.exit_status:
            raise error.TestFail("Run successfully with wrong options!")
    else:
        if result.exit_status:
            raise error.TestFail("Cat file failed.")
        else:
            if not re.search(content, result.stdout):
                raise error.TestFail("Catted file do not match")

    # Cleanup
    cleanup_file_in_vm(vm, file_ref)
