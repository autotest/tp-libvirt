import logging

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

DEFAULT_NET = 'default'
VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def create_iface(iface_type, target_dev, **iface_args):
    """
    Create iface for test
    """
    source_map = {'network': {'network': DEFAULT_NET},
                  'direct': {'dev': iface_args['host_iface_name'],
                             'mode': 'bridge'}}
    iface_attrs = {'model': 'virtio',
                   'source': source_map[iface_type],
                   'target': {'dev': target_dev},
                   'type_name': iface_type}
    return libvirt_vmxml.create_vm_device_by_type('interface', iface_attrs)


def add_iface(iface_type, target_dev, **kwargs):
    """
    Add iface to vmxml
    """
    if iface_type == 'tap':
        create_cmd = f'ip tuntap add {target_dev} mode tap'
        process.run(create_cmd, shell=True)
        check_cmd = 'ip tuntap'
        libvirt.check_cmd_output(check_cmd, f'{target_dev}: tap persist')
    elif iface_type == 'macvtap':
        if 'host_iface_name' not in kwargs:
            host_iface_name = utils_net.get_net_if(state="UP")[0]
        else:
            host_iface_name = kwargs['host_iface_name']
        create_cmd = f'ip l add l {host_iface_name} name {target_dev} ' \
                     f'type macvtap mode bridge'
        process.run(create_cmd, shell=True)
        check_cmd = 'ip l'
        libvirt.check_cmd_output(check_cmd,
                                 f'{target_dev}@{host_iface_name}')
    elif iface_type in ['network', 'direct']:
        new_iface = create_iface(iface_type, target_dev, **kwargs)
        vmxml = kwargs.get('vmxml')
        vmxml.add_device(new_iface, allow_dup=True)
        vmxml.sync()


def show_ip_link(link_name):
    """
    Print ip link info
    """
    cmd = 'ip l show ' + link_name
    link_info = process.run(cmd, shell=True, ignore_status=True).stdout_text

    return link_info.strip()


def cleanup_iface(iface_type, iface_name):
    """
    Clean up tap/macvtap device
    """
    if iface_type in ['tap', 'macvtap']:
        process.run(f'ip l delete {iface_name}', shell=True, ignore_status=True)


def run(test, params, env):
    """
    Test start vm with duplicate target dev name - negative
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    iface_type_a = params.get('iface_type_a')
    iface_type_b = params.get('iface_type_b')
    iface_name = 'test_iface_' + utils_misc.generate_random_string(3)
    host_iface_name = params.get("host_iface_name")
    if not host_iface_name:
        host_iface_name = utils_net.get_net_if(state="UP")[0]

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test_args = {'host_iface_name': host_iface_name,
                     'vmxml': vmxml}

        vmxml.del_device('interface', by_tag=True)
        add_iface(iface_type_a, iface_name, **test_args)
        add_iface(iface_type_b, iface_name, **test_args)

        LOG.debug(f'VMXML after adding interfaces with duplicate target dev:'
                  f'\n{vmxml}')

        link_before_start = show_ip_link(iface_name)
        if iface_type_a in ['network', 'direct']:
            if link_before_start != '':
                test.error('There should not be any ip link on host.')

        vm_start_test = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(vm_start_test, status_error)
        libvirt.check_result(vm_start_test, error_msg)

        link_after_start = show_ip_link(iface_name)
        if link_after_start != link_before_start:
            test.fail(f'IP link info changed after vm started.\n'
                      f'Before vm started :{link_before_start}\n'
                      f'After vm started: {link_after_start}')

    finally:
        bkxml.sync()
        cleanup_iface(iface_type_a, iface_name)
