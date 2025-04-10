import os
import shutil

from aexpect import remote

from avocado.utils import process

from virttest import data_dir
from virttest import migration
from virttest import nfs
from virttest import ssh_key
from virttest import utils_conn
from virttest import utils_disk
from virttest import utils_libvirtd
from virttest import utils_iptables
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_nested
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

tcp_obj = None


def prepare_package_in_vm(vm):
    """
    Prepare libvirt/qemu package in vm

    :param vm: VM object
    """
    if not vm.is_alive():
        vm.start()
    vm_session = vm.wait_for_login()
    libvirt_nested.install_virt_pkgs(vm_session)
    utils_libvirtd.Libvirtd(all_daemons=True, session=vm_session).restart()
    vm_session.close()


def update_cpu_xml(vm_xml, cpu_mode, test, feature_list=None):
    """
    Update cpu xml

    :param vm_xml: VM xml
    :param cpu_mode: CPU mode
    :param test: Test object
    :param feature_list: Feature list
    :return: New VM xml
    """
    cpu_xml = vm_xml.cpu
    cpu_xml.mode = cpu_mode
    if feature_list:
        for key, value in feature_list.items():
            try:
                vmx_index = cpu_xml.get_feature_index(key)
            except Exception as detail:
                test.log.debug("Got a exception: %s", detail)
                cpu_xml.add_feature(name=key, policy=value)
            else:
                cpu_xml.set_feature(vmx_index, name=key, policy=value)
    vm_xml.cpu = cpu_xml
    test.log.debug("cpu xml: %s", vm_xml.cpu)
    vm_xml.sync()
    return vm_xml


def prepare_env_in_vm(vm, vm_hostname, mount_src, mount_dir, desturi_port):
    """
    Prepare env in vm

    :param vm: VM object
    :param vm_hostname: VM hostname
    :param mount_src: Mount source
    :param mount_dir: Mount dir
    :param desturi_port: Desturi port
    """
    vm_session = vm.wait_for_login()
    cmd = "mkdir -p %s" % mount_src
    vm_session.cmd_status_output(cmd)

    cmd = "mkdir -p %s" % mount_dir
    vm_session.cmd_status_output(cmd)

    cmd = "hostnamectl set-hostname %s" % vm_hostname
    vm_session.cmd_status_output(cmd)

    cmd = "setsebool virt_use_nfs 1 -P"
    vm_session.cmd_status_output(cmd)

    firewall_cmd = utils_iptables.Firewall_cmd(vm_session)
    firewall_cmd.add_service('nfs', permanent=True)
    firewall_cmd.add_port(desturi_port, 'tcp', permanent=True)

    vm_session.close()


def prepare_nfs_server_in_vm(vm_ip, params):
    """
    Prepare nfs server in vm

    :param vm_ip: VM ip
    :param params: Dictionary with the test parameters
    """
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    mount_src = params.get("nfs_mount_src")
    mount_dir = params.get("nfs_mount_dir")

    # prepare nfs server in vm
    nfs_server_params = {
        "nfs_setup": True,
        "nfs_mount_dir": mount_dir,
        "nfs_mount_src": mount_src,
        "nfs_mount_options": params.get("nfs_mount_options"),
        "nfs_server_ip": vm_ip,
        "nfs_server_pwd": server_pwd,
        "nfs_server_user": server_user,
        "run_mount": False,
        "setup_remote_nfs": "yes",
        "export_options": params.get("export_options")
    }

    nfs_server = nfs.Nfs(nfs_server_params)
    nfs_server.setup()


