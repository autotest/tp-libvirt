import logging
import os
import re
import socket


from avocado.core import exceptions
from avocado.core import data_dir
from avocado.utils import process

from virttest import ssh_key
from virttest import remote
from virttest import utils_package
from virttest import utils_selinux
from virttest import utils_conn
from virttest import utils_misc
from virttest import virsh
from virttest import virt_vm
from virttest import migration
from virttest import ceph
from virttest import libvirt_vm
from virttest import utils_secret

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest.utils_test import libvirt


def prepare_ceph_disk(ceph_params, remote_virsh_dargs, test, runner_on_target):
    """
    Prepare one image on remote ceph server with enabled or disabled auth
    And expose it to VM by network access

    :param ceph_params: parameter to setup ceph.
    :param remote_virsh_dargs: parameter to remote virsh.
    :param test: test itself.
    :param runner_on_target: remote runner
    """
    # Ceph server config parameters
    virsh_dargs = {'debug': True, 'ignore_status': True}
    mon_host = ceph_params.get('mon_host')
    client_name = ceph_params.get('client_name')
    client_key = ceph_params.get("client_key")
    vol_name = ceph_params.get("vol_name")
    disk_img = ceph_params.get("disk_img")
    key_file = ceph_params.get("key_file")
    disk_format = ceph_params.get("disk_format")
    key_opt = ""

    # Auth and secret config parameters.
    auth_user = ceph_params.get("auth_user")
    auth_key = ceph_params.get("auth_key")
    auth_type = ceph_params.get("auth_type")
    auth_usage = ceph_params.get("secret_usage")
    secret_uuid = ceph_params.get("secret_uuid")

    # Remote host parameters.
    remote_ip = ceph_params.get("server_ip")
    remote_user = ceph_params.get("server_user", "root")
    remote_pwd = ceph_params.get("server_pwd")

    # Clean up dirty secrets in test environments if there are.
    utils_secret.clean_up_secrets()

    # Install ceph-common package which include rbd command
    if utils_package.package_install(["ceph-common"]):
        if client_name and client_key:
            # Clean up dirty secrets on remote host.
            try:
                remote_virsh = virsh.VirshPersistent(**remote_virsh_dargs)
                utils_secret.clean_up_secrets(remote_virsh)
            except (process.CmdError, remote.SCPError) as detail:
                raise exceptions.TestError(detail)
            finally:
                logging.debug('clean up secret on remote host')
                remote_virsh.close_session()

            with open(key_file, 'w') as f:
                f.write("[%s]\n\tkey = %s\n" %
                        (client_name, client_key))
            key_opt = "--keyring %s" % key_file

            # Create secret xml
            sec_xml = secret_xml.SecretXML("no", "no")
            sec_xml.usage = auth_type
            sec_xml.usage_name = auth_usage
            sec_xml.uuid = secret_uuid
            sec_xml.xmltreefile.write()

            logging.debug("Secret xml: %s", sec_xml)
            ret = virsh.secret_define(sec_xml.xml)
            libvirt.check_exit_status(ret)

            secret_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                     ret.stdout.strip())[0].lstrip()
            logging.debug("Secret uuid %s", secret_uuid)
            if secret_uuid is None:
                test.fail("Failed to get secret uuid")

            # Set secret value
            ret = virsh.secret_set_value(secret_uuid, auth_key,
                                         **virsh_dargs)
            libvirt.check_exit_status(ret)

            # Create secret on remote host.
            local_path = sec_xml.xml
            remote_path = '/var/lib/libvirt/images/new_secret.xml'
            remote_folder = '/var/lib/libvirt/images'
            cmd = 'mkdir -p %s && chmod 777 %s && touch %s' % (remote_folder, remote_folder, remote_path)
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()

            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))
            remote.scp_to_remote(remote_ip, '22', remote_user,
                                 remote_pwd, local_path, remote_path,
                                 limit="", log_filename=None,
                                 timeout=600, interface=None)
            cmd = "/usr/bin/virsh secret-define --file %s" % remote_path
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

            # Set secret value on remote host.
            cmd = "/usr/bin/virsh secret-set-value --secret %s --base64 %s" % (secret_uuid, auth_key)
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()

            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

        # Delete the disk if it exists
        disk_src_name = "%s/%s" % (vol_name, disk_img)
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
               "{2}".format(mon_host, key_opt, disk_src_name))
        copy_key_config_cmd = "yes|cp %s /etc/ceph/ceph.conf" % key_file
        process.run(copy_key_config_cmd, ignore_status=True, shell=True)
        process.run(cmd, ignore_status=True, shell=True)

        # Convert the disk format
        first_disk_device = ceph_params.get('first_disk')
        blk_source = first_disk_device['source']
        disk_path = ("rbd:%s:mon_host=%s" %
                     (disk_src_name, mon_host))
        if auth_user and auth_key:
            disk_path += (":id=%s:key=%s" %
                          (auth_user, auth_key))
        disk_cmd = ("rbd -m %s %s info %s || qemu-img convert"
                    " -O %s %s %s" % (mon_host, key_opt,
                                      disk_src_name, disk_format,
                                      blk_source, disk_path))
        process.run(disk_cmd, ignore_status=False, shell=True)
        return (key_opt, secret_uuid)


