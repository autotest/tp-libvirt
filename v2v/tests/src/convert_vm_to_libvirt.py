import os
import logging
from autotest.client import utils
from autotest.client.shared import ssh_key
from autotest.client.shared import error
from virttest import utils_v2v
from virttest import libvirt_storage
from virttest import libvirt_vm
from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Convert a remote vm to local libvirt(KVM).
    """
    vm_name = params.get("main_vm")
    # Remote host parameters
    username = params.get("username", "root")
    xen_ip = params.get("xen_ip", "XEN.EXAMPLE")
    xen_pwd = params.get("xen_pwd", "PWD.EXAMPLE")
    vpx_ip = params.get("vpx_ip", "ESX.EXAMPLE")
    vpx_pwd = params.get("vpx_pwd", "PWD.EXAMPLE")
    vpx_pwd_file = params.get("vpx_passwd_file")
    vpx_dc = params.get("vpx_dc", "VPX.DC.EXAMPLE")
    esx_ip = params.get("esx_ip", "ESX.EXAMPLE")
    for param in [vm_name, xen_ip, xen_pwd, vpx_ip, vpx_pwd, vpx_dc, esx_ip]:
        if "EXAMPLE" in param:
            raise error.TestNAError("Please replace %s with real value" % param)

    hypervisor = params.get("hypervisor")
    pool_type = params.get("pool_type", "dir")
    pool_name = params.get("pool_name", "v2v_test")
    target_path = params.get("target_path", "pool_path")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    emulated_size = params.get("emulated_image_size", "10G")

    # If target_path is not an abs path, join it to data_dir.tmpdir
    if not os.path.dirname(target_path):
        target_path = os.path.join(data_dir.get_tmp_dir(), target_path)

    # V2V parameters
    input_mode = params.get("input_mode")
    network = params.get("network")
    bridge = params.get("bridge")

    # Set libguestfs environment
    os.environ['LIBGUESTFS_BACKEND'] = 'direct'

    # Extra v2v command options, default to None
    v2v_opts = params.get("v2v_opts")

    if hypervisor == "esx":
        # Create password file to access ESX hypervisor
        remote_ip = vpx_ip
        remote_pwd = vpx_pwd
        fp = open(vpx_pwd_file, 'w')
        fp.write(vpx_pwd)
        fp.close()

    if hypervisor == "xen":
        # Set up ssh access using ssh-agent and authorized_keys
        remote_ip = xen_ip
        remote_pwd = xen_pwd
        ssh_key.setup_ssh_key(remote_ip, user=username, port=22,
                              password=remote_pwd)
        try:
            utils_misc.add_identities_into_ssh_agent()
        except:
            utils.run("ssh-agent -k")
            raise error.TestError("Failed to start 'ssh-agent'")

    # Create remote uri for remote host
    ruri = utils_v2v.Uri(hypervisor)
    remote_uri = ruri.get_uri(remote_ip, vpx_dc, esx_ip)
    logging.debug("The current virsh uri: %s", remote_uri)

    # Check remote vms
    rvirsh_dargs = {'uri': remote_uri, 'remote_ip': remote_ip,
                    'remote_user': username, 'remote_pwd': remote_pwd}
    rvirsh = virsh.VirshPersistent(**rvirsh_dargs)
    if not rvirsh.domain_exists(vm_name):
        rvirsh.close_session()
        raise error.TestError("Couldn't find vm '%s' to convert on "
                              "remote uri '%s'." % (vm_name, remote_uri))
    remote_vm = libvirt_vm.VM(vm_name, params, test.bindir,
                              env.get("address_cache"))
    remote_vm.connect_uri = remote_uri

    # Prepare local libvirt storage pool
    rsp = libvirt_storage.StoragePool(rvirsh)
    pvt = utlv.PoolVolumeTest(test, params)
    lsp = libvirt_storage.StoragePool()

    # Maintain a single params for v2v to avoid duplicate parameters
    v2v_params = {"hostname": remote_ip, "username": username,
                  "password": remote_pwd, "hypervisor": hypervisor,
                  "storage": pool_name, "network": network,
                  "bridge": bridge, "target": "libvirt",
                  "main_vm": vm_name, "input_mode": input_mode}
    if vpx_dc:
        v2v_params.update({"vpx_dc": vpx_dc})
    if esx_ip:
        v2v_params.update({"esx_ip": esx_ip})
    if v2v_opts:
        v2v_params.update({"v2v_opts": v2v_opts})

    try:
        # Create storage pool for test
        pvt.pre_pool(pool_name, pool_type, target_path, emulated_img,
                     image_size=emulated_size)
        logging.debug(lsp.pool_info(pool_name))
        ret = utils_v2v.v2v_cmd(v2v_params)
        logging.debug("virt-v2v verbose messages:\n%s", ret)
        if ret.exit_status != 0:
            if "Input/output error" in ret.stderr:
                raise error.TestFail("Encounter BZ#1146007")
            raise error.TestFail("Convert VM failed")

        # Update parameters for local hypervisor and vm, then start vm
        logging.debug("XML info:\n%s", virsh.dumpxml(vm_name))
        params['main_vm'] = vm_name
        params['target'] = "libvirt"
        vm = env.create_vm("libvirt", "libvirt", vm_name, params, test.bindir)
        vm.start()
    finally:
        if hypervisor == "esx":
            utils.run("rm -rf %s" % vpx_pwd_file)
        else:
            if hypervisor == "xen":
                utils.run("ssh-agent -k")
            rsp.delete_pool(pool_name)
        if rvirsh:
            rvirsh.close_session()
