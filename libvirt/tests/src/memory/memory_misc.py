import logging
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_test import libvirt


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
                    cpuxml = vm_xml.VMCPUXML
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

    # Variable assignment
    group = params.get('group', 'default')
    case = params.get('case', '')
    scenario = params.get('scenario', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    expect_msg = params.get('expect_msg', '')
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
