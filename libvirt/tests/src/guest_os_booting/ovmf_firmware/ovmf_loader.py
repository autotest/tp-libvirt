#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#

import os
import shutil
import tempfile

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_sys
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def _replace_path_in_obj(obj, old, new):
    if old == new or not old:
        return obj
    if isinstance(obj, dict):
        return {k: _replace_path_in_obj(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_path_in_obj(i, old, new) for i in obj]
    if isinstance(obj, str) and obj == old:
        return new
    return obj


def _resolve_custom_loader_path(custom_loader_path, loader_dict, test):
    """
    Params often use /usr/share/edk2/ovmf/... (x86 layout). On aarch64 that
    tree may be missing or unwritable — use Avocado data dir or /tmp.
    """
    if not custom_loader_path:
        return custom_loader_path, loader_dict
    dest_dir = os.path.dirname(custom_loader_path)
    usable = (
        dest_dir
        and os.path.isdir(dest_dir)
        and os.access(dest_dir, os.W_OK)
    )
    if usable:
        return custom_loader_path, loader_dict
    for alt in (
        os.path.join(data_dir.get_data_dir(), "images", os.path.basename(custom_loader_path)),
        os.path.join(tempfile.gettempdir(), os.path.basename(custom_loader_path)),
    ):
        parent = os.path.dirname(alt)
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            continue
        if os.access(parent, os.W_OK):
            test.log.info(
                "custom_loader_path %r not usable (dir=%r); using %s instead",
                custom_loader_path,
                dest_dir,
                alt,
            )
            loader_dict = _replace_path_in_obj(loader_dict, custom_loader_path, alt)
            return alt, loader_dict
    return custom_loader_path, loader_dict


def create_custom_loader(test, loader_path, new_custom_path):
    """
    Create a customized OVMF loader by copying an existing one.
    :param loader_path: Absolute path to the source OVMF loader file.
    :param new_custom_path: Absolute path where the copy will be placed.
    """
    try:
        parent = os.path.dirname(new_custom_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(loader_path, new_custom_path)
        test.log.debug("ovmf lodader copied to the custom path %s", new_custom_path)
    except OSError as err:
        test.fail("failed to create the custom ovmf loader path: %s" % err)


def run(test, params, env):
    """
    This case is to verify the ovmf loader elements.
    1) Prepare a guest with related os loader xml.
    2) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    loader_dict = eval(params.get("loader_dict", "{}"))
    loader_xpath = eval(params.get("loader_xpath", "[]"))
    smm_state = params.get("smm_state")
    error_msg = params.get("error_msg")
    incorrect_loader_path = params.get("incorrect_loader_path", "")
    use_file = "yes" == params.get("use_file", "no")
    stateless = "yes" == params.get("stateless", "no")
    custom_loader_path = params.get("custom_loader_path", "")
    custom_loader_path, loader_dict = _resolve_custom_loader_path(
        custom_loader_path, loader_dict, test)
    loader_path = params.get("loader_path")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Check if image mode
        if loader_dict.get("loader_readonly") == "no" and utils_sys.is_image_mode():
            test.cancel("The image mode filesystem is read only so {'loader_readonly': 'no'} is not supported")

        if smm_state:
            guest_os.prepare_smm_xml(vm_name, smm_state, "")
        if "nvram" in loader_dict:
            loader_dict["nvram"] = loader_dict["nvram"].replace("nvram_VARS", f"{vm_name}_VARS")
        vmxml = guest_os.prepare_os_xml(vm_name, loader_dict, firmware_type)
        if custom_loader_path:
            create_custom_loader(test, loader_path, custom_loader_path)
        # stateless='yes' only use for AMD test, so here we only check the dumpxml for it to avoid the machine issue
        if stateless:
            virsh.start(vm_name, debug=True)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, loader_xpath)
        else:
            if use_file:
                fd = os.open(incorrect_loader_path, os.O_CREAT)
                os.close(fd)
            guest_os.check_vm_startup(vm, vm_name, error_msg)
    finally:
        bkxml.sync()
        if os.path.exists(incorrect_loader_path):
            os.remove(incorrect_loader_path)
        if os.path.exists(custom_loader_path):
            os.remove(custom_loader_path)
            test.log.debug("cleaned up %s", custom_loader_path)
