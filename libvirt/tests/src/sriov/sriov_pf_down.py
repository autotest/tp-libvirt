from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Test VFs when PF is down
    """
    def setup_default():
        """
        Default setup
        """
        test.log.info("Set pf state to down.")
        pf_iface_obj = utils_net.Interface(pf_name)
        pf_iface_obj.down()

    def teardown_default():
        """
        Default cleanup
        """
        pf_iface_obj = utils_net.Interface(pf_name)
        pf_iface_obj.up()

    def test_at_dt():
        """
        Test attach-detach interfaces
        """
        options = '' if vm.is_alive() else '--config'
        iface_dict = eval(params.get('iface_dict')
                          % utils_sriov.pci_to_addr(vf_pci))
        iface = interface_base.create_iface('hostdev', iface_dict)
        result = virsh.attach_device(vm_name, iface.xml,
                                     flagstr=options, debug=True)
        if not start_vm:
            result = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(result, status_error)
        if error_msg:
            libvirt.check_result(result, error_msg)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    start_vm = "yes" == params.get("start_vm")
    status_error = "yes" == params.get("status_error", "no")
    error_msg = params.get("error_msg")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")
    sriov_base.setup_vf(pf_pci, params)

    vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
    pf_name = utils_sriov.get_pf_info_by_pci(pf_pci).get('iface')

    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else setup_default
    teardown_test = eval("teardown_%s" % test_case) if "teardown_%s" % \
        test_case in locals() else teardown_default
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        test.log.info("Recover test enviroment.")
        orig_config_xml.sync()
        teardown_test()
