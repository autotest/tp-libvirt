import logging
import os

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check connectivity for bridge type interface with QEMU network helper
    for unprivileged users
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    outside_ip = params.get('outside_ip')
    rand_id = utils_misc.generate_random_string(3)
    linux_bridge = 'br_' + rand_id
    br_conf_file = '/etc/qemu-kvm/bridge.conf'
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]

    unpr_user = params.get('unpr_user', 'test_unpr') + rand_id

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    LOG.debug("Remove 'dac' security driver for unprivileged user")
    vmxml.del_seclabel(by_attr=[('model', 'dac')])

    try:
        utils_net.create_linux_bridge_tmux(linux_bridge, host_iface)
        process.run(f'ip l show type bridge {linux_bridge}', shell=True)
        process.run('chmod u+s /usr/libexec/qemu-bridge-helper', shell=True)

        with open(br_conf_file, 'a') as fh:
            fh.write(f'allow {linux_bridge}\n')

        with open(br_conf_file) as fh:
            LOG.debug(f'Content of {br_conf_file}:\n'
                      f'{fh.read()}')

        br_conf_perm = oct(os.stat(br_conf_file).st_mode)[-3:]
        if br_conf_perm != '644':
            test.error(f'Permission of {br_conf_file} is {br_conf_perm}, '
                       f'but it should be 644')

        # Create unprivileged user
        LOG.info(f'Create unprivileged user {unpr_user}')
        process.run(f'useradd {unpr_user}', shell=True)

        unpr_vmxml = network_base.prepare_vmxml_for_unprivileged_user(unpr_user,
                                                                      vmxml)
        unpr_vm_name = unpr_vmxml.vm_name

        unpr_vmxml.del_device('interface', by_tag=True)
        iface_attrs = {'source': {'bridge': linux_bridge},
                       'model': 'virtio', 'type_name': 'bridge'}
        libvirt_vmxml.modify_vm_device(unpr_vmxml, 'interface', iface_attrs)

        network_base.define_vm_for_unprivileged_user(unpr_user, unpr_vmxml)

        # Switch to unprivileged user and modify vm's interface
        # Start vm as unprivileged user and test network
        virsh.start(unpr_vm_name, debug=True, ignore_status=False,
                    unprivileged_user=unpr_user)
        session = network_base.unprivileged_user_login(unpr_vm_name, unpr_user,
                                                       params.get('username'),
                                                       params.get('password'))

        LOG.debug(session.cmd_output('ifconfig'))
        host_ip = utils_net.get_host_ip_address()

        ips = {'outside_ip': outside_ip, 'host_public_ip': host_ip}
        network_base.ping_check(params, ips, session)

        session.close()

    finally:
        virsh.destroy(unpr_vm_name, unprivileged_user=unpr_user, debug=True)
        virsh.undefine(unpr_vm_name, options='--nvram',
                       unprivileged_user=unpr_user, debug=True)
        utils_net.delete_linux_bridge_tmux(linux_bridge, host_iface)
        process.run(f'pkill -u {unpr_user};userdel -f -r {unpr_user}',
                    shell=True, ignore_status=True)
