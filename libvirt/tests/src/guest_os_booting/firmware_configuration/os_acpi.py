#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os
import re

from avocado.utils import process
from avocado.utils.download import url_download

from virttest import data_dir
from virttest import utils_package

from virttest.libvirt_xml import vm_xml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the os acpi element.
    1) Prepare a guest with os acpi.
    2) Start the guest.
    3) Compare the SLIC result of guest and host.
    """
    vm_name = params.get("main_vm")
    acpi_url = params.get("acpi_url")
    acpi_file = params.get("acpi_file")
    cmd_in_guest = params.get("cmd_in_guest")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    def get_guest_result():
        """
        Filter the SLIC part from the complicated acpi result.
        """
        session = vm.wait_for_login()
        utils_package.package_install(["acpica-tools"], session, 360)
        status, guest_output = session.cmd_status_output(cmd_in_guest)
        if status != 0:
            test.fail(f"Run {cmd_in_guest} command failed!")
        lines = guest_output.splitlines()
        # Search the line includes "SCLI @" from back to front and get the
        # last three lines.
        slic_lines = []
        for line in reversed(lines):
            if "SLIC @" in line:
                break
            slic_lines.append(line)
        need_slic_lines = slic_lines[-3:]
        need_slic_lines.reverse()
        return need_slic_lines

    try:
        test.log.info("TEST_SETUP: Download the slice table to use.")
        slic_dat = os.path.join(data_dir.get_data_dir(), "%s" % acpi_file)
        url_download(acpi_url, slic_dat)

        test.log.info("TEST_STEP1: prepare a running guest with acpi")
        acpi_dict = eval(params.get("acpi_dict", "{}") % slic_dat)
        guest_os.prepare_os_xml(vm_name, acpi_dict)
        guest_os.check_vm_startup(vm, vm_name)

        test.log.info("TEST_STEP2: compare the slic result between host and guest")
        guest_result = get_guest_result()
        test.log.info(f"The guest result is {guest_result}.")
        cmd_in_host = params.get("cmd_in_host") % slic_dat
        host_result = process.run(cmd_in_host, ignore_status=False, shell=True).stdout_text
        test.log.info(f"The host result is {host_result}.")
        pattern = r'00+|[^0-9A-Za-z]'
        host_slic = re.sub(pattern, '', host_result[:-9])
        guest_slic = re.sub(pattern, '', str(guest_result))
        if host_slic.upper() == guest_slic.upper():
            test.log.debug("The slic between host and guest are same.")
        else:
            test.fail("The slic result in guest is different with host.")
    finally:
        bkxml.sync()
        if os.path.exists(slic_dat):
            os.remove(slic_dat)
