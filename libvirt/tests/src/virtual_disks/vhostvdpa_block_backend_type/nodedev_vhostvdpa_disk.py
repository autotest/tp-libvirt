from virttest import libvirt_version
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import nodedev_xml

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Verify nodedev related operations against vhost-vdpa disk.
    """
    def check_nodedev_xml(dev_name):
        """
        Check nodedev xml for vhost-vdpa device.

        :param dev_name: Device name
        """
        test.log.info(f"TEST_STEP: Check xml for node device - {dev_name}.")
        nodexml = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name)
        nodedev_attrs = nodexml.fetch_attrs()
        nodedev_attrs.update({"cap": {"chardev": nodexml.cap.get_chardev()}})
        idx = nodedev_attrs["path"][-1]
        exp_attrs = {"driver_name": "vhost_vdpa", "cap_type": "vdpa",
                     "cap": {"chardev": f"/dev/vhost-vdpa-{idx}"}}
        test.log.debug(f"Exptected: {exp_attrs}")
        test.log.debug(f"Actual: {nodedev_attrs}")
        for k, v in exp_attrs.items():
            if nodedev_attrs.get(k) != v:
                test.fail("Failed to check node device xml!")

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        test.log.info("TEST_STEP: Prepare vhost-vdpa disks.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(
            sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup(dev_num=2)

        test.log.info("TEST_STEP: List node devices with vdpa capability on host.")
        vdpa_devices = [d for d in test_env_obj.get_vdpa_dev_info()['dev']]
        devs = virsh.nodedev_list(
            cap="vdpa", **VIRSH_ARGS).stdout_text.strip().splitlines()
        if devs != ["vdpa_" + d for d in vdpa_devices]:
            test.fail("Failed to get vdpa devices!")

        for dev_name in devs:
            check_nodedev_xml(dev_name)

    finally:
        test_env_obj.cleanup()
