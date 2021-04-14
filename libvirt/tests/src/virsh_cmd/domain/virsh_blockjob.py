import os
import time
import logging

from virttest import data_dir
from virttest import utils_libvirtd
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utl


def finish_job(vm_name, target, timeout, test):
    """
    Make sure the block copy job finish.

    :param vm_name: Domain name
    :param target: Domain disk target dev
    :param timeout: Timeout value
    """
    job_time = 0
    while job_time < timeout:
        if utl.check_blockjob(vm_name, target, "progress", "100"):
            logging.debug("Block job progress up to 100%.")
            break
        else:
            job_time += 2
            time.sleep(2)
    if job_time >= timeout:
        test.fail("Blockjob timeout in %s sec." % timeout)


def get_disk(vm_name, test):
    """
    Get a disk target dev of the VM.

    :param vm_name: Domain name
    :return: Disk target dev
    """
    disks = vm_xml.VMXML.get_disk_blk(vm_name)
    dev = ""
    try:
        dev = disks[0]
        logging.debug("Use %s of domain %s to do testing.", dev, vm_name)
    except IndexError:
        test.fail("No disk in domain %s." % vm_name)
    return dev


def check_disk(vm_name, disk, test):
    """
    Check if given disk exist in VM.

    :param vm_name: Domain name.
    :param disk: Domian disk source path or darget dev.
    :return: True/False
    """
    if vm_xml.VMXML.check_disk_exist(vm_name, disk):
        logging.debug("Find %s in domain %s.", disk, vm_name)
    else:
        test.fail("Can't find %s in domain %s." % (disk, vm_name))


def run(test, params, env):
    """
    Test command: virsh blockjob.

    This command can manage active block operations.
    1. Positive testing
        1.1 Query active block job for the specified disk.
        1.2 Manager the active block job(cancle/pivot).
        1.3 Adjust speed for the active block job.
    2. Negative testing
        2.1 Query active block job for a invalid disk.
        2.2 Invalid bandwith test.
        2.3 No active block job management.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    options = params.get("blockjob_options", "")
    bandwidth = params.get("blockjob_bandwidth", "")
    no_blockjob = "yes" == params.get("no_blockjob", "no")
    invalid_disk = params.get("invalid_disk")
    persistent_vm = "yes" == params.get("persistent_vm", "no")
    status_error = "yes" == params.get("status_error", "no")
    blockcopy_options = params.get("blockjob_under_test_options", "")

    target = get_disk(vm_name, test)
    if not target:
        test.fail("Require target disk to copy.")

    # Prepare transient/persistent vm
    original_xml = vm.backup_xml()
    if not persistent_vm and vm.is_persistent():
        vm.undefine()
    elif persistent_vm and not vm.is_persistent():
        vm.define(original_xml)

    #Create a block job, e.g.: blockcopy
    tmp_file = time.strftime("%Y-%m-%d-%H.%M.%S.img")
    dest_path = os.path.join(data_dir.get_tmp_dir(), tmp_file)
    if not no_blockjob:
        cmd_result = virsh.blockcopy(vm_name, target, dest_path, blockcopy_options,
                                     ignore_status=True, debug=True)
        status = cmd_result.exit_status
        if status != 0:
            test.error("Fail to create blockcopy job.")
        # This option need blockjcopy job finish first
        if options.count("--pivot"):
            #Set default blockcopy timeout to 300 sec
            timeout = 300
            finish_job(vm_name, target, timeout, test)

    if len(bandwidth):
        options += "--bandwidth %s" % bandwidth

    if invalid_disk:
        target = invalid_disk

    # Wait for few seconds to be more like human activity,
    # otherwise, unexpected failure may happen.
    time.sleep(3)
    # Run virsh blockjob command
    cmd_result = virsh.blockjob(vm_name, target, options,
                                ignore_status=True, debug=True)
    err = cmd_result.stderr.strip()
    status = cmd_result.exit_status

    # Check result
    if not utils_libvirtd.libvirtd_is_running():
        test.fail("Libvirtd service is dead.")
    try:
        if not status_error:
            if status == 0:
                #'abort' option check
                if options.count("--abort"):
                    utl.check_blockjob(vm_name, target, "no_job", 0)
                #'pivot' option check
                if options.count("--pivot"):
                    if utl.check_blockjob(vm_name, target, "no_job", 0):
                        check_disk(vm_name, dest_path, test)
                #'bandwidth' option check
                if options.count("--bandwidth"):
                    utl.check_blockjob(vm_name, target, "bandwidth", bandwidth)
            else:
                test.fail(err)
        else:
            if status:
                logging.debug("Expect error: %s", err)
            else:
                test.fail("Expect fail, but run successfully.")
        #cleanup
    finally:
        try:
            if vm.exists():
                vm.destroy()
            else:
                test.fail("Domain is disappeared.")
        finally:
            vm.define(original_xml)
            if os.path.exists(dest_path):
                os.remove(dest_path)
