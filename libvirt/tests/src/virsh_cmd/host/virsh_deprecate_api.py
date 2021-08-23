import json
import logging
import re

from virttest import libvirt_version
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh

import xml.etree.ElementTree as ET


def get_deprecated_name_list_qmp(vm_name, cmd):
    """
    Get the list of deprecated items by executing given QMP command.

    :param vm_name: the VM name to communicate with.
    :param cmd: QMP command to execute.
    :return: List of deprecated items.
    """
    res = virsh.qemu_monitor_command(vm_name, cmd)
    jdata = json.loads(res.stdout_text)
    qmp_deprecated = []
    for data in jdata['return']:
        for key in data:
            if key == "name":
                name = data[key]
            if key == "deprecated" and data[key]:
                qmp_deprecated.append(name)
    logging.debug("List of deprecated items per QMP: {}".format(qmp_deprecated))
    return qmp_deprecated


def get_deprecated_domain_capabilities_list(function, tree):
    """
    Get the list of deprecated items by executing virsh (dom)capabilities.

    :param function: Name of the virsh function to execute [(dom)capabilities].
    :param tree: Path in the function XML output to look for.
    :return: List of deprecated items.
    """
    domain_deprecated = []
    res = function()
    if isinstance(res, str):
        xml = ET.fromstring(res)
    else:
        xml = ET.fromstring(res.stdout_text)
    models_list = (xml.findall(tree))
    for model in models_list:
        if model.get('deprecated') == 'yes':
            domain_deprecated.append(model.text)
    logging.debug("List of deprecated items per (dom)capabilities: {}"
                  .format(domain_deprecated))
    return domain_deprecated


def check_deprecated_output(test, qmp_list, domain_list):
    """
    Check if the output of the QMP command and virsh (dom)capabilities
    corresponds with each other.

    :param test: Test instance.
    :param qmp_list: List of deprecated items from the QMP command
    :param domain_list: List of deprecated items from the virsh (dom)capabilities
    """
    for deprecated in domain_list:
        if deprecated not in qmp_list:
            test.fail("Domain deprecated cpu/machine type: {} not found in the "
                      "QMP deprecated cpu/machine type list: {}.".
                      format(deprecated, qmp_list))


def prepare_deprecated_vm_xml_and_provide_deprecated_list(params, deprecated_vm):
    """
    Prepare the VM xml with deprecated features and return their list

    :param params: Params dictionary from the test.
    :param deprecated_vm: The VM to be updated with deprecated features.
    :return: List of deprecated items used in the updated VM
    """
    # Get the lists
    domain_cpu_tree = params.get("domain_cpu_tree")
    domain_machine_tree = params.get("domain_machine_tree")
    domain_cpu_list = get_deprecated_domain_capabilities_list(
        virsh.domcapabilities, domain_cpu_tree)
    domain_type_list = get_deprecated_domain_capabilities_list(
        virsh.capabilities, domain_machine_tree)
    deprecated_list = []
    if domain_cpu_list:
        vmcpuxml = libvirt_xml.vm_xml.VMCPUXML()
        # Use the first deprecated cpu from list
        vmcpuxml.model = domain_cpu_list[0]
        vmcpuxml.check = 'none'
        deprecated_vm.cpu = vmcpuxml
        deprecated_list.append(domain_cpu_list[0])
    if domain_type_list:
        # Use the first deprecated machine type from list
        deprecated_vm.os.machine = domain_type_list[0]
        deprecated_list.append(domain_type_list[0])
    return deprecated_list