def build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                   volume_name, disk_img=None, transport=None, auth=None):
    """
    Try to build disk xml

    :param vm_name: specified VM name.
    :param disk_format: disk format,e.g raw or qcow2
    :param host_ip: host ip address
    :param disk_src_protocol: access disk procotol ,e.g network or file
    :param volume_name: volume name
    :param disk_img: disk image name
    :param transport: transport pattern,e.g TCP
    :param auth: dict containing ceph parameters
    """
    # Delete existed disks first.
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks_dev = vmxml.get_devices(device_type="disk")
    for disk in disks_dev:
        vmxml.del_device(disk)

    disk_xml = Disk(type_name="network")
    driver_dict = {"name": "qemu",
                   "type": disk_format}
    disk_xml.driver = driver_dict
    disk_xml.target = {"dev": "vda", "bus": "virtio"}
    # If protocol is rbd,create ceph disk xml.
    if disk_src_protocol == "rbd":
        disk_xml.device = "disk"
        vol_name = volume_name
        source_dict = {"protocol": disk_src_protocol,
                       "name": "%s/%s" % (vol_name, disk_img)}
        host_dict = {"name": host_ip, "port": "6789"}
        if transport:
            host_dict.update({"transport": transport})
        if auth:
            disk_xml.auth = disk_xml.new_auth(**auth)
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": source_dict, "hosts": [host_dict]})
    # Add the new disk xml.
    vmxml.add_device(disk_xml)
    vmxml.sync()


def download_guest_image(test, guest_image_url, target_image_name="/mnt/cephfs/jeos-common-x86_64-latest.qcow2"):
    """
    Download given guest image file from url.

    :param test: test object.
    :param guest_image_url: downloaded image url.
    :param target_image_name: target iso path
    :return: downloaded target image path if succeed, otherwise test fail.
    """
    if utils_package.package_install("wget"):
        logging.debug('begin download guest images ..............................')

        def _download():
            download_cmd = ("wget %s -O %s" % (guest_image_url, target_image_name))
            if process.system(download_cmd, verbose=False, shell=True):
                test.error("Failed to download image file")
            return True
        utils_misc.wait_for(_download, timeout=900, ignore_errors=True)
        logging.debug('finish download guest images ..............................')
        return target_image_name
    else:
        test.fail("Fail to install wget")


def setup_or_cleanup_cephfs_disk(test, cephfs_params, vm, is_setup=True):
    """
    Setup or cleanup cephfs disk

    :param test: test object.
    :param cephfs_params: parameter to setup cephfs.
    :param vm: vm object
    :param is_setup: one boolean to show setup or clean up
    """
    # Cephfs config parameters
    mon_host = cephfs_params.get('mon_host')
    ceph_uri = "%s:6789:/" % (mon_host)
    mount_point = cephfs_params.get('cephfs_mount_dir')
    mount_options = "name=%s,secret=%s" % (cephfs_params.get("auth_user"), cephfs_params.get("auth_key"))

    server_ip = cephfs_params.get("server_ip")
    server_user = cephfs_params.get("server_user")
    server_pwd = cephfs_params.get("server_pwd")
    symlink_name = cephfs_params.get("cephfs_symlink")
    cephfs_create_symlink = "yes" == cephfs_params.get("cephfs_create_symlink", "no")

    default_guest_image_download_path = cephfs_params.get("default_guest_image_download_path")
    image_download = cephfs_params.get("guest_image_download_path")
    guest_image_download_path = default_guest_image_download_path if 'EXAMPLE' in image_download else image_download

    clean_up_cmd = "rm -rf %s/*" % mount_point
    if is_setup:
        ceph.cephfs_mount(ceph_uri, mount_point, mount_options, verbose=True, session=None)
        process.run(clean_up_cmd, ignore_status=True, shell=True)
        remote_ssh_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$", timeout=480)
        ceph.cephfs_mount(ceph_uri, mount_point, mount_options, verbose=True, session=remote_ssh_session)
        remote_ssh_session.cmd(clean_up_cmd, ok_status=[0, 1], ignore_all_errors=True)

        first_disk_img = os.path.basename(vm.get_first_disk_devices()['source'])
        cephfs_img_path = os.path.join(mount_point, first_disk_img)
        if cephfs_create_symlink:
            utils_misc.make_symlink(mount_point, symlink_name)
            utils_misc.make_symlink(mount_point, symlink_name, remote_ssh_session)
            cephfs_img_path = os.path.join(symlink_name, first_disk_img)
        remote_ssh_session.close()
        cephfs_backend_disk = {'disk_source_name': cephfs_img_path, 'enable_cache': 'no', 'driver_cache': 'unsafe'}
        replace_guest_image_url = "%s/%s" % (guest_image_download_path, first_disk_img)
        download_guest_image(test, replace_guest_image_url, "%s/%s" % (mount_point, first_disk_img))
        libvirt.set_vm_disk(vm, cephfs_backend_disk)
    else:
        if cephfs_create_symlink:
            utils_misc.rm_link(symlink_name)
            utils_misc.rm_link(symlink_name, remote_ssh_session)
        ceph.cephfs_umount(ceph_uri, mount_point, verbose=True, session=None)
        remote_ssh_session = remote.remote_login("ssh", server_ip, "22", server_user,
                                                 server_pwd, r"[\#\$]\s*$", timeout=480)
        ceph.cephfs_umount(ceph_uri, mount_point, verbose=True, session=remote_ssh_session)
        remote_ssh_session.close()


