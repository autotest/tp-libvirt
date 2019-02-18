import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.iommu import Iommu


def run(test, params, env):
    """
    Test vcpu
    """
    vm_name = params.get('main_vm')
    check = params.get('check', '')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg', '')
    guest_vcpu = params.get('guest_vcpu')
    boot_timeout = int(params.get('boot_timeout', 240))
    start_fail = 'yes' == params.get('start_fail', 'no')

    vm = env.get_vm(vm_name)
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    def set_iommu(vmxml, **dargs):
        """

        Add iommu device to vm.

        :param vmxml: xml of vm to be add iommu device
        :param dargs: args or the iommu device
        :return:
        """
        logging.info('Add iommu device to vm.')
        iommu_device = Iommu()
        iommu_device.model = dargs.get('model', 'intel')
        iommu_device.driver = dargs.get('driver', {'intremap': 'on', 'eim': 'on'})
        vmxml.add_device(iommu_device)

    try:
        # Configure a guest vcpu > 255 without iommu device
        if check == 'no_iommu':
            logging.info('Set vcpu to %s', guest_vcpu)
            vmxml.vcpu = int(guest_vcpu)
            result_need_check = virsh.define(vmxml.xml, debug=True)

        # Set iommu device but not set ioapci in features
        if check == 'with_iommu':
            logging.info('Set vcpu to %s', guest_vcpu)
            vmxml.vcpu = int(guest_vcpu)
            set_iommu(vmxml)
            result_need_check = virsh.define(vmxml.xml, debug=True)

        # Add ioapic and iommu device in xml
        if check.startswith('ioapic_iommu'):
            logging.info('Modify features')
            vm_features = vmxml.features
            vm_features.add_feature('apic')
            vm_features.add_feature('ioapic', 'driver', 'qemu')
            vmxml.features = vm_features
            logging.debug(vmxml.features.get_feature_list())

            logging.info('Set vcpu to %s', guest_vcpu)
            set_iommu(vmxml)

            ori_vcpu = vmxml.vcpu
            vmxml.vcpu = int(guest_vcpu)
            vmxml.current_vcpu = ori_vcpu

            if 'hotplug' not in check:
                vmxml.current_vcpu = int(guest_vcpu)

            vmxml.sync()
            logging.debug(virsh.dumpxml(vm_name))

            if status_error:
                if start_fail:
                    result_need_check = virsh.start(vm_name, debug=True)

            else:
                # Login guest and check guest cpu number
                virsh.start(vm_name, debug=True)
                session = vm.wait_for_login(timeout=boot_timeout)
                logging.debug(session.cmd('lscpu -e'))

                # Hotplug vcpu to $guest_vcpu
                if 'hotplug' in check:
                    res = virsh.setvcpus(vm_name, guest_vcpu, debug=True)
                    libvirt.check_exit_status(res)

                # Check if vcpu(s) are online
                if not utils_misc.wait_for(
                        lambda: utils_misc.check_if_vm_vcpu_match(int(guest_vcpu), vm),
                        timeout=60, step=5, text="wait for vcpu online"):
                    test.fail('Not all CPU(s) are online')

        # Check result if there's result to check
        if 'result_need_check' in locals():
            libvirt.check_result(result_need_check, err_msg)

    finally:
        bkxml.sync()