def check_dominfo(test, vm_name, deprecated_list, empty=False):
    """
    Check a virsh dominfo for a 'Messages' section and particular deprecated
    features.

    :param test: Test instance.
    :param vm_name: Name of the VM to be checked for dominfo
    :param deprecated_list: List of deprecated items expected in dominfo.
    :param empty: Flag used for checking the Messages section, for no Messages
    section is set to True.
    """
    res = virsh.dominfo(vm_name)
    tainted_message = "tainted: use of deprecated configuration settings"
    if tainted_message not in res.stdout_text:
        if empty:
            logging.debug("No Messages are found in dominfo output as expected.")
        else:
            test.fail("There is no tainted deprecated messsage: {} in dominfo "
                      "output: {}".format(tainted_message, res.stdout_text))
    else:
        if empty:
            test.fail("Tainted deprecated message: '{}' found in dominfo: {},"
                      "but no Messages output is expected in dominfo.".
                      format(tainted_message, res.stdout_text))
        else:
            logging.debug("Tainted deprecated message: '{}' found in dominfo.".
                          format(tainted_message))
    if not empty:
        for item in deprecated_list:
            deprecated_message = "deprecated configuration:.*'{}'".format(item)
            found = False
            for line in res.stdout_text.split('\n'):
                if re.search(deprecated_message, line):
                    found = True
                    break
            if not found:
                test.fail("There is no deprecated configuration: {} found in "
                          "dominfo output: {}".
                          format(deprecated_message, res.stdout_text))
            else:
                logging.debug("Deprecated configuration: {} found in dominfo "
                              "output.".format(deprecated_message))


def run(test, params, env):
    """
    Test the libvirt API to report deprecation status of machine-types and
    devices.
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    deprecated_domain = params.get("deprecated_domain", "no") == "yes"
    check = params.get("check", "no") == "yes"
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    deprecated_vm = backup_xml.copy()

    try:
        vm.start()
        vm.wait_for_login().close()
        if check:
            qmp_cmd = params.get("qmp_cmd")
            domain_tree = params.get("domain_tree")
            virsh_function = params.get("virsh_function")
            # Get a list of deprecated CPU architectures/machine types by
            # executing QMP command
            qmp_list = get_deprecated_name_list_qmp(vm_name, qmp_cmd)
            # and (dom)capabilities
            domain_list = get_deprecated_domain_capabilities_list(
                eval(virsh_function), domain_tree)
            check_deprecated_output(test, qmp_list, domain_list)

        if deprecated_domain:
            deprecated_list = prepare_deprecated_vm_xml_and_provide_deprecated_list(params, deprecated_vm)
            if not deprecated_list:
                test.cancel("There is no deprecated cpu or machine type in "
                            "current qemu version, skipping the test.")
            # No "Messages" in the output since the default VM is still running.
            check_dominfo(test, vm_name, deprecated_list, empty=True)
            vm.destroy()
            # Update VM with a deprecated items and check dominfo
            deprecated_vm.sync()
            logging.debug("vm xml is %s", deprecated_vm)
            vm.start()
            vm.wait_for_login().close()
            check_dominfo(test, vm_name, deprecated_list)
            # Reboot the VM and check a dominfo again
            vm.reboot()
            check_dominfo(test, vm_name, deprecated_list)
            # Restart libvirtd and check a dominfo again
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            check_dominfo(test, vm_name, deprecated_list)
            # Save a VM to file and check a dominfo - No "Messages"
            deprecated_vm_file = "deprecated_vm"
            vm.save_to_file(deprecated_vm_file)
            check_dominfo(test, vm_name, deprecated_list, empty=True)
            # Restore a VM from file and check dominfo
            vm.restore_from_file(deprecated_vm_file)
            check_dominfo(test, vm_name, deprecated_list)
            # Destroy VM and check dominfo - No "Messages"
            vm.destroy()
            check_dominfo(test, vm_name, deprecated_list, empty=True)
            # Start the VM and shut it down internally - No "Messages" in
            # dominfo output
            vm.start()
            session = vm.wait_for_login()
            utils_misc.cmd_status_output("shutdown now", session=session)
            utils_misc.wait_for(lambda: vm.state() == 'shut off', 60)
            check_dominfo(test, vm_name, deprecated_list, empty=True)

    except Exception as e:
        test.error('Unexpected error: {}'.format(e))
    finally:
        if vm.is_alive:
            vm.destroy()
        backup_xml.sync()
