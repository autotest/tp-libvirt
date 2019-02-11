import logging
import platform
import re

from avocado.utils import process

from virttest import virsh
from virttest import utils_hotplug
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test hpt resizing
    """
    resizing = params.get('resizing')
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    hpt_order_path = params.get('hpt_order_path', '')
    qemu_check = params.get('qemu_check', '')

    def set_hpt(resizing, vmxml, sync=True):
        """
        Set resizing value to vm xml
        """
        features_xml = vm_xml.VMFeaturesXML()
        features_xml.hpt_resizing = resizing
        vmxml.features = features_xml
        if sync:
            vmxml.sync()

    def set_memory(vmxml):
        """
        Set memory attributes in vm xml
        """
        vmxml.max_mem_rt = int(params.get('max_mem_rt', 30670848))
        vmxml.max_mem_rt_slots = int(params.get('max_mem_rt_slots', 16))
        vmxml.max_mem_rt_unit = params.get('max_mem_rt_unit', 'KiB')

        cpu = vm_xml.VMCPUXML()
        cpu.xml = "<cpu><numa/></cpu>"

        numa_cell = eval(params.get('numa_cell'))
        logging.debug(numa_cell)

        vmxml.vcpu = max([int(cell['cpus'][-1]) for cell in numa_cell]) + 1

        cpu.numa_cell = numa_cell
        vmxml.cpu = cpu
        vmxml.sync()

    def check_hpt_order(session, resizing=''):
        """
        Return htp order in hpt_order file by default
        If 'resizing' is disabled, test updating htp_order
        """
        if not hpt_order_path:
            test.cancel('No hpt order path provided.')
        hpt_order = session.cmd_output('cat %s' % hpt_order_path).strip()
        hpt_order = int(hpt_order)
        logging.info('Current hpt_order is %d', hpt_order)
        if resizing == 'disabled':
            cmd_result = session.cmd_status_output(
                'echo %d > %s' % (hpt_order + 1, hpt_order_path))
            result = process.CmdResult(stderr=cmd_result[1],
                                       exit_status=cmd_result[0])
            libvirt.check_exit_status(result, True)
            libvirt.check_result(result, [error_msg])
        return hpt_order

    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        arch = platform.machine()
        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Test on ppc64le hosts
        if arch.lower() == 'ppc64le':
            set_hpt(resizing, new_xml)
            if resizing == 'enabled':
                set_memory(new_xml)

            # Start vm and check if start succeeds
            libvirt.check_exit_status(virsh.start(vm_name, debug=True))
            libvirt.check_qemu_cmd_line(qemu_check)
            session = vm.wait_for_login()
            hpt_order = check_hpt_order(session, resizing)

            if resizing == 'enabled':
                mem_xml = utils_hotplug.create_mem_xml(
                    tg_size=int(params.get('mem_size', 2048000)),
                    tg_sizeunit=params.get('size_unit', 'KiB'),
                    tg_node=int(params.get('mem_node', 0)),
                    mem_model=params.get('mem_model', 'dimm')
                )
                logging.debug(mem_xml)

                # Attach memory device to the guest for 12 times
                # that will reach the maxinum memory limitation
                for i in range(12):
                    virsh.attach_device(vm_name, mem_xml.xml,
                                        debug=True, ignore_status=False)
                xml_after_attach = vm_xml.VMXML.new_from_dumpxml(vm_name)
                logging.debug(xml_after_attach)

                # Check dumpxml of the guest,
                # check if each device has its alias
                for i in range(12):
                    pattern = "alias\s+name=[\'\"]dimm%d[\'\"]" % i
                    logging.debug('Searching for %s', pattern)
                    if not re.search(pattern, str(xml_after_attach.xmltreefile)):
                        test.fail('Missing memory alias: %s' % pattern)

                # Log in the guest and check dmesg
                dmesg = session.cmd('dmesg')
                logging.debug(dmesg)
                dmesg_content = params.get('dmesg_content', '').split('|')
                for order in range(1, 3):
                    order += hpt_order
                    for content in dmesg_content:
                        if content % order not in dmesg:
                            test.fail('Missing dmesg: %s' % (content % order))

        # Test on non-ppc64le hosts
        else:
            set_hpt(resizing, new_xml, sync=False)
            result = virsh.define(new_xml.xml)
            libvirt.check_exit_status(result, status_error)
            libvirt.check_result(result, [error_msg])

    finally:
        bk_xml.sync()
