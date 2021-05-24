import os
import re
import ast
import uuid
import logging
import platform
import tempfile
import time
import random

from six.moves import xrange

from avocado.utils import cpu as cpu_util
from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_config
from virttest import utils_numeric
from virttest import utils_hotplug
from virttest import libvirt_version
from virttest import virt_vm
from virttest import data_dir
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.staging import utils_memory
from virttest.staging.utils_memory import drop_caches
from virttest.staging.utils_memory import read_from_numastat

from virttest import libvirt_version


def run(test, params, env):
    """
    Test rbd disk device.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare disk image.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    # Global variable to store max/current memory,
    # it may change after attach/detach
    new_max_mem = None
    new_cur_mem = None

    def consume_vm_mem(size=1000, timeout=360):
        """
        To consume guest memory, default size is 1000M
        """
        session = vm.wait_for_login()
        # Mount tmpfs on /mnt and write to a file on it,
        # it is the memory operation
        sh_cmd = ("swapoff -a; mount -t tmpfs -o size={0}M tmpfs "
                  "/mnt; dd if=/dev/urandom of=/mnt/test bs=1M"
                  " count={0}".format(size))
        session.cmd(sh_cmd, timeout=timeout)
        session.close()

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

    def setup_hugepages(page_size=2048, shp_num=2000):
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

    def check_qemu_cmd(max_mem_rt, tg_size):
        """
        Check qemu command line options.
        :param max_mem_rt: size of max memory
        :param tg_size: Target hotplug memory size
        :return: None
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        if discard:
            if libvirt_version.version_compare(7, 3, 0):
                cmd = cmd + " | grep " + '\\"discard-data\\":true'
            else:
                cmd += " | grep 'discard-data=yes'"
        elif max_mem_rt:
            cmd += (" | grep 'slots=%s,maxmem=%sk'"
                    % (max_mem_slots, max_mem_rt))
            if tg_size:
                size = int(tg_size) * 1024
                if huge_pages or discard or cold_plug_discard:
                    cmd_str = 'memdimm.\|memory-backend-file,id=ram-node.'
                    cmd += (" | grep 'memory-backend-file,id=%s' | grep 'size=%s"
                            % (cmd_str, size))
                else:
                    cmd_str = 'mem.\|memory-backend-ram,id=ram-node.'
                    cmd += (" | grep 'memory-backend-ram,id=%s' | grep 'size=%s"
                            % (cmd_str, size))

                if pg_size:
                    cmd += ",host-nodes=%s" % node_mask
                    if numa_memnode:
                        for node in numa_memnode:
                            if ('nodeset' in node and
                                    node['nodeset'] in node_mask):
                                cmd += ",policy=%s" % node['mode']
                    cmd += ".*pc-dimm,node=%s" % tg_node
                if mem_addr:
                    cmd += (".*slot=%s" %
                            (mem_addr['slot']))
                cmd += "'"
            if cold_plug_discard:
                cmd += " | grep 'discard-data=yes'"

        # Run the command
        result = process.run(cmd, shell=True, verbose=True, ignore_status=True)
        if result.exit_status:
            test.fail('Qemu command check fail.')

    def check_guest_meminfo(old_mem, check_option):
        """
        Check meminfo on guest.
        """
        assert old_mem is not None
        session = vm.wait_for_login()
        # Hot-plugged memory should be online by udev rules
        udev_file = "/lib/udev/rules.d/80-hotplug-cpu-mem.rules"
        udev_rules = ('SUBSYSTEM=="memory", ACTION=="add", TEST=="state",'
                      ' ATTR{state}=="offline", ATTR{state}="online"')
        cmd = ("grep memory %s || echo '%s' >> %s"
               % (udev_file, udev_rules, udev_file))
        session.cmd(cmd)
        # Wait a while for new memory to be detected.
        utils_misc.wait_for(
            lambda: vm.get_totalmem_sys(online) != int(old_mem), 30,
            first=20.0)
        new_mem = vm.get_totalmem_sys(online)
        session.close()
        logging.debug("Memtotal on guest: %s", new_mem)
        no_of_times = 1
        if at_times:
            no_of_times = at_times
        if check_option == "attach":
            if new_mem != int(old_mem) + (int(tg_size) * no_of_times):
                test.fail("Total memory on guest couldn't changed after "
                          "attach memory device")

        if check_option == "detach":
            if new_mem != int(old_mem) - (int(tg_size) * no_of_times):
                test.fail("Total memory on guest couldn't changed after "
                          "detach memory device")

    def check_dom_xml(at_mem=False, dt_mem=False):
        """
        Check domain xml options.
        """
        # Global variable to store max/current memory
        global new_max_mem
        global new_cur_mem
        if attach_option.count("config"):
            dom_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        else:
            dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        try:
            xml_max_mem_rt = int(dom_xml.max_mem_rt)
            xml_max_mem = int(dom_xml.max_mem)
            xml_cur_mem = int(dom_xml.current_mem)
            assert int(max_mem_rt) == xml_max_mem_rt

            # Check attached/detached memory
            logging.info("at_mem=%s,dt_mem=%s", at_mem, dt_mem)
            logging.info("detach_device is %s", detach_device)
            if at_mem:
                if at_times:
                    assert int(max_mem) + (int(tg_size) *
                                           at_times) == xml_max_mem
                else:
                    assert int(max_mem) + int(tg_size) == xml_max_mem
                # Bug 1220702, skip the check for current memory
                if at_times:
                    assert int(cur_mem) + (int(tg_size) *
                                           at_times) == xml_cur_mem
                else:
                    assert int(cur_mem) + int(tg_size) == xml_cur_mem
                new_max_mem = xml_max_mem
                new_cur_mem = xml_cur_mem
                mem_dev = dom_xml.get_devices("memory")
                memory_devices = 1
                if at_times:
                    memory_devices = at_times
                if len(mem_dev) != memory_devices:
                    test.fail("Found wrong number of memory device")
                assert int(tg_size) == int(mem_dev[0].target.size)
                assert int(tg_node) == int(mem_dev[0].target.node)
            elif dt_mem:
                if at_times:
                    assert int(new_max_mem) - (int(tg_size) *
                                               at_times) == xml_max_mem
                    assert int(new_cur_mem) - (int(tg_size) *
                                               at_times) == xml_cur_mem
                else:
                    assert int(new_max_mem) - int(tg_size) == xml_max_mem
                    # Bug 1220702, skip the check for current memory
                    assert int(new_cur_mem) - int(tg_size) == xml_cur_mem
        except AssertionError:
            utils_misc.log_last_traceback()
            test.fail("Found unmatched memory setting from domain xml")

    def check_mem_align():
        """
        Check if set memory align to 256
        """
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        dom_mem = {}
        dom_mem['maxMemory'] = int(dom_xml.max_mem_rt)
        dom_mem['memory'] = int(dom_xml.memory)
        dom_mem['currentMemory'] = int(dom_xml.current_mem)

        cpuxml = dom_xml.cpu
        numa_cell = cpuxml.numa_cell
        dom_mem['numacellMemory'] = int(numa_cell[0]['memory'])
        sum_numa_mem = sum([int(cell['memory']) for cell in numa_cell])

        attached_mem = dom_xml.get_devices(device_type='memory')[0]
        dom_mem['attached_mem'] = attached_mem.target.size

        all_align = True
        for key in dom_mem:
            logging.info('%-20s:%15d', key, dom_mem[key])
            if dom_mem[key] % 262144:
                logging.error('%s not align to 256', key)
                if key == 'currentMemory':
                    continue
                all_align = False

        if not all_align:
            test.fail('Memory not align to 256')

        if dom_mem['memory'] == sum_numa_mem + dom_mem['attached_mem']:
            logging.info('Check Pass: Memory is equal to (all numa memory + memory device)')
        else:
            test.fail('Memory is not equal to (all numa memory + memory device)')

        return dom_mem

    def check_save_restore():
        """
        Test save and restore operation
        """
        save_file = os.path.join(data_dir.get_tmp_dir(),
                                 "%s.save" % vm_name)
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        def _wait_for_restore():
            try:
                virsh.restore(save_file, debug=True, ignore_status=False)
                return True
            except Exception as e:
                logging.error(e)
        utils_misc.wait_for(_wait_for_restore, 30, step=5)
        if os.path.exists(save_file):
            os.remove(save_file)
        # Login to check vm status
        vm.wait_for_login().close()

    def add_device(dev_xml, attach, at_error=False):
        """
        Add memory device by attachment or modify domain xml.
        """
        if attach:
            ret = virsh.attach_device(vm_name, dev_xml.xml,
                                      flagstr=attach_option,
                                      debug=True)
            libvirt.check_exit_status(ret, at_error)
        else:
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            if numa_cells:
                del vmxml.max_mem
                del vmxml.current_mem
            vmxml.add_device(dev_xml)
            vmxml.sync()

    def modify_domain_xml():
        """
        Modify domain xml and define it.
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        mem_unit = params.get("mem_unit", "KiB")
        vcpu = params.get("vcpu", "4")
        if max_mem_rt:
            vmxml.max_mem_rt = int(max_mem_rt)
            vmxml.max_mem_rt_slots = max_mem_slots
            vmxml.max_mem_rt_unit = mem_unit
        if memory_val:
            vmxml.memory = int(memory_val)
        if vcpu:
            vmxml.vcpu = int(vcpu)
            vcpu_placement = params.get("vcpu_placement", "static")
            vmxml.placement = vcpu_placement
        if numa_memnode:
            vmxml.numa_memory = {}
            vmxml.numa_memnode = numa_memnode
        else:
            try:
                del vmxml.numa_memory
                del vmxml.numa_memnode
            except Exception:
                # Not exists
                pass

        if numa_cells:
            cells = [ast.literal_eval(x) for x in numa_cells]
            # Rounding the numa memory values
            if align_mem_values:
                for cell in range(cells.__len__()):
                    memory_value = str(utils_numeric.align_value(
                        cells[cell]["memory"],
                        align_to_value))
                    cells[cell]["memory"] = memory_value
            cpu_xml = vm_xml.VMCPUXML()
            cpu_xml.xml = "<cpu mode='host-model'><numa/></cpu>"
            cpu_mode = params.get("cpu_mode")
            model_fallback = params.get("model_fallback")
            if cpu_mode:
                cpu_xml.mode = cpu_mode
            if model_fallback:
                cpu_xml.fallback = model_fallback
            cpu_xml.numa_cell = cpu_xml.dicts_to_cells(cells)
            vmxml.cpu = cpu_xml
            # Delete memory and currentMemory tag,
            # libvirt will fill it automatically
            del vmxml.max_mem
            del vmxml.current_mem

        # hugepages setting
        if huge_pages or discard or cold_plug_discard:
            membacking = vm_xml.VMMemBackingXML()
            membacking.discard = True
            membacking.source = ''
            membacking.source_type = 'file'
            if huge_pages:
                hugepages = vm_xml.VMHugepagesXML()
                pagexml_list = []
                for i in range(len(huge_pages)):
                    pagexml = hugepages.PageXML()
                    pagexml.update(huge_pages[i])
                    pagexml_list.append(pagexml)
                hugepages.pages = pagexml_list
                membacking.hugepages = hugepages
            vmxml.mb = membacking

        logging.debug("vm xml: %s", vmxml)
        vmxml.sync()

    pre_vm_state = params.get("pre_vm_state", "running")
    attach_device = "yes" == params.get("attach_device", "no")
    detach_device = "yes" == params.get("detach_device", "no")
    detach_alias = "yes" == params.get("detach_alias", "no")
    detach_alias_options = params.get("detach_alias_options")
    attach_error = "yes" == params.get("attach_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    detach_error = "yes" == params.get("detach_error", "no")
    maxmem_error = "yes" == params.get("maxmem_error", "no")
    attach_option = params.get("attach_option", "")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    wait_before_save_secs = int(params.get("wait_before_save_secs", 0))
    test_managedsave = "yes" == params.get("test_managedsave", "no")
    test_save_restore = "yes" == params.get("test_save_restore", "no")
    test_mem_binding = "yes" == params.get("test_mem_binding", "no")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    add_mem_device = "yes" == params.get("add_mem_device", "no")
    test_dom_xml = "yes" == params.get("test_dom_xml", "no")
    max_mem = params.get("max_mem")
    max_mem_rt = params.get("max_mem_rt")
    max_mem_slots = params.get("max_mem_slots", "16")
    memory_val = params.get('memory_val', '')
    mem_align = 'yes' == params.get('mem_align', 'no')
    hot_plug = 'yes' == params.get('hot_plug', 'no')
    cur_mem = params.get("current_mem")
    numa_cells = params.get("numa_cells", "").split()
    set_max_mem = params.get("set_max_mem")
    align_mem_values = "yes" == params.get("align_mem_values", "no")
    align_to_value = int(params.get("align_to_value", "65536"))
    hot_reboot = "yes" == params.get("hot_reboot", "no")
    rand_reboot = "yes" == params.get("rand_reboot", "no")
    guest_known_unplug_errors = []
    guest_known_unplug_errors.append(params.get("guest_known_unplug_errors"))
    host_known_unplug_errors = []
    host_known_unplug_errors.append(params.get("host_known_unplug_errors"))
    discard = "yes" == params.get("discard", "no")
    cold_plug_discard = "yes" == params.get("cold_plug_discard", "no")
    if cold_plug_discard or discard:
        mem_discard = 'yes'
    else:
        mem_discard = 'no'

    # params for attached device
    mem_model = params.get("mem_model", "dimm")
    tg_size = params.get("tg_size")
    tg_sizeunit = params.get("tg_sizeunit", 'KiB')
    tg_node = params.get("tg_node", 0)
    pg_size = params.get("page_size")
    pg_unit = params.get("page_unit", "KiB")
    huge_page_num = int(params.get('huge_page_num', 2000))
    node_mask = params.get("node_mask", "0")
    mem_addr = ast.literal_eval(params.get("memory_addr", "{}"))
    huge_pages = [ast.literal_eval(x)
                  for x in params.get("huge_pages", "").split()]
    numa_memnode = [ast.literal_eval(x)
                    for x in params.get("numa_memnode", "").split()]
    at_times = int(params.get("attach_times", 1))
    online = params.get("mem_online", "no")

    config = utils_config.LibvirtQemuConfig()
    setup_hugepages_flag = params.get("setup_hugepages")
    if (setup_hugepages_flag == "yes"):
        cpu_arch = cpu_util.get_family() if hasattr(cpu_util, 'get_family')\
            else cpu_util.get_cpu_arch()
        if cpu_arch == 'power8':
            pg_size = '16384'
            huge_page_num = 200
        elif cpu_arch == 'power9':
            pg_size = '2048'
            huge_page_num = 2000
        [x.update({'size': pg_size}) for x in huge_pages]
        setup_hugepages(int(pg_size), shp_num=huge_page_num)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    if not libvirt_version.version_compare(1, 2, 14):
        test.cancel("Memory hotplug not supported in current libvirt version.")

    if 'align_256m' in params.get('name', ''):
        arch = platform.machine()
        if arch.lower() != 'ppc64le':
            test.cancel('This case is for ppc64le only.')

    if align_mem_values:
        # Rounding the following values to 'align'
        max_mem = utils_numeric.align_value(max_mem, align_to_value)
        max_mem_rt = utils_numeric.align_value(max_mem_rt, align_to_value)
        cur_mem = utils_numeric.align_value(cur_mem, align_to_value)
        tg_size = utils_numeric.align_value(tg_size, align_to_value)

    try:
        # Drop caches first for host has enough memory
        drop_caches()
        # Destroy domain first
        if vm.is_alive():
            vm.destroy(gracefully=False)
        modify_domain_xml()
        numa_info = utils_misc.NumaInfo()
        logging.debug(numa_info.get_all_node_meminfo())

        # Start the domain any way if attach memory device
        old_mem_total = None
        if attach_device:
            vm.start()
            session = vm.wait_for_login()
            old_mem_total = vm.get_totalmem_sys(online)
            logging.debug("Memtotal on guest: %s", old_mem_total)
            session.close()
        elif discard:
            vm.start()
            session = vm.wait_for_login()
            check_qemu_cmd(max_mem_rt, tg_size)
        dev_xml = None

        # To attach the memory device.
        if (add_mem_device and not hot_plug) or cold_plug_discard:
            at_times = int(params.get("attach_times", 1))
            randvar = 0
            if rand_reboot:
                rand_value = random.randint(15, 25)
                logging.debug("reboots at %s", rand_value)
            for x in xrange(at_times):
                # If any error excepted, command error status should be
                # checked in the last time
                device_alias = "ua-" + str(uuid.uuid4())
                dev_xml = utils_hotplug.create_mem_xml(tg_size, pg_size,
                                                       mem_addr, tg_sizeunit,
                                                       pg_unit, tg_node,
                                                       node_mask, mem_model,
                                                       mem_discard,
                                                       device_alias)
                randvar = randvar + 1
                logging.debug("attaching device count = %s", x)
                if x == at_times - 1:
                    add_device(dev_xml, attach_device, attach_error)
                else:
                    add_device(dev_xml, attach_device)
                if hot_reboot:
                    vm.reboot()
                    vm.wait_for_login()
                if rand_reboot and randvar == rand_value:
                    vm.reboot()
                    vm.wait_for_login()
                    randvar = 0
                    rand_value = random.randint(15, 25)
                    logging.debug("reboots at %s", rand_value)

        # Check domain xml after attach device.
        if test_dom_xml:
            check_dom_xml(at_mem=attach_device)

        # Set domain state
        if pre_vm_state == "transient":
            logging.info("Creating %s...", vm_name)
            vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml,
                            **virsh_dargs).exit_status:
                vmxml_backup.define()
                test.fail("Cann't create the domain")
        elif vm.is_dead():
            try:
                vm.start()
                vm.wait_for_login().close()
            except virt_vm.VMStartError as detail:
                if start_error:
                    pass
                else:
                    except_msg = "memory hotplug isn't supported by this QEMU binary"
                    if except_msg in detail.reason:
                        test.cancel(detail)
                    test.fail(detail)

        # Set memory operation
        if set_max_mem:
            max_mem_option = params.get("max_mem_option", "")
            ret = virsh.setmaxmem(vm_name, set_max_mem,
                                  flagstr=max_mem_option)
            libvirt.check_exit_status(ret, maxmem_error)

        # Hotplug memory device
        if add_mem_device and hot_plug:
            process.run('ps -ef|grep qemu', shell=True, verbose=True)
            session = vm.wait_for_login()
            original_mem = vm.get_totalmem_sys()
            dev_xml = utils_hotplug.create_mem_xml(tg_size, pg_size,
                                                   mem_addr, tg_sizeunit,
                                                   pg_unit, tg_node,
                                                   node_mask, mem_model)
            add_device(dev_xml, True)
            mem_after = vm.get_totalmem_sys()
            params['delta'] = mem_after - original_mem

        # Check domain xml after start the domain.
        if test_dom_xml:
            check_dom_xml(at_mem=attach_device)

        if mem_align:
            dom_mem = check_mem_align()
            check_qemu_cmd(dom_mem['maxMemory'], dom_mem['attached_mem'])
            if hot_plug and params['delta'] != dom_mem['attached_mem']:
                test.fail('Memory after attach not equal to original mem + attached mem')

        # Check qemu command line
        if test_qemu_cmd:
            check_qemu_cmd(max_mem_rt, tg_size)

        # Check guest meminfo after attachment
        if (attach_device and not attach_option.count("config") and
                not any([attach_error, start_error])):
            check_guest_meminfo(old_mem_total, check_option="attach")

        # Consuming memory on guest,
        # to verify memory changes by numastat
        if test_mem_binding:
            pid = vm.get_pid()
            old_numastat = read_from_numastat(pid, "Total")
            logging.debug("Numastat: %s", old_numastat)
            # Increase the memory consumed to  1500
            consume_vm_mem(1500)
            new_numastat = read_from_numastat(pid, "Total")
            logging.debug("Numastat: %s", new_numastat)
            # Only check total memory which is the last element
            if float(new_numastat[-1]) - float(old_numastat[-1]) < 0:
                test.fail("Numa memory can't be consumed on guest")

        # Run managedsave command to check domain xml.
        if test_managedsave:
            # Wait 10s for vm to be ready before managedsave
            time.sleep(wait_before_save_secs)
            ret = virsh.managedsave(vm_name, **virsh_dargs)
            libvirt.check_exit_status(ret)

            def _wait_for_vm_start():
                try:
                    vm.start()
                    return True
                except Exception as e:
                    logging.error(e)
            utils_misc.wait_for(_wait_for_vm_start, timeout=30, step=5)
            vm.wait_for_login().close()
            if test_dom_xml:
                check_dom_xml(at_mem=attach_device)

        # Run save and restore command to check domain xml
        if test_save_restore:
            # Wait 10s for vm to be ready before save
            time.sleep(wait_before_save_secs)
            check_save_restore()
            if test_dom_xml:
                check_dom_xml(at_mem=attach_device)

        # Check domain xml after restarting libvirtd
        if restart_libvirtd:
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            if test_dom_xml:
                check_dom_xml(at_mem=attach_device)

        # Detach the memory device
        unplug_failed_with_known_error = False
        if detach_device:
            dev_xml = utils_hotplug.create_mem_xml(tg_size, pg_size,
                                                   mem_addr, tg_sizeunit,
                                                   pg_unit, tg_node,
                                                   node_mask, mem_model,
                                                   mem_discard)
            for x in xrange(at_times):
                if not detach_alias:
                    ret = virsh.detach_device(vm_name, dev_xml.xml,
                                              flagstr=attach_option, debug=True)
                else:
                    ret = virsh.detach_device_alias(vm_name, device_alias,
                                                    detach_alias_options, debug=True)
                if ret.stderr and host_known_unplug_errors:
                    for known_error in host_known_unplug_errors:
                        if (known_error[0] == known_error[-1]) and \
                           known_error.startswith(("'")):
                            known_error = known_error[1:-1]
                        if known_error in ret.stderr:
                            unplug_failed_with_known_error = True
                            logging.debug("Known error occured in Host, while"
                                          " hot unplug: %s", known_error)
                if unplug_failed_with_known_error:
                    break
                try:
                    libvirt.check_exit_status(ret, detach_error)
                except Exception as detail:
                    dmesg_file = tempfile.mktemp(dir=data_dir.get_tmp_dir())
                    try:
                        session = vm.wait_for_login()
                        utils_misc.verify_dmesg(dmesg_log_file=dmesg_file,
                                                ignore_result=True,
                                                session=session, level_check=5)
                    except Exception:
                        session.close()
                        test.fail("After memory unplug Unable to connect to VM"
                                  " or unable to collect dmesg")
                    session.close()
                    if os.path.exists(dmesg_file):
                        with open(dmesg_file, 'r') as f:
                            flag = re.findall(
                                r'memory memory\d+?: Offline failed', f.read())
                        if not flag:
                            # The attached memory is used by vm, and it could
                            #  not be unplugged.The result is expected
                            os.remove(dmesg_file)
                            test.fail(detail)
                        unplug_failed_with_known_error = True
                        os.remove(dmesg_file)
            # Check whether a known error occured or not
            dmesg_file = tempfile.mktemp(dir=data_dir.get_tmp_dir())
            try:
                session = vm.wait_for_login()
                utils_misc.verify_dmesg(dmesg_log_file=dmesg_file,
                                        ignore_result=True, session=session,
                                        level_check=4)
            except Exception:
                session.close()
                test.fail("After memory unplug Unable to connect to VM"
                          " or unable to collect dmesg")
            session.close()
            if guest_known_unplug_errors and os.path.exists(dmesg_file):
                for known_error in guest_known_unplug_errors:
                    if (known_error[0] == known_error[-1]) and \
                       known_error.startswith(("'")):
                        known_error = known_error[1:-1]
                    with open(dmesg_file, 'r') as f:
                        if known_error in f.read():
                            unplug_failed_with_known_error = True
                            logging.debug("Known error occured, while hot"
                                          " unplug: %s", known_error)
            if test_dom_xml and not unplug_failed_with_known_error:
                check_dom_xml(dt_mem=detach_device)
                # Remove dmesg temp file
                if os.path.exists(dmesg_file):
                    os.remove(dmesg_file)
    except xcepts.LibvirtXMLError:
        if define_error:
            pass
    finally:
        # Delete snapshots.
        snapshot_lists = virsh.snapshot_list(vm_name)
        if len(snapshot_lists) > 0:
            libvirt.clean_up_snapshots(vm_name, snapshot_lists)
            for snap in snapshot_lists:
                virsh.snapshot_delete(vm_name, snap, "--metadata")

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        if (setup_hugepages_flag == "yes"):
            restore_hugepages()
        vmxml_backup.sync()
