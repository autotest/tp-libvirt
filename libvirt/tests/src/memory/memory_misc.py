import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.staging import utils_memory
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def set_vmxml(vmxml, params):
    """
    Setup vmxml for test

    :param vmxml: xml instance of vm
    :param params: params of test
    """
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    if not vm_attrs:
        vm_attrs = {k.replace('vmxml_', ''): int(v) if v.isdigit() else v
                    for k, v in params.items() if k.startswith('vmxml_')}
    cpu_attrs = eval(params.get('cpu_attrs', '{}'))
    vm_attrs.update({'cpu': cpu_attrs})
    vmxml.setup_attrs(**vm_attrs)
    vmxml.sync()


def set_hugepage(vm_mem_size):
    """
    Set number of hugepages according to hugepage size and vm memory size

    :param vm_mem_size: vm's memory size
    """
    page_size = utils_memory.get_huge_page_size()

    page_num = vm_mem_size // page_size
    utils_memory.set_num_huge_pages(page_num)


def run(test, params, env):
    """
    Test memory function
    """

    def setup_test_default(case):
        """
        Default setup for test cases

        :param case: test case
        """
        test.log.info('No specific setup step for %s', case)

    def cleanup_test_default(case):
        """
        Default cleanup for test cases

        :param case: test case
        """
        test.log.info('No specific cleanup step for %s', case)

    def check_result(cmd_result, status_error, error_msg=None):
        """
        Check command result including exit status and error message

        :param cmd_result: The result object to check
        :param status_error: The expected exit status, True to be failed
        :param error_msg: Expected error message
        """
        libvirt.check_exit_status(cmd_result, status_error)
        if error_msg:
            libvirt.check_result(cmd_result, error_msg)

    def setup_test_memorybacking(case):
        """
        Setup steps of memory backing tests

        :param case: test case
        """
        if case == 'prealloc_thread':
            vm_mem_size = vmxml.memory
            set_hugepage(vm_mem_size)

            # Setup memoryBacking of vmxml from attrs
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing_attrs = eval(params.get('mem_backing_attrs', '{}'))
            mem_backing.setup_attrs(**mem_backing_attrs)
            test.log.debug('memoryBacking xml is: %s', mem_backing)
            vmxml.mb = mem_backing

            vmxml.sync()
            test.log.debug(virsh.dumpxml(vm_name).stdout_text)

        if case == 'no_mem_backing':
            vm_mem_size = vmxml.memory
            set_hugepage(vm_mem_size)

            mem_device = Memory()
            mem_device_attrs = eval(params.get('mem_device_attrs'))
            mem_device.setup_attrs(**mem_device_attrs)

            vmxml.del_mb()
            vmxml.add_device(mem_device)
            set_vmxml(vmxml, params)

            test.log.debug(virsh.dumpxml(vm_name).stdout_text)

    def run_test_memorybacking(case):
        """
        Test memory backing cases

        :param case: test case
        """
        if case == 'no_numa':
            # Verify <access mode='shared'/> is ignored
            # if no NUMA nodes are configured
            if libvirt_version.version_compare(7, 0, 0) or \
                    not libvirt_version.version_compare(5, 0, 0):
                test.cancel('This case is not supported by current libvirt.')
            access_mode = params.get('access_mode')

            # Setup memoryBacking
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.access_mode = access_mode
            hugepages = vm_xml.VMHugepagesXML()
            mem_backing.hugepages = hugepages
            vmxml.mb = mem_backing
            test.log.debug('membacking xml is: %s', mem_backing)

            vmxml.xmltreefile.write()

            # Define xml
            cmd_result = virsh.define(vmxml.xml, debug=True)
            check_result(cmd_result, status_error, error_msg)

        if case == 'mem_lock':
            # Allow use mlock without hard limit
            hard_limit = params.get('hard_limit')
            hard_limit_unit = params.get('hard_limit_unit', 'KiB')

            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.locked = True
            vmxml.mb = mem_backing

            if hard_limit:
                mem_tune = vm_xml.VMMemTuneXML()
                mem_tune.hard_limit = int(hard_limit)
                mem_tune.hard_limit_unit = hard_limit_unit
                vmxml.memtune = mem_tune

            vmxml.sync()
            vm.start()

            output = process.run('prlimit -p `pidof qemu-kvm`',
                                 shell=True, verbose=True).stdout_text
            if not re.search(expect_msg, output):
                test.fail('Not found expected content "%s" in output.' % expect_msg)

        if case == 'prealloc_thread':
            virsh.start(vm_name, ignore_status=False)
            vm.wait_for_login().close()
            libvirt.check_qemu_cmd_line(qemu_check)

        if case == 'no_mem_backing':
            perm_before = process.run('ls /dev/hugepages/libvirt/qemu/ -ldZ',
                                      shell=True).stdout_text
            if 'root root' not in perm_before:
                test.fail('Permission should be "root root"')
            virsh.start(vm_name, **VIRSH_ARGS)

            perm_after = process.run('ls /dev/hugepages/libvirt/qemu/ -lZ',
                                     shell=True).stdout_text
            if 'qemu qemu' not in perm_after:
                test.fail('Permission should be "qemu qemu"')

            mem_device_attrs = eval(params.get('mem_device_attrs'))
            new_mem_device = Memory()
            new_mem_device.setup_attrs(**mem_device_attrs)

            mem_devices = vmxml.get_devices('memory')
            virsh.attach_device(vm_name, new_mem_device.xml, **VIRSH_ARGS)

            new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            mem_devices_after_attach = new_vmxml.get_devices('memory')
            test.log.debug(virsh.dumpxml(vm_name).stdout_text)

            test.log.info('Memory devices before attach(%d): %s\n'
                          'Memory devices after attatch(%d): %s',
                          len(mem_devices_after_attach),
                          mem_devices_after_attach,
                          len(mem_devices),
                          mem_devices)
            if len(mem_devices_after_attach) != len(mem_devices) + 1:
                test.fail('Attach memory device failed.')

    def run_test_edit_mem(case):
        """
        Test memory edit cases

        :param case: test case
        """
        if case == 'forbid_0':
            # Forbid to set memory to ZERO
            if scenario == 'set_mem':
                vmxml.memory = 0
                vmxml.xmltreefile.write()
                cmd_result = virsh.define(vmxml.xml, debug=True)
                check_result(cmd_result, status_error, error_msg)
            if scenario == 'set_cur_mem':
                vmxml.current_mem = 0
                vmxml.sync()
                new_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                cur_mem = new_vmxml.current_mem
                test.log.debug('Currrent memory after define is %d', cur_mem)
                if int(cur_mem) == 0:
                    test.fail('Current memory should not be 0.')
            if scenario == 'set_with_numa':
                numa_cells = eval(params.get('numa_cells', '[]'))
                if vmxml.xmltreefile.find('cpu'):
                    cpuxml = vmxml.cpu
                else:
                    cpuxml = vm_xml.VMCPUXML()
                cpuxml.numa_cell = cpuxml.dicts_to_cells(numa_cells)
                vmxml.cpu = cpuxml
                vmxml.vcpu = 4
                vmxml.memory = 0
                vmxml.current_mem = 0
                vmxml.sync()
                new_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                test.log.debug(new_vmxml.current_mem)
                test.log.debug(new_vmxml.memory)

    def run_test_dommemstat(case):
        """
        Test virsh command dommemstat related cases

        :param case: test case
        """
        if case == 'disk_caches':
            # Verify dommemstat show right disk cache for RHEL8 guest
            # Update memballoon device
            balloon_dict = {k: v for k, v in params.items()
                            if k.startswith('membal_')}
            libvirt.update_memballoon_xml(vmxml, balloon_dict)
            test.log.debug(virsh.dumpxml(vm_name).stdout_text)

            vm.start()
            session = vm.wait_for_login()

            # Get info from virsh dommemstat command
            dommemstat_output = virsh.dommemstat(
                vm_name, debug=True).stdout_text.strip()
            dommemstat = {}
            for line in dommemstat_output.splitlines():
                k, v = line.strip().split(' ')
                dommemstat[k] = v

            # Get info from vm
            meminfo_keys = ['Buffers', 'Cached', 'SwapCached']
            meminfo = {k: utils_misc.get_mem_info(session, k) for k in meminfo_keys}

            # from kernel commit: Buffers + Cached + SwapCached = disk_caches
            tmp_sum = meminfo['Buffers'] + meminfo['Cached'] + meminfo['SwapCached']
            test.log.info('Buffers %d + Cached %d + SwapCached %d = %d kb',
                          meminfo['Buffers'],
                          meminfo['Cached'],
                          meminfo['SwapCached'],
                          tmp_sum
                          )

            # Compare and make sure error is within allowable range
            test.log.info('disk_caches is %s', dommemstat['disk_caches'])
            allow_error = int(params.get('allow_error', 15))
            actual_error = (tmp_sum - int(dommemstat['disk_caches'])) / tmp_sum * 100
            test.log.debug('Actual error: %.2f%%', actual_error)
            if actual_error > allow_error:
                test.fail('Buffers + Cached + SwapCached (%d) '
                          'should be close to disk_caches (%s). '
                          'Allowable error: %.2f%%' %
                          (tmp_sum, dommemstat['disk_caches'], allow_error)
                          )

    def setup_test_xml_check(case):
        """
        Set up xml check related cases

        :param case: test case
        """
        if case == 'smbios':
            # Edit guest XML with smbios /sysinfo /idmap /metadata and memory device
            vmxml_attrs = {k.replace('vmxml_', ''): int(v) if v.isdigit() else v
                           for k, v in params.items() if k.startswith('vmxml_')}

            vmxml_attrs.update({
                'sysinfo': eval(params.get('sysinfo_attrs', '{}')),
                'os': eval(params.get('os_attrs', '{}')),
                'idmap': eval(params.get('idmap_attrs', '{}')),
                'cpu': eval(params.get('cpu_attrs', '{}'))
            })
            vmxml.setup_attrs(**vmxml_attrs)

            # Setup mem device
            memxml_attrs = eval(params.get('memxml_attrs', '{}'))
            memxml = Memory()
            memxml.setup_attrs(**memxml_attrs)
            vmxml.add_device(memxml)

            # Finish setting up vmxml
            vmxml.sync()
            test.log.debug(virsh.dumpxml(vm_name).stdout_text)

    def run_test_xml_check(case):
        """
        Test xml check related cases

        :param case: test case
        """
        if case == 'smbios':
            # Make sure previous xml settings exist after vm started
            cmp_list = ['os', 'sysinfo', 'idmap']
            virsh.start(vm_name, ignore_status=False)
            test.log.debug(virsh.dumpxml(vm_name).stdout_text)
            newxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

            for tag in cmp_list:
                old_xml = getattr(vmxml, tag)
                new_xml = getattr(newxml, tag)
                new_xml_attrs = new_xml.fetch_attrs()
                old_attrs = eval(params.get('%s_attrs' % tag))

                test.log.debug('Comparing attributes of %s of 2 xmls: \n%s\n%s\n%s\n%s',
                               tag, old_xml, old_attrs, new_xml, new_xml_attrs)
                if all([new_xml_attrs.get(k) == old_attrs[k] for k in old_attrs]):
                    test.log.debug('Result: Target xml settings are equal.')
                else:
                    test.fail('Xml comparison of %s failed.' % tag)

    def run_test_dimm(case):
        """
        Multi-operation for dimm devices

        :param case: test case
        """
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml.setup_attrs(**vm_attrs)

        # Start a guest with 3 dimm device
        dimm_devices_attrs = [eval(v) for k, v in params.items()
                              if k.startswith('dimm_device_')]
        for attrs in dimm_devices_attrs:
            dimm_device = Memory()
            dimm_device.setup_attrs(**attrs)
            test.log.debug(dimm_device)
            vmxml.add_device(dimm_device)

        vmxml.sync()
        test.log.debug(virsh.dumpxml(vm_name).stdout_text)
        vm.start()
        # Check qemu cmd line for amount of dimm device
        dimm_device_num = len(dimm_devices_attrs)
        qemu_cmd = 'pgrep -a qemu'
        qemu_cmd_output = process.run(qemu_cmd, verbose=True).stdout_text
        qemu_cmd_num = len(re.findall("-device.*?pc-dimm", qemu_cmd_output))
        if qemu_cmd_num != dimm_device_num:
            test.fail('The amount of dimm device in qemu command line does not'
                      ' match vmxml, expect %d, but get %d' % (dimm_device_num,
                                                               qemu_cmd_num))

        # Attach a mem device
        at_dimm_device_attrs = eval(params.get('at_dimm_device'))
        at_dim_device = Memory()
        at_dim_device.setup_attrs(**at_dimm_device_attrs)
        virsh.attach_device(vm_name, at_dim_device.xml, **VIRSH_ARGS)

        # Managedsave guest and restore
        virsh.managedsave(vm_name, **VIRSH_ARGS)
        virsh.start(vm_name, **VIRSH_ARGS)

        # Check qemu cmd line for attached dimm device
        new_qemu_cmd_output = process.run(qemu_cmd, verbose=True).stdout_text
        new_qemu_cmd_num = len(re.findall("-device.*?pc-dimm", new_qemu_cmd_output))
        if new_qemu_cmd_num != dimm_device_num + 1:
            test.fail('The amount of dimm device in qemu command line does not'
                      ' match vmxml, expect %d, but get %d' % (dimm_device_num + 1,
                                                               new_qemu_cmd_num))
        libvirt.check_qemu_cmd_line(qemu_check)

    def setup_test_audit_size(case):
        """
        Setup vmxml for test

        :param case: test case
        """
        set_vmxml(vmxml, params)

    def run_test_audit_size(case):
        """
        Audit memory size with memory hot-plug/unplug operations

        :param case: test case
        """
        numa_node_size = int(params.get('numa_node_size'))
        at_size = int(params.get('at_size'))
        current_mem = int(params.get('vmxml_current_mem'))
        audit_cmd = params.get('audit_cmd')
        dominfo_check_0 = params.get('dominfo_check_0') % (
            numa_node_size * 2, current_mem)
        dominfo_check_1 = params.get('dominfo_check_1') % (
            numa_node_size * 2 + at_size, current_mem + at_size
        )
        ausearch_check_1 = params.get('ausearch_check_1') % (
            0, numa_node_size * 2,
            numa_node_size * 2, numa_node_size * 2 + at_size
        )
        ausearch_check_2 = params.get('ausearch_check_2') % (
            numa_node_size * 2 + at_size, numa_node_size * 2 + at_size
        )
        dominfo_check_3 = dominfo_check_0
        ausearch_check_3 = params.get('ausearch_check_3') % (
            numa_node_size * 2 + at_size, numa_node_size * 2
        )

        # Start vm and wait for vm to bootup
        vm.start()
        vm.wait_for_login().close()
        test.log.debug('Vmxml after started:\n%s',
                       virsh.dumpxml(vm_name).stdout_text)

        # Check dominfo before hotplug mem device
        dominfo = virsh.dominfo(vm_name, **VIRSH_ARGS)
        libvirt.check_result(dominfo, expected_match=dominfo_check_0)

        # Prepare dimm memory devices to be attached
        dimm_devices = []
        for i in (0, 1):
            dimm_device = Memory()
            dimm_device_attrs = eval(
                params.get('dimm_device_%d_attrs' % i, '{}'))
            dimm_device.setup_attrs(**dimm_device_attrs)
            dimm_devices.append(dimm_device)

        def check_dominfo_and_ausearch(dominfo_check, ausearch_check):
            """
            Check output of virsh dominfo and ausearch command

            :param dominfo_check: patterns to search in dominfo output
            :param ausearch_check: patterns to search in ausearch output
            """
            if dominfo_check:
                dominfo = virsh.dominfo(vm_name, **VIRSH_ARGS)
                libvirt.check_result(dominfo, expected_match=dominfo_check)
            if ausearch_check:
                ausearch_result = process.run(audit_cmd,
                                              verbose=True, shell=True)
                libvirt.check_result(ausearch_result,
                                     expected_match=ausearch_check)

        # Hotplug dimm device to guest
        virsh.attach_device(vm_name, dimm_devices[1].xml, **VIRSH_ARGS)
        check_dominfo_and_ausearch(dominfo_check_1, ausearch_check_1)

        # Hotplug dimm device with size 0 G, should fail with error message
        at_result = virsh.attach_device(vm_name, dimm_devices[0].xml,
                                        debug=True)
        libvirt.check_result(at_result, error_msg)
        check_dominfo_and_ausearch(None, ausearch_check_2)

        # HotUnplug the dimm device
        virsh.detach_device(vm_name, dimm_devices[1].xml, **VIRSH_ARGS)
        check_dominfo_and_ausearch(dominfo_check_3, ausearch_check_3)

    def setup_test_managedsave(case):
        """
        Setup steps for test

        :param case: test case
        """
        set_vmxml(vmxml, params)

    def run_test_managedsave(case):
        """
        Test steps for:memory should not change after managedsave/restore

        :param case: test case
        """
        test.log.debug(virsh.dominfo(vm_name).stdout_text)
        vm.start()
        vm.wait_for_login().close()

        virsh.managedsave(vm_name, **VIRSH_ARGS)
        virsh.start(vm_name, **VIRSH_ARGS)
        test.log.debug(virsh.dumpxml(vm_name).stdout_text)

        # Current memory size should not change after managedsave and restore
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        new_current_mem = new_vmxml.current_mem
        set_current_mem = int(params.get('vmxml_current_mem'))
        test.log.debug('Set current mem: %d\nCurrent mem after managedsave: %d',
                       set_current_mem, new_current_mem)

        if new_current_mem != set_current_mem:
            test.fail('Size of current memory %d changed to %d after '
                      'managedsave' % (set_current_mem, new_current_mem))

    # Version check for current test
    libvirt_version.is_libvirt_feature_supported(params)

    # Variable assignment
    group = params.get('group', 'default')
    case = params.get('case', '')
    scenario = params.get('scenario', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    expect_msg = params.get('expect_msg', '')
    qemu_check = params.get('qemu_check')
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Get setup function
    setup_test = eval('setup_test_%s' % group) \
        if 'setup_test_%s' % group in locals() else setup_test_default
    # Get runtest function
    run_test = eval('run_test_%s' % group)
    # Get cleanup function
    cleanup_test = eval('cleanup_test_%s' % group) \
        if 'cleanup_test_%s' % group in locals() else cleanup_test_default

    try:
        # Execute test
        setup_test(case)
        run_test(case)

    finally:
        bkxml.sync()
        cleanup_test(case)
