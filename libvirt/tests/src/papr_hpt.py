import logging
import platform
import re
import os

from avocado.utils import cpu
from avocado.utils import process

from virttest import virsh
from virttest import utils_hotplug
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Test hpt resizing
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = eval(params.get('error_msg', '[]'))

    hpt_attrs = eval(params.get('hpt_attrs', '{}'))
    hpt_order_path = params.get('hpt_order_path', '')
    cpu_attrs = eval(params.get('cpu_attrs', '{}'))
    numa_cell = eval(params.get('numa_cell', '{}'))
    hugepage = 'yes' == params.get('hugepage', 'no')
    maxpagesize = int(params.get('maxpagesize', 0))
    check_hp = 'yes' == params.get('check_hp', 'no')
    qemu_check = params.get('qemu_check', '')
    skip_p8 = 'yes' == params.get('skip_p8', 'no')

    def set_hpt(vmxml, sync, **attrs):
        """
        Set resizing value to vm xml

        :param vmxml: xml of vm to be manipulated
        :param sync: whether to sync vmxml after
        :param attrs: attrs to set to hpt xml
        """
        if vmxml.xmltreefile.find('/features'):
            features_xml = vmxml.features
        else:
            features_xml = vm_xml.VMFeaturesXML()
        hpt_xml = vm_xml.VMFeaturesHptXML()
        for attr in attrs:
            setattr(hpt_xml, attr, attrs[attr])
        features_xml.hpt = hpt_xml
        vmxml.features = features_xml
        logging.debug(vmxml)
        if sync:
            vmxml.sync()

    def set_cpu(vmxml, **attrs):
        """
        Set cpu attrs for vmxml according to given attrs

        :param vmxml: xml of vm to be manipulated
        :param attrs: attrs to set to cpu xml
        """
        if vmxml.xmltreefile.find('cpu'):
            cpu = vmxml.cpu
        else:
            cpu = vm_xml.VMCPUXML()
        if 'numa_cell' in attrs:
            cpu.xmltreefile.create_by_xpath('/numa')
            attrs['numa_cell'] = cpu.dicts_to_cells(attrs['numa_cell'])
        for key in attrs:
            setattr(cpu, key, attrs[key])
        vmxml.cpu = cpu
        vmxml.sync()

    def set_memory(vmxml):
        """
        Set memory attributes in vm xml
        """
        vmxml.max_mem_rt = int(params.get('max_mem_rt', 30670848))
        vmxml.max_mem_rt_slots = int(params.get('max_mem_rt_slots', 16))
        vmxml.max_mem_rt_unit = params.get('max_mem_rt_unit', 'KiB')

        logging.debug(numa_cell)
        if numa_cell:
            # Remove cpu topology to avoid that it doesn't match vcpu count
            if vmxml.get_cpu_topology():
                new_cpu = vmxml.cpu
                new_cpu.del_topology()
                vmxml.cpu = new_cpu
            vmxml.vcpu = max([int(cell['cpus'][-1]) for cell in numa_cell]) + 1
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
            libvirt.check_result(result, error_msg)
        return hpt_order

    def check_hp_in_vm(session, page_size):
        """
        Check if hugepage size is correct inside vm

        :param session: the session of the running vm
        :param page_size: the expected pagesize to be checked inside vm
        """
        expect = False if int(page_size) == 65536 else True
        meminfo = session.cmd_output('cat /proc/meminfo|grep Huge')
        logging.info('meminfo: \n%s', meminfo)
        pattern = 'Hugepagesize:\s+%d\s+kB' % int(page_size / 1024)
        logging.info('"%s" should %s be found in meminfo output',
                     pattern, '' if expect else 'not')
        result = expect == bool(re.search(pattern, meminfo))
        if not result:
            test.fail('meminfo output not meet expectation')

        # Check PAGE_SIZE in another way
        if not expect:
            conf_page_size = session.cmd_output('getconf PAGE_SIZE')
            logging.debug('Output of "getconf PAGE_SIZE": %s', conf_page_size)
            if int(conf_page_size) != int(page_size):
                test.fail('PAGE_SIZE not correct, should be %r, actually is %r' %
                          (page_size, conf_page_size))

    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        arch = platform.machine()
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        resizing = hpt_attrs.get('resizing')

        # Test on ppc64le hosts
        if arch.lower() == 'ppc64le':
            cpu_arch = cpu.get_family() if hasattr(cpu, 'get_family') else cpu.get_cpu_arch()
            logging.debug('cpu_arch is: %s', cpu_arch)
            if skip_p8 and cpu_arch == 'power8':
                test.cancel('This case is not for POWER8')
            if maxpagesize and not utils_misc.compare_qemu_version(3, 1, 0):
                test.cancel('Qemu version is too low, '
                            'does not support maxpagesize setting')
            if maxpagesize == 16384 and cpu_arch == 'power9':
                test.cancel('Power9 does not support 16M pagesize.')

            set_hpt(vmxml, True, **hpt_attrs)
            if cpu_attrs or numa_cell:
                if numa_cell:
                    cpu_attrs['numa_cell'] = numa_cell
                set_cpu(vmxml, **cpu_attrs)
            if hugepage:
                vm_mem = vmxml.max_mem
                host_hp_size = utils_memory.get_huge_page_size()

                # Make 100m extra memory just to be safe
                hp_count = max((vm_mem + 102400) // host_hp_size, 1200)
                vm_xml.VMXML.set_memoryBacking_tag(vm_name, hpgs=True)

                # Set up hugepage env
                mnt_source, hp_path, fstype = 'hugetlbfs', '/dev/hugepages', 'hugetlbfs'
                if not os.path.isdir(hp_path):
                    process.run('mkdir %s' % hp_path, verbose=True)
                utils_memory.set_num_huge_pages(hp_count)
                if utils_misc.is_mounted(mnt_source, hp_path, fstype, verbose=True):
                    utils_misc.umount(mnt_source, hp_path, fstype, verbose=True)
                utils_misc.mount(mnt_source, hp_path, fstype, verbose=True)

                # Restart libvirtd service to make sure mounted hugepage
                # be recognized
                utils_libvirtd.libvirtd_restart()

            if resizing == 'enabled':
                set_memory(vmxml)
            logging.debug('vmxml: \n%s', vmxml)

            # Start vm and check if start succeeds
            result = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(result, expect_error=status_error)

            # if vm is not suposed to start, terminate test
            if status_error:
                libvirt.check_result(result, error_msg)
                return

            libvirt.check_qemu_cmd_line(qemu_check)
            session = vm.wait_for_login()
            hpt_order = check_hpt_order(session, resizing)

            # Check hugepage inside vm
            if check_hp:
                check_hp_in_vm(session, maxpagesize * 1024)

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

        # Test on non-ppc64le hosts
        else:
            set_hpt(vmxml, sync=False, **hpt_attrs)
            result = virsh.define(vmxml.xml)
            libvirt.check_exit_status(result, status_error)
            libvirt.check_result(result, error_msg)

    finally:
        bk_xml.sync()
        if hugepage:
            utils_misc.umount('hugetlbfs', '/dev/hugepages', 'hugetlbfs')
            utils_memory.set_num_huge_pages(0)
