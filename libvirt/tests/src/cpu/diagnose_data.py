import logging
import re

from virttest.libvirt_xml.vm_xml import VMXML, VMFeaturesXML
from virttest import virsh


def run(test, params, env):
    """
    Test limits on the number of vcpus and recorded diagnose data

    :param test: test object
    :param params: Dict with the test parameters
    :param env: Dict with the test environment
    :return:
    """
    final_number_of_vcpus = int(params.get("final_number_of_vcpus"))
    els = params.get("els")
    diag318 = params.get("diag318")
    check_stat = params.get("check_stat") == "yes"
    plug = params.get("plug")
    vm_name = params.get("main_vm")

    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        update_vm_xml(diag318, els, final_number_of_vcpus, plug, vmxml)
        vm.start()
        session = vm.wait_for_login()
        if plug == "hot":
            virsh.setvcpus(vm_name, final_number_of_vcpus, "--live",
                           ignore_status=False)
        if check_stat:
            raise_if_only_zero_entries(session)
    except Exception as e:
        test.fail("Test failed: %s" % e)
    finally:
        vmxml_backup.sync()


def update_vm_xml(diag318, els, final_number_of_vcpus, plug, vmxml):
    """
    Updates the vm's xml with the given input parameters

    :param diag318: feature policy
    :param els: feature policy
    :param final_number_of_vcpus: max vcpus
    :param plug: hot or cold
    :param vmxml: vm's xml
    :return: None
    """
    current_vcpus = final_number_of_vcpus
    if plug == "hot":
        current_vcpus = current_vcpus - 1
    vmxml.vcpu = final_number_of_vcpus
    vmxml.current_vcpu = current_vcpus
    vmxml.features = VMFeaturesXML()
    vmxml.features.add_feature("els", "policy", els)
    vmxml.features.add_feature("diag318", "policy", diag318)
    vmxml.sync()


def raise_if_only_zero_entries(session):
    """
    Raises an assertion error if diag 318 debug counter has only zeros

    :param session: guest session
    :raises AssertionError: if check fails
    """
    out = session.cmd_output('cat /sys/kernel/debug/diag_stat|grep "diag 318"')
    logging.debug("diag_stat contains: %s", out)
    no_zeros = out.replace("0", "").replace("diag 318", "")
    if not re.search(r"\d", no_zeros):
        raise AssertionError("Expected counters to be none zero, got: %s"
                             % out)
