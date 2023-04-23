import re

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import nodedev_xml
from virttest.utils_libvirt import libvirt_vfio

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Nodedev-reattach/detach a VF device to/from host
    """
    def check_event(target_event, event_tracker):
        """
        Check whether target_event exists in actual event_output

        :param target_event: event that's supposed to exist
        :param event_tracker: event tracker to get events
        :return: True if target_event exists, False if not.
        """
        event_output = utils_misc.wait_for(
            lambda: event_tracker.get_stripped_output(), 20, first=5)
        test.log.debug("Event output: %s", event_output)
        if re.search(target_event, event_tracker.get_stripped_output()):
            test.log.debug('event found: %s', target_event)
            return True
        else:
            test.log.error('event not found, %s', target_event)
            return False

    def get_event_tracker(iface_name):
        """
        Get an event_tracker

        :param iface_name: Interface name
        :return: An event session
        """
        net_nodes = virsh.nodedev_list(cap="net", debug=True,
                                       ignore_status=False).stdout_text.strip()
        res = re.search("net_%s_.*" % iface_name, net_nodes)
        if not res:
            test.error("Unable to get noddev %s, please check the env."
                       % iface_name)
        event_cmd = "nodedev-event --event lifecycle --loop --timestamp"
        return virsh.EventTracker.start_get_event(vm.name, event_cmd=event_cmd)

    def run_test(dev_name, dev_pci, iface_name):
        """
        Nodedev-reattach/detach a VF device to/from host.

        1. Run virsh nodedev-event in another terminal to capture events.
        2. Check nodedev driver info before detach.
        3. Run 'virsh nodedev-detach' for the PF or VF.
        4. Check driver info gain after detach.
        5. on the host, run nodedev-reattach for the PF or VF.
        6. Check driver info gain.
        """
        event_tracker = get_event_tracker(iface_name)

        test.log.info("TEST_STEP1: Check device's driver.")
        libvirt_vfio.check_vfio_pci(dev_pci, True, ignore_error=True)
        dev_driver = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name).get('driver_name')
        if dev_driver == "vfio-pci":
            test.fail("Got incorrect device driver '%s'!" % dev_driver)

        test.log.info("TEST_STEP2: Detach the node device.")
        virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        if not check_event("Deleted", event_tracker):
            test.fail("Fail to get 'Deleted' events!")
        libvirt_vfio.check_vfio_pci(dev_pci)

        test.log.info("TEST_STEP3: Reattach the node device.")
        virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
        if not check_event("Created", event_tracker):
            test.fail("Fail to get 'Created' events!")

        libvirt_vfio.check_vfio_pci(dev_pci, True, ignore_error=True)
        event_tracker.close()

    dev_name = params.get("dev_name", "pf")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    if dev_name == "vf":
        dev_name = sriov_test_obj.vf_dev_name
        dev_pci = sriov_test_obj.vf_pci
        iface_name = sriov_test_obj.vf_name
    else:
        dev_name = sriov_test_obj.pf_dev_name
        dev_pci = sriov_test_obj.pf_pci
        iface_name = sriov_test_obj.pf_name

    try:
        run_test(dev_name, dev_pci, iface_name)

    finally:
        virsh.nodedev_reattach(dev_name, debug=True)
