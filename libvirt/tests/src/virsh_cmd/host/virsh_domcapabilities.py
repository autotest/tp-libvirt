from six import itervalues

from virttest import ssh_key
from virttest import libvirt_vm
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    Test the command virsh domcapabilities
    """
    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))
    remote_ref = params.get("remote_ref", "")

    if remote_ref == "remote":
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", None)

        if 'EXAMPLE.COM' in remote_ip:
            test.cancel("Please replace '%s' with valid remote ip" % remote_ip)

        ssh_key.setup_ssh_key(remote_ip, "root", remote_pwd)
        connect_uri = libvirt_vm.complete_uri(remote_ip)

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
        for arch_prop in list(itervalues(guest_capa)):
            for arch in list(arch_prop.keys()):
                machine_list = arch_prop[arch]['machine']
                virttype_list = []
                emulatorbin_list = [arch_prop[arch]['emulator']]
                for key in list(arch_prop[arch].keys()):
                    if key.startswith("domain_"):
                        virttype_list.append(key[7:])
                        if list(itervalues(arch_prop[arch][key])):
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
