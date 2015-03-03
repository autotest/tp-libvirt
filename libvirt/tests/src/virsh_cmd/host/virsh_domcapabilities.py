from autotest.client.shared import error
from virttest import libvirt_vm
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    Test the command virsh domcapabilities
    """
    target_uri = params.get("target_uri", "default")
    if target_uri.count("EXAMPLE.COM"):
        raise error.TestNAError("Please replace '%s' with valid uri" %
                                target_uri)
    connect_uri = libvirt_vm.normalize_connect_uri(target_uri)
    virsh_options = params.get("virsh_options", "")
    virttype = params.get("virttype_value", "")
    emulatorbin = params.get("emulatorbin_value", "")
    arch = params.get("arch_value", "")
    machine = params.get("machine_value", "")
    option_dict = {'arch': arch, 'virttype': virttype,
                   'emulatorbin': emulatorbin, 'machine': machine}
    options_list = [option_dict]
    extra_option = params.get("extra_option", "")
    # Get --virttype, --emulatorbin, --arch and --machine values from
    # virsh capabilities output, then assemble option for testing
    # This will ignore the virttype, emulatorbin, arch and machine values
    if virsh_options == "AUTO":
        options_list = []
        capa_xml = capability_xml.CapabilityXML()
        guest_capa = capa_xml.get_guest_capabilities()
        for arch_prop in guest_capa.values():
            arch = arch_prop.keys()[0]
            machine_list = arch_prop[arch]['machine']
            virttype_list = []
            emulatorbin_list = [arch_prop[arch]['emulator']]
            for key in arch_prop[arch].keys():
                if key.startswith("domain_"):
                    virttype_list.append(key[7:])
                    if arch_prop[arch][key].values():
                        emulatorbin_list.append(arch_prop[arch][key]['emulator'])
            for virttype in virttype_list:
                for emulatorbin in emulatorbin_list:
                    for machine in machine_list:
                        option_dict = {'arch': arch,
                                       'virttype': virttype,
                                       'emulatorbin': emulatorbin,
                                       'machine': machine}
                        options_list.append(option_dict)

    # Run test cases
    for option in options_list:
        result = virsh.domcapabilities(virttype=option['virttype'],
                                       emulatorbin=option['emulatorbin'],
                                       arch=option['arch'],
                                       machine=option['machine'],
                                       options=extra_option,
                                       uri=connect_uri,
                                       ignore_status=True,
                                       debug=True)
    # Check status_error
    status_error = "yes" == params.get("status_error", "no")
    utlv.check_exit_status(result, status_error)