def prepare_both_vms(params, vm1, vm2, vm1_xml, vm2_xml, test):
    """
    Prepare vms

    :param params: Dictionary with the test parameters
    :param vm1: VM1 object
    :param vm2: VM2 object
    :param vm1_xml: VM1 xml
    :param vm2_xml: VM2 xml
    :param test: Test object
    """
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_user = params.get("client_user", "root")
    client_pwd = params.get("client_pwd")
    mount_src = params.get("nfs_mount_src")
    mount_dir = params.get("nfs_mount_dir")
    vm1_hostname = params.get("vm1_hostname")
    vm2_hostname = params.get("vm2_hostname")
    desturi_port = params.get("migrate_desturi_port")
    cpu_mode = params.get("cpu_mode")
    l1vm_feature_list = eval(params.get("l1vm_feature_list"))

    def prepare_single_vm(vm, vm_xml, vm_hostname, test):
        vm_xml = update_cpu_xml(vm_xml, cpu_mode, test, l1vm_feature_list)
        prepare_package_in_vm(vm)
        prepare_env_in_vm(vm, vm_hostname, mount_src, mount_dir, desturi_port)
        return vm.get_address()

    vm1_ip = prepare_single_vm(vm1, vm1_xml, vm1_hostname, test)
    vm2_ip = prepare_single_vm(vm2, vm2_xml, vm2_hostname, test)

    ssh_key.setup_ssh_key(vm1_ip, server_user, server_pwd, 22)
    ssh_key.setup_ssh_key(vm2_ip, client_user, client_pwd, 22)
    ssh_key.setup_remote_ssh_key(vm1_ip, server_user, server_pwd, vm2_ip, client_user, client_pwd, port=22)
    ssh_key.setup_remote_ssh_key(vm2_ip, client_user, client_pwd, vm1_ip, server_user, server_pwd, port=22)

    test.log.debug("Prepare nfs server in %s.", vm1_ip)
    prepare_nfs_server_in_vm(vm1_ip, params)

    hosts_dict = {"%s" % vm1_hostname: "%s" % vm1_ip, "%s" % vm2_hostname: "%s" % vm2_ip}
    mount_src = vm1_ip + ":" + mount_src
    for vm in (vm1, vm2):
        vm_session = vm.wait_for_login()
        utils_disk.mount(mount_src, mount_dir, fstype="nfs", session=vm_session)
        utils_net.map_hostname_ipaddress(hosts_dict, vm_session)
        vm_session.close()

    return vm1_ip, vm2_ip


