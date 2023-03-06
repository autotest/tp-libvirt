import logging as log

from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices import interface


logging = log.getLogger('avocado.' + __name__)


def customize_config(conf_dict, config_type):
    """
    Updates the daemon configuration and returns
    object for later recovery.

    :param conf_dict: key value pairs for the configuration file
    :param config_type: configuration file name without extension, e.g. virtqemud
    """
    return libvirt.customize_libvirt_config(
            conf_dict,
            config_type=config_type,
            remote_host=False,
            extra_params=None)


def recover_config(daemon_conf):
    """
    Recovers the original daemon configuration

    :param daemon_conf: configuration object that was created when the
                        configuration file was updated
    """
    libvirt.customize_libvirt_config(
            None,
            remote_host=False,
            is_recover=True,
            config_object=daemon_conf)


def add_filter_to_first_interface(vmxml):
    """
    Adds 'clean-traffic' as filter to the first
    interface. This filter is pre-defined by libvirt.

    :param vmxml: VMXML instance of the VM
    """
    iface_xml = vmxml.get_devices('interface')[0]
    vmxml.del_device(iface_xml)
    new_iface = interface.Interface('network')
    new_iface.xml = iface_xml.xml
    new_filterref = new_iface.new_filterref(**{
            "name": "clean-traffic"
        })
    new_iface.filterref = new_filterref
    logging.debug("new interface xml is: %s" % new_iface)
    vmxml.add_device(new_iface)
    vmxml.sync()


def run(test, params, env):
    """
    Test VM state when daemon restarts

    1) Configure access driver
    2) Start domain
    3) Restart daemon
    4) Check VM is still running
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    daemon_names = ["virtqemud", "virtnwfilterd", "virtnetworkd"]
    conf_dict = {'access_driver': '["polkit"]',
                 'unix_sock_rw_perms': '"0777"',
                 'auth_unix_rw': '"none"'}
    daemon_configs = None

    try:
        add_filter_to_first_interface(vmxml)

        libvirtd = utils_libvirtd.Libvirtd(all_daemons=True)
        libvirtd.stop()
        daemon_configs = [customize_config(conf_dict, x) for x in daemon_names]
        libvirtd.start()

        vm.start()
        libvirtd.restart()
        if not vm.is_alive():
            test.fail("VM is not running after daemon restart.")

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        if daemon_configs:
            [recover_config(x) for x in daemon_configs]
