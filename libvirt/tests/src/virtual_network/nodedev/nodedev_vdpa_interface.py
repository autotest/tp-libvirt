from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_vdpa
from virttest import virsh

from virttest.libvirt_xml import nodedev_xml


VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Check the nodedev info for the device
    """

    def check_environment(params):
        """
        Check the test environment

        :param params: Dictionary with the test parameters
        """
        libvirt_version.is_libvirt_feature_supported(params)
        utils_misc.is_qemu_function_supported(params)

    def setup_vdpa():
        """
        Setup vDPA environment
        """
        test_env_obj = None
        test_target = params.get('test_target', '')
        test.log.info("TEST_SETUP: Setup vDPA environment.")
        if test_target == "simulator":
            test_env_obj = utils_vdpa.VDPASimulatorTest()
        else:
            pf_pci = utils_vdpa.get_vdpa_pci()
            test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        return test_env_obj

    def teardown_vdpa():
        """
        Cleanup vDPA environment
        """
        test.log.info("TEST_TEARDOWN: Clean up vDPA environment.")
        if test_obj:
            test_obj.cleanup()

    def check_nodedev_info(dev_dict):
        """Check nodedev info for the node device

        1) virsh nodedev-list and check the device
        2) virsh nodedev-dumpxml and check the device info
        3) Validate using virt-xml-validate

        :param dev_dict: device params
        """
        dev_name = dev_dict.get('name')
        test.log.info("TEST_STEP1: List %s device using virsh nodedev-list.",
                      dev_name)
        result = virsh.nodedev_list(**VIRSH_ARGS)
        if dev_name not in result.stdout_text:
            test.fail("Failed to list %s device!" % dev_name)

        test.log.info("TEST_STEP2: Check device info using virsh "
                      "nodedev-dumpxml.")
        dev_xml = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name)
        test.log.debug("Nodedev xml: {}".format(dev_xml))
        if not all([getattr(dev_xml, attr).endswith(value) for attr, value in
                   dev_dict.items()]):
            test.fail('nodedev xml comparison failed.')

        test.log.info("TEST_STEP3: Validate xml using virt-xml-validate.")
        if not dev_xml.get_validates():
            test.fail("Failed to validate node device xml!")

    check_environment(params)

    # Variable assignment
    dev_dict = eval(params.get('dev_dict', '{}'))

    test_obj = None
    try:
        # Execute test
        test.log.info("TEST_CASE: %s",
                      check_nodedev_info.__doc__.split('\n')[0])
        test_obj = setup_vdpa()
        check_nodedev_info(dev_dict)

    finally:
        teardown_vdpa()
