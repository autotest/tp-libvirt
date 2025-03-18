import logging as log
import os

from avocado.utils import process

from virttest import remote
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps            # pylint: disable=W0611

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_vtpm_func(params, vm, test, remote_host=False):
    """
    Check vtpm function

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param remote_host: True to check context on remote
    """
    tpm_cmd = params.get("tpm_cmd")
    dest_uri = params.get("virsh_migrate_desturi")
    src_uri = params.get("virsh_migrate_connect_uri")
    test.log.debug("Check vtpm func: %s (remote).", remote)
    if remote_host:
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.connect_uri = dest_uri
    if vm.serial_console is None:
        vm.create_serial_console()
    vm_session = vm.wait_for_serial_login(timeout=240)
    if not utils_package.package_install("tpm2-tools", vm_session):
        test.error("Failed to install tpm2-tools in vm")
    cmd_result = vm_session.cmd_status(tpm_cmd)
    vm_session.close()
    if remote_host:
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.connect_uri = src_uri
    if cmd_result:
        test.fail("Fail to run '%s': %s." % (tpm_cmd, cmd_result))


def setup_vtpm(params, test, vm):
    """
    Setup vTPM device in guest xml

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    """
    vm_name = params.get("migrate_main_vm")
    tpm_dict = eval(params.get('tpm_dict', '{}'))
    auth_sec_dict = params.get("auth_sec_dict")

    test.log.info("Setup vTPM device in guest xml.")
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Remove all existing tpm devices
    vmxml.remove_all_device_by_type('tpm')

    tpm_dict['backend']['encryption_secret'] = auth_sec_dict['sec_uuid']
    libvirt_vmxml.modify_vm_device(vmxml, 'tpm', tpm_dict)

    vm.start()
    vm.wait_for_login().close()


def set_secret(params):
    """
    Set secret

    :param params: dict, test parameters
    """
    auth_sec_dict = eval(params.get("auth_sec_dict"))
    src_secret_value = params.get("src_secret_value")
    src_secret_value_path = params.get("src_secret_value_path")
    dst_secret_value = params.get("dst_secret_value")
    dst_secret_value_path = params.get("dst_secret_value_path")
    remote_pwd = params.get("migrate_dest_pwd")
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("remote_user", "root")

    def _create_secret(session=None, remote_args=None, secret_value_path=None):
        """
        create secret

        :param session: a session object of remote host
        :param remote_args: remote host parameters
        :param secret_value_path: the path of secret value
        """
        src_sec_uuid = None
        dst_sec_uuid = None
        libvirt_secret.clean_up_secrets(session)
        if remote_args:
            dst_sec_uuid = libvirt.create_secret(auth_sec_dict, remote_args=remote_args)
        else:
            src_sec_uuid = libvirt.create_secret(auth_sec_dict)
            auth_sec_dict.update({"sec_uuid": src_sec_uuid})
        if os.path.exists(secret_value_path):
            os.remove(secret_value_path)
        if session:
            cmd = "echo '%s' > %s" % (dst_secret_value, secret_value_path)
            process.run(cmd, shell=True)
            remote.scp_to_remote(remote_ip, '22', remote_user, remote_pwd,
                                 secret_value_path, secret_value_path,
                                 limit="", log_filename=None, timeout=60,
                                 interface=None)
            cmd = f"virsh secret-set-value {dst_sec_uuid} --file {secret_value_path} --plain"
            remote.run_remote_cmd(cmd, params)
        else:
            cmd = "echo '%s' > %s" % (src_secret_value, secret_value_path)
            process.run(cmd, shell=True)
            cmd = f"virsh secret-set-value {src_sec_uuid} --file {secret_value_path} --plain"
            process.run(cmd, shell=True)
        return src_sec_uuid, dst_sec_uuid

    src_sec_uuid, _ = _create_secret(secret_value_path=src_secret_value_path)
    params.update({"auth_sec_dict": auth_sec_dict})
    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}
    remote_virsh_session = None
    remote_virsh_session = virsh.VirshPersistent(**virsh_dargs)
    _, dst_sec_uuid = _create_secret(session=remote_virsh_session, remote_args=virsh_dargs,
                                     secret_value_path=dst_secret_value_path)
    return src_sec_uuid, dst_sec_uuid


def compare_swtpm_version(major, minor):
    """
    Get swtpm pkg version and check whether > major.minor

    :param major: swtpm major version number
    :param minor: swtpm minor version number
    :return: boolean value compared to major.minor
    """
    cmd = 'rpm -q swtpm'
    v_swtpm = process.run(cmd).stdout_text.strip().split('-')
    v_major = int(v_swtpm[1].split('.')[0])
    v_minor = int(v_swtpm[1].split('.')[1])
    return False if (v_major == major and v_minor < minor) else True
