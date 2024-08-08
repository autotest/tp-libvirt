import re
import logging as log

from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh
from virttest import libvirt_version

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the ras feature
    1. Enable 'ras=on/off' in a guest
    2. check the xml and qemu cmd line
    """
    # Ras feature supported since 10.4.0.
    if not libvirt_version.version_compare(10, 4, 0):
        test.cancel("Ras feature is not supported "
                    "on current version.")

    ras_state = params.get("ras_state")

    def check_dumpxml():
        """
        Check whether the added devices are shown in the guest xml
        """
        pattern = "<ras state=\"%s\" />" % ras_state
        # Check ras state
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        if pattern not in str(vm_xml):
            test.fail("Can not find %s "
                      "in the guest xml file." % pattern)

    def check_qemu_cmd_line():
        """
        Check whether the ras feature is shown in the qemu cmd line
        """
        if not vm.get_pid():
            test.fail('VM pid file missing.')
        with open('/proc/%s/cmdline' % vm.get_pid()) as cmdline_file:
            cmdline = cmdline_file.read()
        pattern = r"-machine.*ras=%s" % ras_state
        if not re.search(pattern, cmdline):
            test.fail("Can not find the ras=%s "
                      "in qemu cmd line." % ras_state)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    logging.debug("vm xml is %s", vm_xml_backup)

    if vm.is_alive():
        vm.destroy()

    try:
        features_xml = vm_xml.features
        if features_xml.has_feature('ras'):
            features_xml.remove_feature('ras')
        features_xml.ras = "%s" % ras_state
        vm_xml.features = features_xml
        vm_xml.sync()
        virsh.start(vm_name, ignore_status=False)
        check_dumpxml()
        check_qemu_cmd_line()
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()
