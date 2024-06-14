import logging as log
import os

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_nested
from virttest.utils_misc import cmd_status_output


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run_cmd_in_guest(vm_session, cmd, test):
    """
    Run a command within the guest.

    :param vm_session: the vm session
    :param cmd: str, the command to be executed in the vm
    :param test:  test object
    :return: str, the output of the command
    """
    status, output = cmd_status_output(cmd, shell=True, session=vm_session)
    if status:
        test.error("Fail to run command '{}' with output '{}'".format(cmd, output))
    return output.strip()


def run_domcap_in_guest(virsh_dargs):
    """
    Execute 'virsh domcapabilities' within the guest

    :param virsh_dargs: dict, parameters used
    :return: CmdResult object, the execution result of the command
    """
    remote_session = None
    try:
        remote_session = virsh.VirshPersistent(**virsh_dargs)
        cmd_result = remote_session.domcapabilities(ignore_status=False)
    finally:
        if remote_session:
            remote_session.close_session()
    return cmd_result


def get_cmd_timestamp(cmd_timestamp):
    """
    Get the final command to search the timestamp.
    As there is only one qemu-kvm version, we only need to
    get the first file name

    :param cmd_timestamp: str, command including %s
    :return: str, the converted command
    """
    target_path = '/var/cache/libvirt/qemu/capabilities/'
    return cmd_timestamp % os.path.join(target_path, os.listdir(target_path)[0])


def get_timestamp_in_vm(virsh_dargs, vm_session, cmd_timestamp, test):
    """
    Get the timestamp within the vm

    :param virsh_dargs: dict, the parameters used
    :param vm_session:  vm session
    :param cmd_timestamp:  str, the command to get timestamp
    :param test: test object
    :return: str, the timestamp in vm
    """
    run_domcap_in_guest(virsh_dargs)
    timestamp = run_cmd_in_guest(vm_session, cmd_timestamp, test)
    logging.debug("The timestamp in vm is %s", timestamp)
    return timestamp


def run(test, params, env):
    """
    Run the cpu tests with nested virt environment setup
    """
    def test_change_vm_cpu():
        """
        Check domain capabilities after host CPU changes
        """
        vm_session = vm.wait_for_login()
        cmd_timestamp = params.get('cmd_in_guest')
        old_timestamp = get_timestamp_in_vm(virsh_dargs,
                                            vm_session,
                                            get_cmd_timestamp(cmd_timestamp),
                                            test)
        vm.destroy()
        if not params.get('cpu_new_mode'):
            test.error("'cpu_new_mode' parameter is missing")
        libvirt_nested.update_vm_cpu(vmxml, new_mode)
        vm.start()
        logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))
        vm_session = vm.wait_for_login()
        run_cmd_in_guest(vm_session, 'll /dev/kvm', test)
        new_timestamp = get_timestamp_in_vm(virsh_dargs,
                                            vm_session,
                                            get_cmd_timestamp(cmd_timestamp),
                                            test)
        if old_timestamp == new_timestamp:
            test.fail("The timestamp '{}' fails to be changed "
                      "from '{}'".format(new_timestamp, old_timestamp))

    def test_check_nested_capability():
        """
        Check whether the feature nested virt is enabled
        """
        vm.wait_for_login()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        cpu_feature = vmxml.cpu.get_dict_type_feature()
        if cpu_feature['vmx'] != 'require':
            test.fail("The feature policy of 'vmx' should not be %s, but require." % cpu_feature['vmx'])

        res = run_domcap_in_guest(virsh_dargs).stdout_text
        logging.debug("Domain domcapabilities: %s", res)
        matching_str = '<domain>kvm</domain>'
        if matching_str not in res:
            test.fail("The guest doesn't support kvm feature.")

    # Variable assignment
    new_mode = params.get('cpu_new_mode')
    old_mode = params.get('cpu_old_mode')
    the_case = params.get('case')

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Backup domain XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    if old_mode:
        libvirt_nested.update_vm_cpu(vmxml, old_mode)

    libvirt_nested.enable_nested_virt_on_host()

    try:
        if not vm.is_alive():
            vm.start()
        logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))
        vm_session = vm.wait_for_login()
        vm_ip = vm.get_address()
        vm_passwd = params.get('password')
        virsh_dargs = {'remote_ip': vm_ip, 'remote_user': 'root',
                       'remote_pwd': vm_passwd, 'unprivileged_user': None,
                       'ssh_remote_auth': True}

        libvirt_nested.install_virt_pkgs(vm_session)
        vm_session.close()
        # Reboot the vm to make sure default libvirt daemons start automatically
        vm.destroy()
        vm.start()

        # Test steps of cases
        run_test = eval('test_%s' % the_case)
        run_test()

    finally:
        logging.info("Recover VM XML configuration")
        vmxml_backup.sync()