def run(test, params, env):
    """
    This case is to verify that migration can succeed in a nested environment
    which both source and target host have special CPU.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps for cases

        """
        test.log.info("Setup steps for cases.")

        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        mount_dir = params.get("nfs_mount_dir")

        nonlocal vm1_ip, vm2_ip
        vm1_ip, vm2_ip = prepare_both_vms(params, vm1, vm2, vm1_xml, vm2_xml, test)

        # Prepare TCP connection in vm
        params.update({"server_ip": vm2_ip})
        params.update({"client_ip": vm1_ip})
        global tcp_obj
        tcp_obj = utils_conn.TCPConnection(params)
        tcp_obj.conn_setup()

        test.log.debug("Prepare %s image.", l2vm_name)
        disk_dict = {'source': {'attrs': {'file': (os.path.join(mount_dir, l2vm_name) + ".qcow2")}}}
        l2vm_xml.vm_name = l2vm_name
        libvirt_vmxml.modify_vm_device(l2vm_xml, 'disk', disk_dict, sync_vm=False)
        remote.scp_to_remote(vm1_ip, '22', server_user, server_pwd, l2vm_img, mount_dir)
        remote.scp_to_remote(vm1_ip, '22', server_user, server_pwd, l2vm_xml.xml, l2vm_xml.xml)

        dest_uri = "qemu+tcp://%s/system" % vm2_ip
        migration_test.migrate_pre_setup(dest_uri, params)

        desturi = "qemu+ssh://%s/system" % vm1_ip
        virsh.define(l2vm_xml.xml, uri=desturi, debug=True)
        virsh.start(l2vm_name, uri=desturi, debug=True)

    def run_migration():
        """
        Run migration

        """
        postcopy_options = params.get("postcopy_options")
        options = params.get("virsh_migrate_options")

        test.log.debug("Run migration.")
        desturi = "qemu+ssh://%s/system" % vm1_ip
        dest_uri = "qemu+tcp://%s/system" % vm2_ip
        ret = virsh.migrate(l2vm_name, dest_uri=dest_uri, uri=desturi, option=options, extra=postcopy_options, debug=True)
        libvirt.check_exit_status(ret)

    def verify_test():
        """
        Verify steps

        """
        def _check_l2vm_ip():
            ret = virsh.domifaddr(l2vm_name, uri=dest_uri, debug=True)
            if "ipv4" in ret.stdout_text.strip():
                return True
            else:
                return False

        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")

        test.log.debug("Verify steps.")
        dest_uri = "qemu+ssh://%s/system" % vm2_ip
        utils_misc.wait_for(_check_l2vm_ip, timeout=120, step=5)
        ret = virsh.domifaddr(l2vm_name, uri=dest_uri, debug=True)
        l2vm_ip = ret.stdout_text.strip().split(" ")[-1].split("/")[0]
        test.log.debug("l2vm_ip: %s", l2vm_ip)

        vm2_session = vm2.wait_for_login()
        vm2_session.cmd_status_output("yum install sshpass -y")
        status, out = vm2_session.cmd_status_output("sshpass -p %s ssh -o StrictHostKeyChecking=no %s@%s 'echo simple_disk_check >> /tmp/simple_disk_check'" % (server_pwd, server_user, l2vm_ip))
        vm2_session.close()
        if status != 0:
            test.fail("Failed to write l2vm: %s" % out)

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.debug("Cleanup test.")
        global tcp_obj
        if tcp_obj:
            tcp_obj.auto_recover = True
            del tcp_obj

        def revert_vm_setup(vm, vm_img, vm_img_bak):
            if vm.is_alive():
                vm.destroy(gracefully=False)
            shutil.copyfile(vm_img_bak, vm_img)
            os.remove(vm_img_bak)

        bak_vm2_xml.sync()
        if os.path.exists(l2vm_img):
            os.remove(l2vm_img)
        revert_vm_setup(vm2, vm2_img, vm2_img_bak)
        revert_vm_setup(vm1, vm1_img, vm1_img_bak)

        #set server/client IP back to configured values
        params.update({"server_ip": params.get("migrate_dest_host")})
        params.update({"client_ip": params.get("migrate_source_host")})

    vms = params.get("vms").split()
    if len(vms) >= 2:
        vm1_name = vms[0]
        vm2_name = vms[1]
    else:
        test.error("Wrong test configuration, there should be defined two VM names.")

    cpu_mode = params.get("cpu_mode")
    l2vm_name = params.get("l2vm_name")
    migration_test = migration.MigrationTest()
    vm1_ip = vm2_ip = None

    cmd = "virsh domcapabilities |grep vmx- |wc -l"
    ret = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
    if int(ret) < 76:
        test.error(f"Tested CPU capability {eval(params.get('l1vm_feature_list'))} not available on host.")

    vm1 = env.get_vm(vm1_name)
    vm2 = env.get_vm(vm2_name)

    vm1_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm1_name)
    vm2_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm2_name)
    bak_vm2_xml = vm2_xml.copy()

    l2vm_xml = vm1_xml.copy()
    l2vm_xml = update_cpu_xml(l2vm_xml, cpu_mode, test)

    vm1_img = vm1.get_first_disk_devices()['source']
    vm2_img = vm2.get_first_disk_devices()['source']
    test.log.debug("vm1 img: %s", vm1_img)
    test.log.debug("vm2 img: %s", vm2_img)

    # Backup l2vm image
    l2vm_img = os.path.join(os.path.join(data_dir.get_data_dir(), 'images'), l2vm_name) + '.qcow2'
    test.log.debug("l2vm img: %s", l2vm_img)
    shutil.copyfile(vm1_img, l2vm_img)
    # Backup vm1 image
    vm1_img_bak = vm1_img + ".bak"
    shutil.copyfile(vm1_img, vm1_img_bak)
    # Backup vm2 image
    vm2_img_bak = vm2_img + ".bak"
    shutil.copyfile(vm2_img, vm2_img_bak)

    try:
        setup_test()
        run_migration()
        verify_test()
    finally:
        cleanup_test()