def run(test, params, env):
    """
    Test remote access with TCP, TLS connection
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm", "no")

    # Server and client parameters
    server_ip = params["server_ip"] = params.get("remote_ip")
    server_user = params["server_user"] = params.get("remote_user", "root")
    server_pwd = params["server_pwd"] = params.get("remote_pwd")
    client_ip = params["client_ip"] = params.get("local_ip")
    client_user = params["client_user"] = params.get("remote_user", "root")
    client_pwd = params["client_pwd"] = params.get("local_pwd")

    extra = params.get("virsh_migrate_extra")
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}

    # Ceph disk parameters
    driver = params.get("test_driver", "qemu")
    transport = params.get("transport")
    plus = params.get("conn_plus", "+")
    options = params.get("virsh_migrate_options", "--live --p2p --verbose --timeout=1200")
    virsh_options = params.get("virsh_options", "")
    func_name = None

    vol_name = params.get("vol_name")
    disk_src_protocol = params.get("disk_source_protocol")
    source_file = params.get("disk_source_file")
    disk_format = params.get("disk_format", "qcow2")
    mon_host = params.get("mon_host")
    ceph_key_opt = ""
    attach_disk = False
    # Disk XML file
    disk_xml = None

    # Ceph and cephfs  disk parameters
    ceph_disk = "yes" == params.get("ceph_disk")
    ceph_cephfs_disk = "yes" == params.get("ceph_cephfs_disk")

    # Set up SSH key
    remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                           server_user, server_pwd,
                                           r"[\#\$]\s*$")
    remote_session.close()

    # Check whether migrate back
    migr_vm_back = "yes" == params.get("migrate_vm_back", "no")

    # Params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
        params.get("migrate_dest_host"))
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
        params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    # Add local ssh to remote authorization file
    ssh_key.setup_remote_ssh_key(client_ip, client_user, client_pwd, server_ip, server_user, server_pwd)
    add_host_cmd = "ssh-keyscan -H %s >> ~/.ssh/known_hosts" % server_ip
    process.run(add_host_cmd, ignore_status=True, shell=True)

    # Set up remote ssh key and remote /etc/hosts file for bi-direction migration
    migr_vm_back = "yes" == params.get("migrate_vm_back", "no")
    if migr_vm_back:
        ssh_key.setup_remote_ssh_key(server_ip, server_user, server_pwd)
        remote_known_hosts_obj = ssh_key.setup_remote_known_hosts_file(client_ip,
                                                                       server_ip,
                                                                       server_user,
                                                                       server_pwd)

    # Reset Vm state if needed
    if vm.is_alive() and start_vm == "no":
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Setup migration context
    migrate_test = migration.MigrationTest()
    migrate_test.check_parameters(params)

    # For --postcopy enable
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (virsh_options, postcopy_options)
        func_name = virsh.migrate_postcopy

    # Install ceph-common on remote host machine.
    remote_ssh_session = remote.remote_login("ssh", server_ip, "22", server_user,
                                             server_pwd, r"[\#\$]\s*$")
    if not utils_package.package_install(["ceph-common"], remote_ssh_session):
        test.error("Failed ot install required packages on remote host")
    remote_ssh_session.close()

    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        # Get initial Selinux config flex bit
        LOCAL_SELINUX_ENFORCING_STATUS = utils_selinux.get_status()
        logging.info("previous local enforce :%s", LOCAL_SELINUX_ENFORCING_STATUS)
        cmd_result = remote.run_remote_cmd('getenforce', params, runner_on_target)
        REMOTE_SELINUX_ENFORCING_STATUS = cmd_result.stdout_text
        logging.info("previous remote enforce :%s", REMOTE_SELINUX_ENFORCING_STATUS)
        if ceph_disk:
            logging.info("Put local SELinux in permissive mode when test ceph migrating")
            utils_selinux.set_status("enforcing")

            logging.info("Put remote SELinux in permissive mode")
            cmd = "setenforce enforcing"
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            if status:
                test.Error("Failed to set SELinux "
                           "in permissive mode")

            # Prepare ceph disk.
            key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
            params['key_file'] = key_file
            params['first_disk'] = vm.get_first_disk_devices()
            ceph_key_opt, secret_uuid = prepare_ceph_disk(params, remote_virsh_dargs, test, runner_on_target)
            host_ip = params.get('mon_host')
            disk_image = params.get('disk_img')

            # Build auth information.
            auth_attrs = {}
            auth_attrs['auth_user'] = params.get("auth_user")
            auth_attrs['secret_type'] = params.get("secret_type")
            auth_attrs['secret_uuid'] = secret_uuid
            build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                           vol_name, disk_image, auth=auth_attrs)
        elif ceph_cephfs_disk:
            write_hostname_into_etc_host()
            setup_or_cleanup_cephfs_disk(test, params, vm, is_setup=True)
        try:
            if vm.is_dead():
                vm.start()
        except virt_vm.VMStartError as e:
            logging.info("Failed to start VM")
            test.fail("Failed to start VM: %s" % vm_name)

        vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
        logging.debug("**************The VM XML after started: \n%s", vm_xml_cxt)

        # Ensure the same VM name doesn't exist on remote host before migrating.
        destroy_vm_cmd = "virsh destroy %s" % vm_name
        remote.run_remote_cmd(destroy_vm_cmd, params, runner_on_target)

        # Trigger migration
        vm.wait_for_login(timeout=900).close()
        migrate_test.ping_vm(vm, params)

        vms = [vm]
        migrate_test.do_migration(vms, None, dest_uri, 'orderly',
                                  options, thread_timeout=1200,
                                  ignore_status=True, virsh_opt=virsh_options,
                                  func=func_name, extra_opts=extra,
                                  func_params=params)
        mig_result = migrate_test.ret
        migrate_test.check_result(mig_result, params)
        if migr_vm_back:
            ssh_connection = utils_conn.SSHConnection(server_ip=client_ip,
                                                      server_pwd=client_pwd,
                                                      client_ip=server_ip,
                                                      client_pwd=server_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()
            # Pre migration setup for local machine
            migrate_test.migrate_pre_setup(src_uri, params)
            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              virsh_options, src_uri)
            logging.debug("Start migrating: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            logging.info(output)
            if status:
                destroy_cmd = "virsh destroy %s" % vm_name
                remote.run_remote_cmd(destroy_cmd, params, runner_on_target)
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))
    finally:
        logging.info("Recovery test environment...................................")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()

        # Clean up of pre migration setup for local machine
        if migr_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migrate_test.migrate_pre_setup(src_uri, params,
                                           cleanup=True)
        # Ensure VM can be cleaned up on remote host even migrating fail.
        destroy_vm_cmd = "virsh destroy %s" % vm_name
        remote.run_remote_cmd(destroy_vm_cmd, params, runner_on_target)

        # Clean up ceph environment.
        if disk_src_protocol == "rbd":
            # Clean up secret
            utils_secret.clean_up_secrets()
            # Clean up dirty secrets on remote host if testing involve in ceph auth.
            client_name = params.get('client_name')
            client_key = params.get("client_key")
            if client_name and client_key:
                try:
                    remote_virsh = virsh.VirshPersistent(**remote_virsh_dargs)
                    utils_secret.clean_up_secrets(remote_virsh)
                except (process.CmdError, remote.SCPError) as detail:
                    test.Error(detail)
                finally:
                    remote_virsh.close_session()
            # Delete the disk if it exists.
            disk_src_name = "%s/%s" % (vol_name, params.get('disk_img'))
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(mon_host, ceph_key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)

        if LOCAL_SELINUX_ENFORCING_STATUS:
            logging.info("Restore SELinux in original mode")
            utils_selinux.set_status(LOCAL_SELINUX_ENFORCING_STATUS)
        if REMOTE_SELINUX_ENFORCING_STATUS:
            logging.info("Put remote SELinux in original mode")
            cmd = "yes yes | setenforce %s" % REMOTE_SELINUX_ENFORCING_STATUS
            remote.run_remote_cmd(cmd, params, runner_on_target)
        if ceph_cephfs_disk:
            setup_or_cleanup_cephfs_disk(test, params, vm, is_setup=False)
