import os
import logging
import re
from autotest.client import lv_utils
from autotest.client.shared import ssh_key, error
from virttest import utils_v2v, libvirt_storage, libvirt_vm, virsh
from virttest import virt_vm, remote, data_dir
from virttest.utils_test import libvirt as utlv


def create_dir_pool(spool, pool_name, target_path):
    """
    Create a persistent dir pool.
    """
    # Check pool before creating
    if spool.pool_exists(pool_name):
        logging.debug("Pool '%s' already exists.", pool_name)
        return False

    if not spool.define_dir_pool(pool_name, target_path):
        return False

    if not spool.build_pool(pool_name):
        return False

    if not spool.start_pool(pool_name):
        return False

    if not spool.set_pool_autostart(pool_name):
        return False
    return True


def prepare_remote_sp(rsp, rvm, pool_name="v2v_test"):
    """
    v2v need remote vm's disk stored in a pool.

    :param rsp: remote storage pool's instance
    :param rvm: remote vm instance
    """
    # Get remote vms' disk path
    disks = rvm.get_disk_devices()
    target_path = ''
    for target in ['hda', 'sda', 'vda', 'xvda']:
        try:
            target_path = disks[target]["source"]
            logging.debug("System Disk:%s", target_path)
            target_path = os.path.dirname(target_path)
        except KeyError:
            continue
    if target_path:
        return create_dir_pool(rsp, pool_name, target_path)
    return False


def cleanup_vm(vm_name=None, disk=None):
    """
    Cleanup the vm with its disk deleted.
    """
    try:
        if vm_name is not None:
            virsh.undefine(vm_name)
    except error.CmdError:
        pass
    try:
        if disk is not None:
            os.remove(disk)
    except IOError:
        pass


def run(test, params, env):
    """
    Convert a remote vm to local libvirt(KVM).
    """
    # VM info
    xen_vm_name = params.get("v2v_xen_vm")
    vmware_vm_name = params.get("v2v_vmware_vm")

    # Remote host parameters
    xen_ip = params.get("remote_xen_ip", "XEN.EXAMPLE")
    vmware_ip = params.get("remote_vmware_ip", "VMWARE.EXAMPLE")
    username = params.get("username", "root")
    xen_pwd = params.get("remote_xen_pwd", "PWD.EXAMPLE")
    vmware_pwd = params.get("remote_vmware_pwd", "PWD.EXAMPLE")
    # To decide which type test it is
    remote_hypervisor = params.get("remote_hypervisor")

    # Local pool parameters
    pool_type = params.get("pool_type", "dir")
    pool_name = params.get("pool_name", "v2v_test")
    target_path = params.get("target_path", "pool_path")
    emulated_img = params.get("emulated_image_path", "v2v_emulated.img")
    emulated_size = params.get("emulated_image_size", "10G")

    # If target_path is not an abs path, join it to data_dir.tmpdir
    if os.path.dirname(target_path) is "":
        target_path = os.path.join(data_dir.get_tmp_dir(), target_path)

    # V2V parameters
    input = params.get("input_method")
    files = params.get("config_files")
    network = params.get("network")
    bridge = params.get("bridge")

    # Result check about
    ignore_virtio = "yes" == params.get("ignore_virtio", "no")

    # Create autologin to remote host
    esx_netrc = params.get("esx_netrc") % (vmware_ip, username, vmware_pwd)
    params['netrc'] = esx_netrc
    if remote_hypervisor == "esx":
        remote_ip = vmware_ip
        remote_pwd = vmware_pwd
        vm_name = vmware_vm_name
        if remote_ip.count("EXAMPLE") or remote_pwd.count("EXAMPLE"):
            raise error.TestNAError("Please provide host or password for "
                                    "vmware test.")
        utils_v2v.build_esx_no_verify(params)
    else:
        remote_ip = xen_ip
        remote_pwd = xen_pwd
        vm_name = xen_vm_name
        if remote_ip.count("EXAMPLE") or remote_pwd.count("EXAMPLE"):
            raise error.TestNAError("Please provide host or password for "
                                    "xen test.")
        ssh_key.setup_ssh_key(xen_ip, user=username, port=22,
                              password=xen_pwd)

    # Create remote uri for remote host
    # Remote virt-v2v uri's instance
    ruri = utils_v2v.Uri(remote_hypervisor)
    remote_uri = ruri.get_uri(remote_ip)

    # Check remote vms
    rvirsh_dargs = {'uri': remote_uri, 'remote_ip': remote_ip,
                    'remote_user': username, 'remote_pwd': remote_pwd}
    rvirsh = virsh.VirshPersistent(**rvirsh_dargs)
    if not rvirsh.domain_exists(vm_name):
        rvirsh.close_session()
        raise error.TestFail("Couldn't find vm '%s' to be converted "
                             "on remote uri '%s'." % (vm_name, remote_uri))

    if remote_hypervisor != "esx":
        remote_vm = libvirt_vm.VM(vm_name, params, test.bindir,
                                  env.get("address_cache"))
        remote_vm.connect_uri = remote_uri
        # Remote storage pool's instance
        rsp = libvirt_storage.StoragePool(rvirsh)
        # Put remote vm's disk into a directory storage pool
        prepare_remote_sp(rsp, remote_vm, pool_name)

    # Prepare local libvirt storage pool
    pvt = utlv.PoolVolumeTest(test, params)

    # Local storage pool's instance
    lsp = libvirt_storage.StoragePool()
    try:
        # Create storage pool for test
        pvt.pre_pool(pool_name, pool_type, target_path, emulated_img,
                     emulated_size)
        logging.debug(lsp.pool_info(pool_name))

        # Maintain a single params for v2v to avoid duplicate parameters
        v2v_params = {"hostname": remote_ip, "username": username,
                      "password": remote_pwd, "hypervisor": remote_hypervisor,
                      "storage": pool_name, "network": network,
                      "bridge": bridge, "target": "libvirt", "vms": vm_name,
                      "netrc": esx_netrc, "input": input, "files": files}
        try:
            result = utils_v2v.v2v_cmd(v2v_params)
            logging.debug(result)
        except error.CmdError, detail:
            raise error.TestFail("Virt v2v failed:\n%s" % str(detail))

        # v2v may be successful, but devices' driver may be not virtio
        error_info = []
        # Check v2v vm on local host
        # Update parameters for local hypervisor and vm
        logging.debug("XML info:\n%s", virsh.dumpxml(vm_name))
        params['vms'] = vm_name
        params['target'] = "libvirt"
        vm_check = utils_v2v.LinuxVMCheck(test, params, env)
        try:
            if not vm_check.is_disk_virtio():
                error_info.append("Error:disk type was not converted to "
                                  "virtio.")
            if not vm_check.is_net_virtio():
                error_info.append("Error:nic type was not converted to "
                                  "virtio.")
        except (remote.LoginError, virt_vm.VMError), detail:
            error_info.append(str(detail))

        # Close vm for cleanup
        if vm_check.vm is not None and vm_check.vm.is_alive():
            vm_check.vm.destroy()

        if not ignore_virtio and len(error_info):
            raise error.TestFail(error_info)
    finally:
        cleanup_vm(vm_name)
        try:
            # Create pool may be not in autostart, allowing to raise out
            pvt.cleanup_pool(pool_name, pool_type, target_path, emulated_img)
        except error.TestFail, detail:   # Make sure cleanup will be finished
            logging.warn(detail)
        if remote_hypervisor != "esx":
            rsp.delete_pool(pool_name)
        rvirsh.close_session()
