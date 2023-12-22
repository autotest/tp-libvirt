# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
import re

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verity the smbios feature of os.
    1) Prepare a guest xml with smbios features.
    2) Start the guest and login it.
    3) Check smbios data in guest and compare it with expected one.
    """
    def check_sysinfo_mode_result():
        """
        Get the expected smbios data
        """
        sysinfo_elements = [bios_entry, system_entry, baseBoard_entry,
                            chassis_entry, oemStrings_entry]
        for item in sysinfo_elements:
            item_key = list(item)[0]
            item_value = item[item_key]
            expected_smbios_data = {}
            for i in range(len(item_value)):
                if item_key == "oemStrings_entry":
                    expected_smbios_data.update({i: item_value[i]['entry']})
                else:
                    expected_smbios_data.update({item_value[i]['entry_name']: item_value[i]['entry']})
            # Check sysinfo in guest
            entry_type = item_key.split('_')[0]
            compare_smbios_data(entry_type, expected_smbios_data)

    def get_smbios_data(entry_type, smbios_output):
        """
        Get the smbios data for host or guest.

        :params entry_type: the entry type of smbios data
        :params smbios_output: the output of dmidecode smbios data
        :return: return the smbios data for from guest
        """
        smbios_data = {}
        for line in str(smbios_output).splitlines():
            line = line.strip()
            # Exclude lines that cannot be parsed into dict
            pattern = re.compile(r'^(.*?):\s+(.*)$')
            match = pattern.match(line)
            if match:
                key, value = match.groups()
                smbios_data[key] = value
        return smbios_data

    def get_expected_smbios_data(vmxml, smbios_mode):
        """
        Check the hardware and firmware info in guest by dmidecode.

        :params vmxml: the xml of guest
        :params smbios_mode: the testing smbios mode
        :return: a tuple with entry_type and expected_smbios_data
        """
        entry_type = "system"
        if smbios_mode == "emulate":
            expected_smbios_data = eval(params.get("expected_system_info") % vmxml.uuid)
        else:
            dmidecode_cmd = "dmidecode -t %s" % entry_type
            host_system = process.run(dmidecode_cmd).stdout_text
            system_info = get_smbios_data(entry_type, host_system)
            expected_smbios_data = eval(params.get("expected_system_info") %
                                        (system_info["Manufacturer"], system_info["Product Name"],
                                         system_info["SKU Number"], system_info["Family"]))
        return entry_type, expected_smbios_data

    def compare_smbios_data(entry_type, expected_smbios_data):
        """
        Compare the smbios data for from guest with the guest xml

        :params entry_type: the entry type of smbios data
        :params expected_smbios_data: the expected smbios data got from guest xml
        """
        if entry_type == "oemStrings":
            dmidecode_cmd = "dmidecode -t 11"
        else:
            dmidecode_cmd = "dmidecode -t %s" % entry_type
        session = vm.wait_for_login()
        smbios_output = session.cmd_status_output(dmidecode_cmd)
        smbios_data = get_smbios_data(entry_type, smbios_output[1])
        session.close()
        test.log.debug(f"The smbios data of guest for {smbios_mode} mode is:"
                       f"{smbios_data}")
        # The keys are not same for sysinfo smbios mode. For example, "date" and "Release Date".
        # So here use length to check the matched status.
        match_values = {}
        test.log.debug(f"The expected smbios data for {smbios_mode} is: {expected_smbios_data}")
        for key, value in expected_smbios_data.items():
            for actual_value in smbios_data.values():
                if value == actual_value:
                    match_values[key] = actual_value
        if len(match_values) != len(expected_smbios_data):
            test.fail(f"The {smbios_data} is not matched with the expected data {expected_smbios_data}.")
        else:
            test.log.debug(f"The smbios data of {entry_type} in guest is expected.")

    vm_name = params.get("main_vm")
    os_dict = eval(params.get("os_dict", "{}"))
    smbios_mode = params.get("smbios_mode")
    error_msg = params.get("error_msg", "")
    with_sysinfo = "yes" == params.get("with_sysinfo", "no")
    type_entry = eval(params.get("sysinfo_type", "{}"))
    bios_entry = eval(params.get("sysinfo_bios", "{}"))
    baseBoard_entry = eval(params.get("sysinfo_baseBoard", "{}"))
    chassis_entry = eval(params.get("sysinfo_chassis", "{}"))
    oemStrings_entry = eval(params.get("sysinfo_oemStrings", "{}"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Prepare sysinfo xml if necessary
        sysinfo_dict = {}
        if smbios_mode == "sysinfo":
            system_entry = eval(params.get("sysinfo_system") % vmxml.uuid)
        else:
            system_entry = eval(params.get("sysinfo_system", "{}"))
        if with_sysinfo:
            sysinfo_dict = {**type_entry, **bios_entry, **system_entry,
                            **baseBoard_entry, **chassis_entry, **oemStrings_entry}
            sysinfo_xml = vm_xml.VMSysinfoXML()
            sysinfo_xml.setup_attrs(**sysinfo_dict)
            vmxml.sysinfo = sysinfo_xml
        # Prepare os xml with smbios
        try:
            vmxml.setup_attrs(os=os_dict)
            vmxml.sync()
            virsh.dumpxml(vm_name, debug=True)
        except xcepts.LibvirtXMLError as details:
            if error_msg and error_msg in str(details):
                test.log.info("Get expected error message: %s.", error_msg)
                return
            else:
                test.fail("Failed to define the guest because error:%s" % str(details))
        if error_msg:
            guest_os.check_vm_startup(vm, vm_name, error_msg)
            return
        else:
            guest_os.check_vm_startup(vm, vm_name)
            test.log.debug(f"The current dumpxml is {vmxml}")
            if smbios_mode == "sysinfo":
                check_sysinfo_mode_result()
            else:
                entry_type, expected_smbios_data = get_expected_smbios_data(vmxml, smbios_mode)
                compare_smbios_data(entry_type, expected_smbios_data)
    finally:
        if vm.is_alive():
            vm.destroy()
        bkxml.sync()
