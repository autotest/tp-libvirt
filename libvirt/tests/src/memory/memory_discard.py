import logging
import ast
import time

from virttest import virsh
from virttest import virt_vm
from virttest import utils_hotplug
from virttest import utils_config
from virttest import utils_misc
from virttest import utils_libvirtd

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Test memory management of nvdimm
    """
    vm_name = params.get('main_vm')

    check = params.get('check', '')
    qemu_checks = params.get('qemu_checks', '')

    def mount_hugepages(page_size):
        """
        To mount hugepages

        :param page_size: unit is kB, it can be 4,2048,1048576,etc
        """
        if page_size == 4:
            perm = ""
        else:
            perm = "pagesize=%dK" % page_size

        tlbfs_status = utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                             "hugetlbfs")
        if tlbfs_status:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs", perm)

    def setup_hugepages(page_size=2048, shp_num=4000):
        """
        To setup hugepages

        :param page_size: unit is kB, it can be 4,2048,1048576,etc
        :param shp_num: number of hugepage, string type
        """
        mount_hugepages(page_size)
        utils_memory.set_num_huge_pages(shp_num)
        config.hugetlbfs_mount = ["/dev/hugepages"]
        utils_libvirtd.libvirtd_restart()

    def restore_hugepages(page_size=4):
        """
        To recover hugepages
        :param page_size: unit is kB, it can be 4,2048,1048576,etc
        """
        mount_hugepages(page_size)
        config.restore()
        utils_libvirtd.libvirtd_restart()

    def create_mbxml():
        """
        Create memoryBacking xml for test
        """
        mb_params = {k: v for k, v in params.items() if k.startswith('mbxml_')}
        logging.debug(mb_params)
        mb_xml = vm_xml.VMMemBackingXML()
        mb_xml.xml = "<memoryBacking></memoryBacking>"
        for attr_key in mb_params:
            val = mb_params[attr_key]
            logging.debug('Set mb params')
            setattr(mb_xml, attr_key.replace('mbxml_', ''),
                    eval(val) if ':' in val else val)
        logging.debug(mb_xml)
        return mb_xml.copy()

    def create_cpuxml():
        """
        Create cpu xml for test
        """
        cpu_params = {k: v for k, v in params.items() if k.startswith('cpuxml_')}
        logging.debug(cpu_params)
        cpu_xml = vm_xml.VMCPUXML()
        cpu_xml.xml = "<cpu><numa/></cpu>"
        if 'cpuxml_numa_cell' in cpu_params:
            cpu_params['cpuxml_numa_cell'] = cpu_xml.dicts_to_cells(
                eval(cpu_params['cpuxml_numa_cell']))
        for attr_key in cpu_params:
            val = cpu_params[attr_key]
            logging.debug('Set cpu params')
            setattr(cpu_xml, attr_key.replace('cpuxml_', ''),
                    eval(val) if ':' in val else val)
        logging.debug(cpu_xml)
        return cpu_xml.copy()

    def create_dimm_xml(**mem_param):
        """
        Create xml of dimm memory device
        """
        mem_xml = utils_hotplug.create_mem_xml(
            pg_size=int(mem_param['source_pagesize']),
            tg_size=mem_param['target_size'],
            tg_sizeunit=mem_param['target_size_unit'],
            tg_node=mem_param['target_node'],
            mem_model="dimm"
        )
        logging.debug(mem_xml)
        return mem_xml.copy()

    huge_pages = [ast.literal_eval(x)
                  for x in params.get("huge_pages", "").split()]

    config = utils_config.LibvirtQemuConfig()
    page_size = params.get("page_size")
    discard = params.get("discard")
    setup_hugepages(int(page_size))

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vm = env.get_vm(vm_name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Set cpu according to params
        cpu_xml = create_cpuxml()
        vmxml.cpu = cpu_xml

        # Set memoryBacking according to params
        mb_xml = create_mbxml()
        vmxml.mb = mb_xml

        # Update other vcpu, memory info according to params
        update_vm_args = {k: params[k] for k in params
                          if k.startswith('setvm_')}
        logging.debug(update_vm_args)
        for key, value in list(update_vm_args.items()):
            attr = key.replace('setvm_', '')
            logging.debug('Set %s = %s', attr, value)
            setattr(vmxml, attr, int(value) if value.isdigit() else value)
        vmxml.sync()
        logging.debug(virsh.dumpxml(vm_name))

        # hugepages setting
        if huge_pages:
            membacking = vm_xml.VMMemBackingXML()
            hugepages = vm_xml.VMHugepagesXML()
            pagexml_list = []
            for i in range(len(huge_pages)):
                pagexml = hugepages.PageXML()
                pagexml.update(huge_pages[i])
                pagexml_list.append(pagexml)
            hugepages.pages = pagexml_list
            membacking.hugepages = hugepages
            vmxml.mb = membacking
            logging.debug(virsh.dumpxml(vm_name))

        if check == "mem_dev" or check == "hot_plug":
            # Add  dimm mem device to vm xml
            dimm_params = {k.replace('dimmxml_', ''): v
                           for k, v in params.items() if k.startswith('dimmxml_')}
            dimm_xml = create_dimm_xml(**dimm_params)
            if params.get('dimmxml_mem_access'):
                dimm_xml.mem_access = dimm_params['mem_access']
            vmxml.add_device(dimm_xml)
            logging.debug(virsh.dumpxml(vm_name))

        test_vm = env.get_vm(vm_name)
        vmxml.sync()
        if test_vm.is_alive():
            test_vm.destroy()

        virsh.start(vm_name, debug=True, ignore_status=False)
        test_vm.wait_for_login()

        if check == 'numa_cell' or check == 'mem_dev':
            # Check qemu command line one by one
            logging.debug("enter check")
            if discard == 'yes':
                libvirt.check_qemu_cmd_line(qemu_checks)
            elif libvirt.check_qemu_cmd_line(qemu_checks, True):
                test.fail("The unexpected [%s] exist in qemu cmd" % qemu_checks)

        if check == 'hot_plug':
            # Add dimm device to vm xml
            dimm_params2 = {k.replace('dimmxml2_', ''): v
                            for k, v in params.items() if k.startswith('dimmxml2_')}
            dimm_xml2 = create_dimm_xml(**dimm_params2)
            if params.get('dimmxml2_mem_access'):
                dimm_xml2.mem_access = dimm_params2['mem_access']

            result = virsh.attach_device(vm_name, dimm_xml2.xml, debug=True)

            ori_devices = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug('Starts with %d memory devices', len(ori_devices))

            result = virsh.attach_device(vm_name, dimm_xml2.xml, debug=True)
            libvirt.check_exit_status(result)

            # After attach, there should be a memory device added
            devices_after_attach = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug('After detach, vm has %d memory devices',
                          len(devices_after_attach))

            if len(ori_devices) != len(devices_after_attach) - 1:
                test.fail('Number of memory devices after attach is %d, should be %d'
                          % (len(devices_after_attach), len(ori_devices) + 1))

            alive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            dimm_detach = alive_vmxml.get_devices('memory')[-1]
            logging.debug(dimm_detach)

            # Hot-unplug dimm device
            result = virsh.detach_device(vm_name, dimm_detach.xml, debug=True)
            libvirt.check_exit_status(result)

            left_devices = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug(left_devices)

            if len(left_devices) != len(ori_devices):
                time.sleep(60)
                test.fail('Number of memory devices after detach is %d, should be %d'
                          % (len(left_devices), len(ori_devices)))

    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
