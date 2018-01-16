import os
import re
import ast
import logging

from avocado.core import exceptions
from autotest.client import utils

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_numeric
from virttest import virt_vm
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.staging.utils_memory import drop_caches
from virttest.staging.utils_memory import read_from_numastat

from provider import libvirt_version


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

    def get_vm_memtotal(session):
        """
        Get guest total memory
        """
        proc_meminfo = session.cmd_output("cat /proc/meminfo")
        # verify format and units are expected
        return int(re.search(r'MemTotal:\s+(\d+)\s+[kK]B',
                             proc_meminfo).group(1))

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

    def check_qemu_cmd():
        """
        Check qemu command line options.
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        if max_mem_rt:
            cmd += (" | grep 'slots=%s,maxmem=%sk'"
                    % (max_mem_slots, max_mem_rt))
        if tg_size:
            size = int(tg_size) * 1024
            cmd_str = 'memdimm.\|memory-backend-ram,id=ram-node.'
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
                cmd += (".*slot=%s,addr=%s" %
                        (mem_addr['slot'], int(mem_addr['base'], 16)))
            cmd += "'"
        # Run the command
        utils.run(cmd)

    def check_guest_meminfo(old_mem):
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
            lambda: get_vm_memtotal(session) != int(old_mem), 20, first=15.0)
        new_mem = get_vm_memtotal(session)
        session.close()
        logging.debug("Memtotal on guest: %s", new_mem)
        no_of_times = 1
        if at_times:
            no_of_times = at_times
        if attach_device:
            if new_mem != int(old_mem) + (int(tg_size) * no_of_times):
                raise exceptions.TestFail("Total memory on guest couldn't"
                                          " changed after attach memory "
                                          "device")
        if detach_device:
            if new_mem != int(old_mem) - (int(tg_size) * no_of_times):
                raise exceptions.TestFail("Total memory on guest couldn't"
                                          " changed after detach memory "
                                          "device")

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
                    raise exceptions.TestFail("Found wrong number of"
                                              " memory device")
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
            utils.log_last_traceback()
            raise exceptions.TestFail("Found unmatched memory setting"
                                      " from domain xml")

    def check_save_restore():
        """
        Test save and restore operation
        """
        save_file = os.path.join(test.tmpdir,
                                 "%s.save" % vm_name)
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if os.path.exists(save_file):
            os.remove(save_file)
        # Login to check vm status
        vm.wait_for_login().close()

    def create_mem_xml():
        """
        Create memory device xml.
        """
        mem_xml = memory.Memory()
        mem_model = params.get("mem_model", "dimm")
        mem_xml.mem_model = mem_model
        if tg_size:
            tg_xml = memory.Memory.Target()
            tg_xml.size = int(tg_size)
            tg_xml.size_unit = tg_sizeunit
            # There is support for non-numa node
            if numa_cells:
                tg_xml.node = int(tg_node)
            mem_xml.target = tg_xml
        if pg_size:
            src_xml = memory.Memory.Source()
            src_xml.pagesize = int(pg_size)
            src_xml.pagesize_unit = pg_unit
            src_xml.nodemask = node_mask
            mem_xml.source = src_xml
        if mem_addr:
            mem_xml.address = mem_xml.new_mem_address(
                **{"attrs": mem_addr})

        logging.debug("Memory device xml: %s", mem_xml)
        return mem_xml.copy()

    def add_device(dev_xml, at_error=False):
        """
        Add memory device by attachment or modify domain xml.
        """
        if attach_device:
            ret = virsh.attach_device(vm_name, dev_xml.xml,
                                      flagstr=attach_option)
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
            cpu_xml.xml = "<cpu><numa/></cpu>"
            cpu_mode = params.get("cpu_mode")
            model_fallback = params.get("model_fallback")
            if cpu_mode:
                cpu_xml.mode = cpu_mode
            if model_fallback:
                cpu_xml.fallback = model_fallback
            cpu_xml.numa_cell = cells
            vmxml.cpu = cpu_xml
            # Delete memory and currentMemory tag,
            # libvirt will fill it automatically
            del vmxml.max_mem
            del vmxml.current_mem

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

        logging.debug("vm xml: %s", vmxml)
        vmxml.sync()

    pre_vm_state = params.get("pre_vm_state", "running")
    attach_device = "yes" == params.get("attach_device", "no")
    detach_device = "yes" == params.get("detach_device", "no")
    attach_error = "yes" == params.get("attach_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    detach_error = "yes" == params.get("detach_error", "no")
    maxmem_error = "yes" == params.get("maxmem_error", "no")
    attach_option = params.get("attach_option", "")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    test_managedsave = "yes" == params.get("test_managedsave", "no")
    test_save_restore = "yes" == params.get("test_save_restore", "no")
    test_mem_binding = "yes" == params.get("test_mem_binding", "no")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    add_mem_device = "yes" == params.get("add_mem_device", "no")
    test_dom_xml = "yes" == params.get("test_dom_xml", "no")
    max_mem = params.get("max_mem")
    max_mem_rt = params.get("max_mem_rt")
    max_mem_slots = params.get("max_mem_slots", "16")
    cur_mem = params.get("current_mem")
    numa_cells = params.get("numa_cells", "").split()
    set_max_mem = params.get("set_max_mem")
    align_mem_values = "yes" == params.get("align_mem_values", "no")
    align_to_value = int(params.get("align_to_value", "65536"))

    # params for attached device
    tg_size = params.get("tg_size")
    tg_sizeunit = params.get("tg_sizeunit", 'KiB')
    tg_node = params.get("tg_node", 0)
    pg_size = params.get("page_size")
    pg_unit = params.get("page_unit", "KiB")
    node_mask = params.get("node_mask", "0")
    mem_addr = ast.literal_eval(params.get("memory_addr", "{}"))
    huge_pages = [ast.literal_eval(x)
                  for x in params.get("huge_pages", "").split()]
    numa_memnode = [ast.literal_eval(x)
                    for x in params.get("numa_memnode", "").split()]
    at_times = int(params.get("attach_times", 1))

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    if not libvirt_version.version_compare(1, 2, 14):
        raise exceptions.TestSkipError("Memory hotplug not supported in"
                                       "current libvirt version.")

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

        # Start the domain any way if attach memory device
        old_mem_total = None
        if attach_device:
            vm.start()
            session = vm.wait_for_login()
            old_mem_total = get_vm_memtotal(session)
            logging.debug("Memtotal on guest: %s", old_mem_total)
            session.close()
        dev_xml = None

        # To attach the memory device.
        if add_mem_device:
            at_times = int(params.get("attach_times", 1))
            dev_xml = create_mem_xml()
            for x in xrange(at_times):
                # If any error excepted, command error status should be
                # checked in the last time
                if x == at_times - 1:
                    add_device(dev_xml, attach_error)
                else:
                    add_device(dev_xml)

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
                raise exceptions.TestFail("Cann't create the domain")
        elif vm.is_dead():
            try:
                vm.start()
                vm.wait_for_login().close()
            except virt_vm.VMStartError:
                if start_error:
                    pass
                else:
                    raise exceptions.TestFail("VM Failed to start"
                                              " for some reason!")

        # Set memory operation
        if set_max_mem:
            max_mem_option = params.get("max_mem_option", "")
            ret = virsh.setmaxmem(vm_name, set_max_mem,
                                  flagstr=max_mem_option)
            libvirt.check_exit_status(ret, maxmem_error)

        # Check domain xml after start the domain.
        if test_dom_xml:
            check_dom_xml(at_mem=attach_device)

        # Check qemu command line
        if test_qemu_cmd:
            check_qemu_cmd()

        # Check guest meminfo after attachment
        if (attach_device and not attach_option.count("config") and
                not any([attach_error, start_error])):
            check_guest_meminfo(old_mem_total)

        # Consuming memory on guest,
        # to verify memory changes by numastat
        if test_mem_binding:
            pid = vm.get_pid()
            old_numastat = read_from_numastat(pid, "Total")
            logging.debug("Numastat: %s", old_numastat)
            consume_vm_mem()
            new_numastat = read_from_numastat(pid, "Total")
            logging.debug("Numastat: %s", new_numastat)
            # Only check total memory which is the last element
            if float(new_numastat[-1]) - float(old_numastat[-1]) < 0:
                raise exceptions.TestFail("Numa memory can't be consumed"
                                          " on guest")

        # Run managedsave command to check domain xml.
        if test_managedsave:
            ret = virsh.managedsave(vm_name, **virsh_dargs)
            libvirt.check_exit_status(ret)
            vm.start()
            vm.wait_for_login().close()
            if test_dom_xml:
                check_dom_xml(at_mem=attach_device)

        # Run save and restore command to check domain xml
        if test_save_restore:
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
        if detach_device:
            if not dev_xml:
                dev_xml = create_mem_xml()
            for x in xrange(at_times):
                ret = virsh.detach_device(vm_name, dev_xml.xml,
                                          flagstr=attach_option)
                libvirt.check_exit_status(ret, detach_error)
            if test_dom_xml:
                check_dom_xml(dt_mem=detach_device)

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
        vmxml_backup.sync()
