import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_misc
from virttest import cpu
from virttest.utils_test import libvirt
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml.devices.iommu import Iommu

from virttest import libvirt_version


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

    def check_onlinevcpus(vm, cpu_num):
        """

        Check whether all vcpus are online as expected.

        :param vm: the exact VM need to check
        :param cpu_num: the num of online vcpus need to match
        """
        if not utils_misc.wait_for(
                lambda: cpu.check_if_vm_vcpu_match(cpu_num, vm),
                timeout=120, step=5, text="wait for vcpu online"):
            test.fail('Not all vcpus are online as expected.')

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
        # Check the output of "virsh maxvcpus" for both i440fx and q35 VM
        if check == 'virsh_maxvcpus':
            report_num = params.get('report_num', '')
            logging.info('Check the output of virsh maxvcpus')
            cmd_result = virsh.maxvcpus(debug=True)
            if cmd_result.exit_status == 0 and cmd_result.stdout.strip() == report_num:
                logging.debug('Test passed as the reported max vcpu num is %s', report_num)
            else:
                test.fail('Test failed as the reported max vcpu num is not as expected.')

        # Check the output of "virsh capabilities" for both i440fx and q35 VM
        if check == "virsh_capabilities":
            report_num_pc_7 = params.get('report_num_pc_7', '')
            report_num_q35_73 = params.get('report_num_q35_73', '')
            report_num_q35_7_8 = params.get('report_num_q35_7_8', '')
            report_num_q35_8_3 = params.get('report_num_q35_8_3', '')
            report_num_q35_8_4 = params.get('report_num_q35_8_4', '')
            logging.info('Check the output of virsh capabilities')
            xmltreefile = capability_xml.CapabilityXML().xmltreefile
            machtype_vcpunum_dict = {}
            for guest in xmltreefile.findall('guest'):
                for arch in guest.findall('arch'):
                    if arch.get('name') == "x86_64":
                        for machine in arch.findall('machine'):
                            machine_text = machine.text
                            vcpunum = machine.get('maxCpus')
                            machtype_vcpunum_dict[machine_text] = vcpunum
            for key in machtype_vcpunum_dict:
                logging.info("%s : %s", key, machtype_vcpunum_dict[key])
                if key.startswith('pc-i440fx') or key.startswith('rhel') or key == 'pc':
                    if machtype_vcpunum_dict[key] != report_num_pc_7:
                        test.fail('Test failed as i440fx_max_vcpus_num in '
                                  'virsh_capa is wrong. Expected: {} '
                                  'Actual: {}.'
                                  .format(report_num_pc_7,
                                          machtype_vcpunum_dict[key]))
                if key.startswith('pc-q35') or key == 'q35':
                    if key == "pc-q35-rhel7.3.0":
                        if machtype_vcpunum_dict[key] != report_num_q35_73:
                            test.fail('Test failed as q35_rhel73_max_vcpus_num '
                                      'in virsh_capa is wrong. Expected: {} '
                                      'Actual: {}.'
                                      .format(report_num_q35_73,
                                              machtype_vcpunum_dict[key]))
                    else:
                        exp_val = report_num_q35_7_8
                        if libvirt_version.version_compare(7, 0, 0):
                            exp_val = report_num_q35_8_4
                        elif libvirt_version.version_compare(6, 6, 0):
                            exp_val = report_num_q35_8_3
                        if machtype_vcpunum_dict[key] != exp_val:
                            test.fail('Test failed as the q35_max_vcpus_num in '
                                      'virsh_capa is wrong. Expected: {} '
                                      'Actual: {}.'
                                      .format(exp_val,
                                              machtype_vcpunum_dict[key]))

        # Test i440fx VM starts with 240(positive)/241(negative) vcpus and hot-plugs vcpus to 240
        if check.startswith('i440fx_test'):
            current_vcpu = params.get('current_vcpu')
            target_vcpu = params.get('target_vcpu')
            if 'hotplug' not in check:
                vmxml.vcpu = int(guest_vcpu)
                vmxml.sync()
                if status_error:
                    if start_fail:
                        result_need_check = virsh.start(vm_name, debug=True)
                else:
                    vm.start()
                    logging.info(libvirt_xml.VMXML.new_from_dumpxml(vm_name))
                    vm.wait_for_login(timeout=boot_timeout).close()
                    check_onlinevcpus(vm, int(guest_vcpu))
            else:
                vmxml.vcpu = int(guest_vcpu)
                vmxml.current_vcpu = int(current_vcpu)
                target_vcpu = int(target_vcpu)
                vmxml.sync()
                vm.start()
                logging.info(libvirt_xml.VMXML.new_from_dumpxml(vm_name))
                vm.wait_for_login(timeout=boot_timeout).close()
                check_onlinevcpus(vm, int(current_vcpu))
                res = virsh.setvcpus(vm_name, target_vcpu, debug=True)
                libvirt.check_exit_status(res)
                check_onlinevcpus(vm, int(target_vcpu))

        # Configure a guest vcpu > 255 without iommu device for q35 VM
        if check == 'no_iommu':
            logging.info('Set vcpu to %s', guest_vcpu)
            vmxml.vcpu = int(guest_vcpu)
            result_need_check = virsh.define(vmxml.xml, debug=True)

        # Set iommu device but not set ioapci in features for q35 VM
        if check == 'with_iommu':
            logging.info('Set vcpu to %s', guest_vcpu)
            vmxml.vcpu = int(guest_vcpu)
            set_iommu(vmxml)
            result_need_check = virsh.define(vmxml.xml, debug=True)

        # Add ioapic and iommu device in xml for q35 VM
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

            if status_error:
                if start_fail:
                    if libvirt_version.version_compare(5, 6, 0):
                        result_need_check = virsh.define(vmxml.xml, debug=True)
                    else:
                        vmxml.sync()
                        result_need_check = virsh.start(vm_name, debug=True)

            else:
                # Login guest and check guest cpu number
                vmxml.sync()
                logging.debug(virsh.dumpxml(vm_name))
                vm.start()
                session = vm.wait_for_login(timeout=boot_timeout)
                logging.debug(session.cmd('lscpu -e'))

                # Hotplug vcpu to $guest_vcpu
                if 'hotplug' in check:
                    res = virsh.setvcpus(vm_name, guest_vcpu, debug=True)
                    libvirt.check_exit_status(res)

                # Check if vcpu(s) are online
                check_onlinevcpus(vm, int(guest_vcpu))

        # Check result if there's result to check
        if 'result_need_check' in locals():
            libvirt.check_result(result_need_check, err_msg)

    finally:
        bkxml.sync()
