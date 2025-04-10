# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from virttest import libvirt_version
from virttest import virsh
from virttest.data_dir import get_data_dir
from virttest.libvirt_xml import vm_xml
from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the ovmf backed nvram.
    1) Prepare a guest with related backed nvram elements.
    2) Start and boot the guest.
    3) Check the dumpxml and the label if necessary.
    """
    def compare_guest_xml(vmxml, os_attrs):
        """
        Compare current xml with the configured values

        :params vmxml: the guest xml
        :params os_attrs: the dict of os xml
        """
        os_xml = vmxml.os
        current_os_attrs = os_xml.fetch_attrs()
        for key in os_attrs:
            if key in current_os_attrs:
                if key == "nvram_attrs":
                    current_os_attrs[key].pop("templateFormat")
                    if "format" not in os_attrs[key]:
                        os_attrs[key]["format"] = "raw"
                if os_attrs[key] != current_os_attrs[key]:
                    test.fail("Configured os xml value {} doesn't match the"
                              " entry {} in guest xml".format(os_attrs[key], current_os_attrs[key]))
            else:
                test.fail("Configured os attributes {} don't existed in guest.".format(key))
        test.log.debug("Get expected os xml of guest.")

    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    source_type = params.get("source_type")
    nvram_file = os.path.join(get_data_dir(), "test.fd")
    nvram_attrs = eval(params.get("nvram_attrs") % source_type)
    os_dict = eval(params.get("os_dict"))
    nvram_source = eval(params.get("nvram_source") % nvram_file)
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP1: prepare the guest xml")
        os_attrs = {**os_dict, **nvram_attrs, **nvram_source}
        guest_os.prepare_os_xml(vm_name, os_attrs, firmware_type)
        test.log.info("TEST_STEP2: start the guest")
        vmxml = guest_os.check_vm_startup(vm, vm_name)
        test.log.info("TEST_STEP3: check the os xml in dumpxml")
        compare_guest_xml(vmxml, os_attrs)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        if os.path.exists(nvram_file):
            os.remove(nvram_file)
        bkxml.sync()
