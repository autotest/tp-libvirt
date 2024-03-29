import logging
import re

import aexpect

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_libguestfs as lgf


def primary_disk_virtio(vm):
    """
    To verify if system disk is virtio.

    :param vm: Libvirt VM object.
    """
    vmdisks = vm.get_disk_devices()
    if "vda" in list(vmdisks.keys()):
        return True
    return False


def set_guestfs_args(guestfs, ignore_status=True, debug=False, timeout=60):
    """
    Maintain Guestfish class' arguments.
    """
    guestfs.set_ignore_status(ignore_status)
    guestfs.set_debug(debug)
    guestfs.set_timeout(timeout)


def add_disk_or_domain(test, guestfs, disk_or_domain, add_ref="domain",
                       readonly=False):
    """
    Add disk or domain to guestfish

    :param guestfs: a session of guestfish
    :param disk_or_domain: a disk or a domain
    :param add_ref: domain or disk
    :param readonly: is added disk or domain readonly.
    """
    if add_ref == "domain":
        add_result = guestfs.add_domain(disk_or_domain, readonly=readonly)
    elif add_ref == "disk":
        add_result = guestfs.add_drive_opts(disk_or_domain, readonly=readonly)

    if add_result.exit_status:
        guestfs.close_session()
        test.fail("Add %s failed:%s" % (add_ref, add_result))
    logging.debug("Add %s successfully.", add_ref)


def launch_disk(test, guestfs):
    # Launch added disk or domain
    launch_result = guestfs.run()
    if launch_result.exit_status:
        guestfs.close_session()
        test.fail("Launch failed:%s" % launch_result)
    logging.debug("Launch successfully.")


def get_root(test, guestfs):
    getroot_result = guestfs.inspect_os()
    roots_list = getroot_result.stdout.splitlines()
    if getroot_result.exit_status or not len(roots_list):
        guestfs.close_session()
        test.fail("Get root failed:%s" % getroot_result)
    return roots_list[0]


def mount_filesystem(test, guestfs, filesystem, mountpoint):
    mount_result = guestfs.mount(filesystem, mountpoint)
    if mount_result.exit_status:
        guestfs.close_session()
        test.fail("Mount filesystem failed:%s" % mount_result)
    logging.debug("Mount filesystem successfully.")


def run(test, params, env):
    """
    Test of built-in 'add-xxx' commands in guestfish.

    1) Get parameters for test
    2) Set options for commands
    3) Run key commands:
       a.add disk or domain with readonly or not
       b.launch
       c.mount root device
    4) Write a file to help result checking
    5) Check result
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Get parameters
    add_ref = params.get("guestfs_add_ref", "domain")
    add_readonly = "yes" == params.get("guestfs_add_readonly", "no")
    status_error = "yes" == params.get("status_error", "no")
    login_to_check = "yes" == params.get("login_to_check_write", "no")
    start_vm = "yes" == params.get("start_vm", "no")
    # Any failed info will be recorded in this dict
    # Result check will rely on it.
    fail_flag = 0
    fail_info = {}

    if vm.is_alive() and not start_vm:
        vm.destroy()

    if add_ref == "domain":
        disk_or_domain = vm_name
    elif add_ref == "disk":
        # Get system disk path of tested domain
        disks = vm.get_disk_devices()
        if len(disks):
            disk = list(disks.values())[0]
            disk_or_domain = disk['source']
        else:
            # No need to test since getting vm's disk failed.
            test.fail("Can not get disk of %s" % vm_name)
    else:
        # If adding an unknown disk or domain
        disk_or_domain = add_ref
        add_ref = "disk"

    guestfs = lgf.GuestfishPersistent()
    set_guestfs_args(guestfs)

    add_error = params.get("guestfs_add_error", "no")
    # Add tested disk or domain
    try:
        add_disk_or_domain(test, guestfs, disk_or_domain, add_ref, add_readonly)
    except exceptions.TestFail as detail:
        guestfs.close_session()
        if add_error:
            logging.debug("Add failed as expected:%s", str(detail))
            return
        raise

    # Launch added disk or domain
    launch_disk(test, guestfs)

    # Mount root filesystem
    root = get_root(test, guestfs)
    mount_filesystem(test, guestfs, root, '/')

    # Write content to file
    status, content = process.getstatusoutput("uuidgen")
    write_result = guestfs.write("/guestfs_temp", content)
    if write_result.exit_status:
        fail_flag = 1
        fail_info['write_content'] = ("Write content to file failed:"
                                      "%s" % write_result)
    else:
        logging.debug("Write content to file successfully.")
        fail_info['write_content'] = "Write content to file successfully."

    # Check writed file in a new guestfish session
    guestfs.new_session()
    set_guestfs_args(guestfs)
    add_disk_or_domain(test, guestfs, disk_or_domain, add_ref, add_readonly)
    launch_disk(test, guestfs)
    mount_filesystem(test, guestfs, root, '/')
    cat_result = guestfs.cat("/guestfs_temp")
    if cat_result.exit_status:
        fail_flag = 1
        fail_info['cat_writed'] = ("Cat writed file failed:"
                                   "%s" % cat_result)
    else:
        guestfs_writed_text = cat_result.stdout
        if not re.search(content, guestfs_writed_text):
            fail_flag = 1
            fail_info['cat_writed'] = ("Catted text is not match with writed:"
                                       "%s" % cat_result)
            logging.debug("Catted text is not match with writed")
        else:
            logging.debug("Cat content of file successfully.")
            fail_info['cat_writed'] = "Cat content of file successfully."

    # Start vm and login to check writed file.
    guestfs.close_session()
    # Convert sdx in root to vdx for virtio system disk
    if primary_disk_virtio(vm):
        root = process.run("echo %s | sed -e 's/sd/vd/g'" % root,
                           ignore_status=True, shell=True).stdout_text.strip()
    if login_to_check:
        try:
            vm.start()
            session = vm.wait_for_login()
            session.cmd("mount %s /mnt" % root)
            try:
                login_wrote_text = session.cmd_output("cat /mnt/guestfs_temp; echo",
                                                      timeout=5)
            except aexpect.ShellTimeoutError as detail:
                # written content with guestfs.write won't contain line break
                # Is is a bug of guestfish.write?
                login_wrote_text = str(detail)
            if not re.search(content, login_wrote_text):
                fail_flag = 1
                fail_info['login_to_check'] = ("Login to check failed:"
                                               "%s" % login_wrote_text)
            else:
                logging.debug("Login to check successfully.")
                fail_info['login_to_check'] = "Login to check successfully."
            session.close()
        except aexpect.ShellError as detail:
            fail_flag = 1
            fail_info['login_to_check'] = detail
        vm.destroy()

    if status_error:
        if not fail_flag:
            test.fail("Expected error is successful:"
                      "%s" % fail_info)
    else:
        if fail_flag:
            test.fail(fail_info)
