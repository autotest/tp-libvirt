import logging as log

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


VIRSH_ARGS = {'debug': True, 'ignore_status': False}


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test memballoon device
    """

    def run_test_alias(case):
        """
        Test memballoon alias

        :param case: test case
        """
        model = params.get('model')
        alias_name = params.get('alias_name')
        has_alias = 'yes' == params.get('has_alias', 'no')

        # Update memballoon device
        balloon_dict = {
            'membal_model': model,
            'membal_alias_name': alias_name
        }

        libvirt.update_memballoon_xml(vmxml, balloon_dict)
        logging.debug(virsh.dumpxml(vm_name).stdout_text)

        # Get memballoon device after vm define and check
        balloons = vmxml.get_devices('memballoon')
        if len(balloons) == 0:
            test.error('Memballoon device was not added to vm.')
        new_balloon = balloons[0]
        if has_alias:
            logging.debug('Expected alias: %s\nActual alias: %s',
                          alias_name, new_balloon.alias_name)
            if new_balloon.alias_name == alias_name:
                logging.info('Memballon alias check PASS.')
            else:
                test.fail('Memballon alias check FAIL.')

        # Check vm start
        cmd_result = virsh.start(vm_name)
        libvirt.check_result(cmd_result, status_error)

    def run_test_period(case):
        """
        Present memballoon statistic period in live xml

        :param case: test case
        """

        def get_dommemstat_keys(output):
            """
            Get output keys of virsh dommemstat

            :param output: output of virsh dommemstat command
            :return: keys of the output
            """
            return set([line.split()[0] for line in output.strip().splitlines()])

        def get_balloon_n_memstatkeys(period):
            """
            Set period and then get memballoon device's xml and output keys of
            virsh dommemstat.

            :param period: value of period
            :return: tuple of memballoon xml and output keys of virsh dommemstat
            """
            virsh.dommemstat(vm_name, '--period %s' % period, **VIRSH_ARGS)
            vmxml_p = vm_xml.VMXML.new_from_dumpxml(vm_name)
            balloon_p = vmxml_p.get_devices('memballoon')[0]
            logging.debug('memballon xml of period %s is :\n%s',
                          period, balloon_p)
            memstat_keys_p = get_dommemstat_keys(
                virsh.dommemstat(vm_name, **VIRSH_ARGS).stdout_text)
            return balloon_p, memstat_keys_p

        if case == 'memstat':
            # Start a guest with memballoon element, it's automatically added.
            # Wait for vm to fully bootup to run test
            vm.wait_for_login().close()

            # Check the memballoon xml and dommemstat
            balloon_list = vmxml.get_devices('memballoon')
            if not balloon_list:
                test.error('There is no memballoon device in vmxml.')
            balloon = balloon_list[0]
            logging.debug('memballoon after vm started: \n%s', balloon)
            memstat_keys = get_dommemstat_keys(
                virsh.dommemstat(vm_name, **VIRSH_ARGS).stdout_text)

            # Set period to 2
            p2 = 2
            # Check the xml and the dommemstat
            balloon_p2, memstat_keys_p2 = get_balloon_n_memstatkeys(p2)
            if balloon_p2.xmltreefile.find('stats') is None \
                    or balloon_p2.stats_period != str(p2):
                test.fail('Period of memballoon check failed, should be %s'
                          % p2)

            # Set period to 0
            p0 = 0
            # Check the xml and the dommemstat
            balloon_p0, memstat_keys_p0 = get_balloon_n_memstatkeys(p0)
            stats_elem = balloon_p0.xmltreefile.find('stats')
            if stats_elem is not None:
                if stats_elem.get('period') is not None:
                    test.fail('Period of memballoon check failed, '
                              'there should not be period in memballoon xml'
                              ' after setting period to 0')

            # Fail test if output keys are not consistent
            if not memstat_keys == memstat_keys_p2 == memstat_keys_p0:
                test.fail('Output keys of dommemstat changed during test.')

    def cleanup_test(case):
        """
        Default cleanup for test cases

        :param case: test case
        """
        logging.info('Cleanup steps for test: %s', case)
        if params.get('destroy_after') == 'yes':
            vm.destroy()
        bkxml.sync()

    # Variable assignment
    group = params.get('group', 'default')
    case = params.get('case', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get('status_error', 'no')
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Get setup function
    setup_test = eval('setup_test_%s' % group) \
        if 'setup_test_%s' % group in locals() else lambda x: None
    # Get runtest function
    run_test = eval('run_test_%s' % group)

    try:
        # Execute test
        setup_test(case)
        run_test(case)

    finally:
        cleanup_test(case)
