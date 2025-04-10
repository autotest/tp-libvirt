import logging

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the ras feature
    1. Enable 'ras=on/off' in a guest
    2. check the xml and qemu cmd line
    """
    # Ras feature supported since 10.4.0.
    libvirt_version.is_libvirt_feature_supported(params)

    ras_state = params.get("ras_state")

    def check_dumpxml():
        """
        Check whether the added devices are shown in the guest xml
        """
        xpath = [{'element_attrs': [".//ras[@state='%s']" % ras_state]}]
        # Check ras state
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vm_xml, xpath)

    def check_qemu_cmd_line():
        """
        Check whether the ras feature is shown in the qemu cmd line
        """
        pattern = r"-machine.*ras=%s" % ras_state
        libvirt.check_qemu_cmd_line(pattern)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    LOG.debug("vm xml is %s", vm_xml_backup)

    if vm.is_alive():
        vm.destroy()

    try:
        features_xml = vm_xml.features
        if features_xml.has_feature('ras'):
            features_xml.remove_feature('ras')
        features_xml.ras = ras_state
        vm_xml.features = features_xml
        vm_xml.sync()
        virsh.start(vm_name, ignore_status=False)
        check_dumpxml()
        check_qemu_cmd_line()
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()
