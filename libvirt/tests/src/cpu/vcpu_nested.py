import logging
import os

from virttest import libvirt_remote
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_nested
from virttest.utils_misc import cmd_status_output


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
    """
    remote_session = None
    try:
        remote_session = virsh.VirshPersistent(**virsh_dargs)
        remote_session.domcapabilities(ignore_status=False)
    finally:
        if remote_session:
            remote_session.close_session()


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


def configure_libvirtd(config_args):
    """
    Configure the libvirtd.conf file

    :param config_args: dict, the parameters to be used
    :return: RemoteFile object
    """
    libvirtd_conf_dict = config_args.get('libvirtd_conf_dict')
    server_params = {'server_ip': config_args.get('remote_ip'),
                     'server_user': config_args.get('remote_user'),
                     'server_pwd': config_args.get('remote_pwd')}
    libvirtd_conf_remote = libvirt_remote.update_remote_file(
        server_params, libvirtd_conf_dict, "/etc/libvirt/libvirtd.conf")

    return libvirtd_conf_remote


def run(test, params, env):
    """
    Run the cpu tests with nested virt environment setup
    """
    enable_libvirtd_debug_in_vm = params.get('enable_libvirtd_debug_in_vm')
    log_file = params.get("libvirtd_log", "/var/log/libvirt/libvirtd.log")
    new_mode = params.get('cpu_new_mode')
    old_mode = params.get('cpu_old_mode')
    search_str_in_vm_libvirtd = params.get('search_str_in_vm_libvirtd')
    the_case = params.get('case')

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    libvirtd_conf_in_vm = None

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
        if enable_libvirtd_debug_in_vm:
            cmd = 'rm -f %s' % log_file
            run_cmd_in_guest(vm_session, cmd, test)
            virsh_dargs.update({'libvirtd_conf_dict': params.get('libvirtd_conf_dict')})
            libvirtd_conf_in_vm = configure_libvirtd(virsh_dargs)
        if the_case == 'change_vm_cpu':
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
            if search_str_in_vm_libvirtd:
                cmd = "grep -E '{}' {}".format(search_str_in_vm_libvirtd, log_file)
                run_cmd_in_guest(vm_session, cmd, test)
    finally:
        if libvirtd_conf_in_vm:
            del libvirtd_conf_in_vm

        logging.info("Recover VM XML configuration")
        vmxml_backup.sync()
