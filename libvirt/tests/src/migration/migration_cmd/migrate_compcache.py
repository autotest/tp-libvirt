# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh

from virttest.utils_test import libvirt


def run(test, params, env):
    """
    To verify that memory compression cache size can be set/got by
    migrate-compcache.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    compression_cache = params.get("compression_cache")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")
    vm_status = params.get("vm_status")
    vm = env.get_vm(vm_name)

    test.log.info("Run test for migrate-compcache/migrate-compcache.")

    ret = virsh.migrate_compcache(vm_name, debug=True)
    orig_size = None
    if ret.exit_status:
        if vm_status == "vm_shutoff":
            libvirt.check_result(ret, err_msg)
        else:
            test.fail(f"Failed to get compression cache size: {ret.stdout_text}")
    else:
        orig_size = ret.stdout_text.strip().split(":")[-1]

    ret = virsh.migrate_compcache(vm_name, size=compression_cache, debug=True)
    if ret.exit_status:
        if status_error:
            libvirt.check_result(ret, err_msg)
        else:
            test.fail(f"Failed to set compression cache size: {ret.stdout_text}")

    if vm_status != "vm_shutoff":
        new_size = virsh.migrate_compcache(vm_name, debug=True).stdout_text.strip().split(":")[-1]
        if status_error:
            if orig_size.split()[0].strip() != new_size.split()[0].strip():
                test.fail(f"The compression cache size shouldn't have been modified but found {new_size}")
            else:
                test.log.info("The compression cache size remains the same as before.")
        else:
            value = new_size.split()[0].strip()
            unit = new_size.split()[-1].strip()
            value = int(float(value))
            if unit == "KiB":
                size = int(int(compression_cache) / 1024)
            elif unit == "MiB":
                size = int(int(compression_cache) / 1048576)
            elif unit == "GiB":
                size = int(int(compression_cache) / 1073741824)
            else:
                test.fail(f"Unexpected unit '{unit}' in compression cache size")
            if value != size:
                test.fail(f"Failed to set the compression cache size: expected {size}, actual {value}")
            else:
                test.log.info("The compression cache size was set successfully.")
