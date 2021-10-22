import logging

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test content:

    Create a domain when same domain has been defined before.
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

    def create_vm():
        """
        Create a domain and determine if it was successfully executed

        :return: bool
        """
        try:
            # check if vm can be created
            ret = virsh.create(vmxml.xml, ignore_status=True)
            if not ret.exit_status:
                logging.debug("Vm created successfully")
            else:
                logging.error("Vm failed to create")
                return False
        except Exception as e:
            test.error(str(e))
        domain_list_result = virsh.dom_list(options=" --all").stdout_text
        logging.debug("Total domains after creating vm: %s", domain_list_result)
        return True

    def run_test_ifacesource(case):
        """
        The network attribute of interface is incorrectly set to
        default1, check whether the domain can be created successfully.

        :param case: test case
        """
        if case == "modify_source":
            # Define xml
            cmd_result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_exit_status(cmd_result, status_error)

            modify_type = params.get('iface_modify_type')
            option_result = params.get('source_network_error')
            modify_dict = {'source': option_result}
            libvirt.modify_vm_iface(vm_name, modify_type, modify_dict)

            cmd_result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_exit_status(cmd_result, status_error)

            domain_list_result = virsh.dom_list(options=" --all").stdout_text
            logging.debug("Total domains before creating vm: %s", domain_list_result)

            create_vm()

    def process_run(cmd, vm_name, extra, extra_command):
        """
        Run the shell command.

        :param cmd: the command to get domain xml
        :param vm_name: the vm name
        :param extra: the other parameter of command
        :param extra_command: the other command after running cmd
        """
        try:
            virsh.dumpxml(vm_name, extra=extra, ignore_status=False)
            _, output = utils_misc.cmd_status_output(cmd.format(vm_name) + extra + extra_command,
                                                     ignore_status=False)
            logging.debug("The cmd running result: %s", "\n".join(output))
        except Exception as e:
            test.fail(str(e))

    def run_test_ifacedel(case):
        """
        Change the property to the correct parameter default,
        define it, and then delete the interface part in the
        XML file. After creating the domain, use dumpxml to
        check whether the interface exists in inactive and
        no-parameter cases.

        :param case: test case
        """
        if case == "delete_iface":
            # Define xml
            cmd_result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_exit_status(cmd_result, status_error)

            modify_type = params.get('iface_modify_type')
            option_result = params.get('source_network_right')
            modify_dict = {'source': option_result}
            libvirt.modify_vm_iface(vm_name, modify_type, modify_dict)

            cmd_result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_exit_status(cmd_result, status_error)

            domain_list_result = virsh.dom_list(options=" --all").stdout_text
            logging.debug("Total domains before creating vm: %s", domain_list_result)

            libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

            run_result = create_vm()
            extra_command = params.get("extra_command")
            if run_result:
                process_run("virsh dumpxml {} ", vm_name, "--inactive ", extra_command)
                process_run("virsh dumpxml {} ", vm_name, "", extra_command)

    vm_name = params.get('main_vm', "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    group = params.get('group', 'default')
    case = params.get('case', '')
    status_error = "yes" == params.get('status_error', 'no')
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
