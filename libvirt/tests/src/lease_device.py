import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import lease


def create_lease(**attrs):
    """
    Create lease device

    :param attrs: attrs of lease device
    :return: xml object of lease device
    """
    lease_obj = lease.Lease()
    lease_obj.setup_attrs(**attrs)

    logging.debug('Lease xml is %s', lease_obj)
    return lease_obj


def run(test, params, env):
    """
    Test domain lease
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

    def run_test_at_dt(case):
        """
        Test attach-detach lease device

        :param case: test case
        """
        lease_args = {k.replace('lease_arg_', ''): v
                      for k, v in params.items()
                      if k.startswith('lease_arg_')}
        if 'target' in lease_args:
            lease_args['target'] = eval(lease_args['target'])
        lease_obj = create_lease(**lease_args)

        options = '' if vm.is_alive() else '--config'
        virsh.attach_device(vm_name, lease_obj.xml, flagstr=options, debug=True)

        # Check attach result
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        lease_list = vmxml.get_devices('lease')
        if not lease_list:
            test.fail('Not found lease device in vmxml after attach.')

        at_lease = lease_list[0]
        logging.debug('Before attach: \n%s\n'
                      'After attach: \n%s' % (lease_obj, at_lease))

        if at_lease != lease_obj:
            test.fail('Lease device xml changed after being attached.')

        # Test vm start
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
            vm.destroy()

        # Detach lease device and check xml
        virsh.detach_device(vm_name, lease_obj.xml, flagstr=options, debug=True)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if vmxml.get_devices('lease'):
            test.fail('Lease device still exists after being detached.')

    # Variable assignment
    group = params.get('group', 'default')
    case = params.get('case', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

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
