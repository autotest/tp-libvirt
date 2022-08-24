import copy
import os

from provider.sriov import sriov_base

from virttest import virsh
from virttest import utils_libvirtd

from virttest.libvirt_xml import network_xml

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Check net info
    """
    def run_test():
        """
        Check net info by network lifecycle commands:
            net-define/undefine/create
            net-destroy/start
            net-dumpxml
            net-info
            net-update
            net-autostart
            net-list
        """
        test.log.info("TEST_STEP1: Create hostdev network from xml.")
        virsh.net_create(net_dev.xml, **VIRSH_ARGS)
        test.log.info("TEST_STEP2: Check net info by virsh command.")
        virsh.net_info(network_name, **VIRSH_ARGS)
        test.log.info("TEST_STEP3: Update the net.")
        virsh.net_update(network_name, "add", net_update_section,
                         net_update_xml, **VIRSH_ARGS)
        test.log.info("TEST_STEP4: Dump net xml and check it.")
        cur_net = network_xml.NetworkXML().new_from_net_dumpxml(network_name).fetch_attrs()
        if cur_net != network_update_dict:
            test.fail("Network XML compare fails! It should be '%s', but "
                      "got '%s'" % (network_update_dict, cur_net))
        virsh.net_destroy(network_name, **VIRSH_ARGS)

        test.log.info("TEST_STEP5: Define network by virsh command.")
        virsh.net_define(net_dev.xml, **VIRSH_ARGS)
        test.log.info("TEST_STEP6: Start the network.")
        virsh.net_start(network_name, **VIRSH_ARGS)
        virsh.net_destroy(network_name, **VIRSH_ARGS)

        test.log.info("TEST_STEP7: Autostart the network.")
        virsh.net_autostart(network_name, **VIRSH_ARGS)
        if os.path.exists('/run/libvirt/network/autostarted'):
            os.remove('/run/libvirt/network/autostarted')
        utils_libvirtd.Libvirtd("virtnetworkd").restart()
        net_state = virsh.net_state_dict()
        if net_state.get(network_name) and all(net_state.get(network_name).values()):
            test.log.info("Check net info PASS!")
        else:
            test.fail("Unable to get correct net info in %s." % net_state)
        test.log.info("TEST_STEP8: Destroy the network.")
        virsh.net_destroy(network_name, **VIRSH_ARGS)
        test.log.info("TEST_STEP9: Undefine the network.")
        virsh.net_undefine(network_name, **VIRSH_ARGS)

    def cleanup_test():
        """
        Test cleanup
        """
        net_state = virsh.net_state_dict()
        if net_state.get(network_name):
            if net_state.get(network_name).get('active'):
                virsh.net_destroy(network_name, debug=True)
            if net_state.get(network_name).get('persistent'):
                virsh.net_undefine(network_name, debug=True)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    net_update_section = params.get("net_update_section", "portgroup")
    net_update_xml = params.get("net_update_xml", '''"<portgroup name='dontpanic'/>"''')
    network_update_dict = eval(params.get("network_update_dict", "{}"))
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    network_dict = sriov_test_obj.parse_network_dict()
    network_update_dict.update(copy.deepcopy(network_dict))
    test.log.debug(network_update_dict)
    test.log.debug(network_dict)
    network_name = network_dict['name']
    net_dev = network_xml.NetworkXML()
    net_dev.setup_attrs(**network_dict)
    try:
        run_test()
    finally:
        cleanup_test()
