#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import platform
import re

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def _guest_is_aarch64_arm64(params, host_arch):
    """
    Guest can be arm64 while Avocado runs on x86_64 (vt-machine-type arm64-mmio).
    Do not rely only on platform.machine().
    """
    if host_arch == "aarch64":
        return True
    arch = (params.get("arch") or params.get("vm_arch_name") or "").lower()
    if arch in ("aarch64", "arm64"):
        return True
    mt = (params.get("machine_type") or "").lower()
    if "arm64" in mt or "aarch64" in mt:
        return True
    shortname = (params.get("shortname") or "").lower()
    if "aarch64" in shortname or "arm64" in shortname:
        return True
    return False


# Match ./os/boot[@dev='hd'] or ./os/boot[@dev="hd"] with optional whitespace.
_LEGACY_OS_BOOT_HD = re.compile(
    r"^\s*\./os/boot\[@dev=(['\"])hd\1\]\s*$",
)


def _adapt_os_xpath_uefi_disk_boot(os_xpath, params, host_arch):
    """
    UEFI AArch64 guests expose boot order on the disk (<boot order='1'/>),
    not <os><boot dev='hd'/>.
    """
    if not _guest_is_aarch64_arm64(params, host_arch):
        return os_xpath

    disk_boot = ".//disk[@device='disk']/boot[@order='1']"

    def _map_one(xp):
        if not isinstance(xp, str):
            return xp
        if _LEGACY_OS_BOOT_HD.match(xp):
            return disk_boot
        return xp

    def _walk(node):
        if isinstance(node, str):
            return _map_one(node)
        if isinstance(node, (list, tuple)):
            return type(node)(_walk(i) for i in node)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    return _walk(os_xpath)


def run(test, params, env):
    """
    This case is to verify vm boot without some default os attributes.
    1) Prepare a guest xml without some default os attributes.
    2) Start the guest and check the guest status.
    3) Check the guest dumpxml.
    """
    vm_name = guest_os.get_vm(params)
    os_dict = eval(params.get("os_dict"))
    host_arch = platform.machine()
    os_xpath = _adapt_os_xpath_uefi_disk_boot(
        eval(params.get("os_xpath")), params, host_arch)
    test.log.info(
        "without_boot_dev: host_arch=%s guest_aarch64_like=%s os_xpath=%r",
        host_arch,
        _guest_is_aarch64_arm64(params, host_arch),
        os_xpath,
    )

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_os()
        vmxml.setup_attrs(os=os_dict)
        vmxml = guest_os.check_vm_startup(vm, vm_name)
        test.log.debug(f"The guest xml is {vmxml}")
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, os_xpath)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name, debug=True)
        bkxml.sync()
