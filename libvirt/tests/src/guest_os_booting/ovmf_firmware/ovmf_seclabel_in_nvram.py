#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from avocado.utils import process

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
        :params os_attrs: the os attributes dict
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

    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    nvram_file = os.path.join(get_data_dir(), "test.fd")
    nvram_attrs = eval(params.get("nvram_attrs"))
    os_dict = eval(params.get("os_dict"))
    seclabel_label = params.get("seclabel_label")
    seclabel_model = params.get("seclabel_model")
    error_msg = params.get("error_msg", "")
    without_label = "yes" == params.get("without_label", "no")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if without_label:
            nvram_source = eval(params.get("nvram_source") % (seclabel_model, nvram_file))
        else:
            nvram_source = eval(params.get("nvram_source") % (seclabel_label, seclabel_model, nvram_file))
        os_attrs = {**os_dict, **nvram_attrs, **nvram_source}
        guest_os.prepare_os_xml(vm_name, os_attrs, firmware_type)
        vmxml = guest_os.check_vm_startup(vm, vm_name, error_msg)
        if error_msg:
            return
        test.log.info("Check the os xml in dumpxml")
        compare_guest_xml(vmxml, os_attrs)
        test.log.info("Check the nvram file label in host")
        label_result = process.run("ls -lZ {}".format(nvram_file)).stdout_text
        if seclabel_model == "dac":
            seclabel_label = seclabel_label.replace(":", " ")
        if seclabel_label in label_result:
            test.log.info("Get expected nvram file label: {}".format(label_result))
        else:
            test.fail("The nvram file label {} is not expected".format(label_result))
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        if os.path.exists(nvram_file):
            os.remove(nvram_file)
        bkxml.sync()
