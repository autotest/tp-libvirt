import logging as log
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test memory function
    """

    def setup_test_default(case):
        """
        Default setup for test cases

        :param case: test case
        """
        logging.info('No specific setup step for %s', case)

    def cleanup_test_default(case):
        """
        Default cleanup for test cases

        :param case: test case
        """
        logging.info('No specific cleanup step for %s', case)

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

    def run_test_memorybacking(case):
        """
        Test memory backing cases

        :param case: test case
        """
        if case == 'no_numa':
            # Verify <access mode='shared'/> is ignored
            # if no NUMA nodes are configured
            if libvirt_version.version_compare(7, 0, 0) or\
                    not libvirt_version.version_compare(5, 0, 0):
                test.cancel('This case is not supported by current libvirt.')
            access_mode = params.get('access_mode')

            # Setup memoryBacking
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.access_mode = access_mode
            hugepages = vm_xml.VMHugepagesXML()
            mem_backing.hugepages = hugepages
            vmxml.mb = mem_backing
            logging.debug('membacking xml is: %s', mem_backing)

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
                logging.debug('Currrent memory after define is %d', cur_mem)
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
                logging.debug(new_vmxml.current_mem)
                logging.debug(new_vmxml.memory)

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
            logging.debug(virsh.dumpxml(vm_name).stdout_text)

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
            logging.info('Buffers %d + Cached %d + SwapCached %d = %d kb',
                         meminfo['Buffers'],
                         meminfo['Cached'],
                         meminfo['SwapCached'],
                         tmp_sum
                         )

            # Compare and make sure error is within allowable range
            logging.info('disk_caches is %s', dommemstat['disk_caches'])
            allow_error = int(params.get('allow_error', 15))
            actual_error = (tmp_sum - int(dommemstat['disk_caches'])) / tmp_sum * 100
            logging.debug('Actual error: %.2f%%', actual_error)
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
            logging.debug(virsh.dumpxml(vm_name).stdout_text)

    def run_test_xml_check(case):
        """
        Test xml check related cases

        :param case: test case
        """
        if case == 'smbios':
            # Make sure previous xml settings exist after vm started
            cmp_list = ['os', 'sysinfo', 'idmap']
            virsh.start(vm_name, ignore_status=False)
            logging.debug(virsh.dumpxml(vm_name).stdout_text)
            newxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

            for attr in cmp_list:
                logging.debug('Comparing %s of 2 xmls: \n%s\n%s',
                              attr, getattr(vmxml, attr),
                              getattr(newxml, attr))
                if getattr(vmxml, attr) == getattr(newxml, attr):
                    logging.debug('Result: Equal.')
                else:
                    test.fail('Xml comparison of %s failed.', attr)

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
            logging.debug(dimm_device)
            vmxml.add_device(dimm_device)

        vmxml.sync()
        logging.debug(virsh.dumpxml(vm_name).stdout_text)
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
        vm_attrs = {k.replace('vmxml_', ''): int(v) if v.isdigit() else v
                    for k, v in params.items() if k.startswith('vmxml_')}
        cpu_attrs = eval(params.get('cpu_attrs', '{}'))
        vm_attrs.update({'cpu': cpu_attrs})
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

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
        logging.debug('Vmxml after started:\n%s',
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
