import logging

from six import iteritems

from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from virttest.libvirt_xml.devices import interface
from virttest import migration_template as mt


class PingError(mt.Error):
    """
    Raise when ping failed

    :param status: exit status of ping
    :param output: output of ping
    """

    def __init__(self, status, output):
        self.status = status
        self.output = output

        def __str__(self):
            super_str = super(PingError, self).__str__()
            return (super_str +
                    ('Ping failed, status: %s, output: %s\n' %
                     (self.status, self.output)))


class OvsBridgeCreateError(mt.Error):
    """
    Raise when failed to create ovs bridge

    :param status: exit status of create command
    :param output: output of creat command
    """

    def __init__(self, status, output):
        self.status = status
        self.output = output

        def __str__(self):
            super_str = super(OvsBridgeCreateError, self).__str__()
            return (super_str +
                    ('Failed to create ovs bridge, status: %s, output: %s\n' %
                     (self.status, self.output)))


class MigrationNetworkBase(mt.MigrationTemplate):
    """
    Base class for migration with virtual network interface

    :param iface_dict: configurations of virtual interface to be set in vm xml
    :param network_dict: configurations of virtual network to be created
    """

    def __init__(self, test, env, params, *args, **dargs):
        for k, v in iteritems(dict(*args, **dargs)):
            params[k] = v
        super(MigrationNetworkBase, self).__init__(
            test, env, params, *args, **dargs)

        self.network_dict = eval(params.get("network_dict", '{}'))
        self.iface_dict = eval(params.get("iface_dict", '{}'))

    def create_virtual_network(self):
        """
        Create virtual network on both local and remote host
        """

        logging.debug("Create virtual network on local host")
        self.create_virtual_network_on_spechost()

        logging.debug("Create virtual network on remote host")
        self.create_virtual_network_on_spechost(remote_args=self.remote_dict)

    def create_virtual_network_on_spechost(self, **remote_args):
        """
        Create virtual network on specified host

        :param remote_args: remote_args of host to create virtual network on
        """

        logging.debug("Create virtual network")
        libvirt_network.create_or_del_network(
            self.network_dict, remote_args=remote_args)

    def set_interface_in_vm_xml(self):
        """
        Set interface in vm xml
        """

        mac = self.generate_vm_mac_address()
        self.iface_dict.update({'mac': mac})

        self.update_iface_xml(self.main_vm.name, self.iface_dict)

    def generate_vm_mac_address(self):
        """
        Generate vm mac address to be set in vm xml

        :return mac: the generated mac address
        """

        if "mac" not in self.iface_dict:
            mac = utils_net.generate_mac_address_simple()
        else:
            mac = self.iface_dict["mac"]

        return mac

    def get_vm_mac_address(self):
        """
        Get vm mac address from iface_dict
        """

        return self.iface_dict["mac"]

    @staticmethod
    @mt.vm_session_handler
    def get_vm_ip_address(vm, mac):
        """
        Get vm ip address per mac address

        :param vm: vm object
        :param mac: vm mac address

        :return vm ip address
        """

        return utils_net.get_guest_ip_address(vm.session, mac)

    @staticmethod
    @mt.vm_session_handler
    def restart_vm_network(vm):
        """
        Restart guest network

        :param vm: vm object
        """

        utils_net.restart_guest_network(vm.session)

    def check_vm_network_accessed(self, ping_dest, session=None):
        """
        Confirm local/remote VM can be accessed through network.

        :param ping_dest: The destination to be ping
        :param session: The session object to the host
        :raise: PingError when ping fails
        """

        logging.info("Check VM network connectivity")
        status, output = utils_net.ping(ping_dest,
                                        count=10,
                                        timeout=20,
                                        output_func=logging.debug,
                                        session=session)
        if status != 0:
            raise PingError(status, output)

    def update_iface_xml(self, vm_name, iface_dict):
        """
        Update interfaces for guest

        :param vm_name: The name of VM
        :param iface_dict: The interface configurations params
        """
        logging.debug("update iface xml")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

        iface = interface.Interface('network')
        iface.xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict)
        libvirt.add_vm_device(vmxml, iface)


class migration_with_ovs_bridge(MigrationNetworkBase):
    """
    Do migration with ovs bridge

    :param ovs_bridge_name: ovs bridge name to be created
    """

    def __init__(self, test, env, params, *args, **dargs):
        super(migration_with_ovs_bridge, self).__init__(
            test, env, params, *args, **dargs)

        self.ovs_bridge_name = params.get("ovs_bridge_name")

    def _pre_start_vm(self):
        if self.ovs_bridge_name:
            self.create_ovs_bridge()
        if self.network_dict:
            self.create_virtual_network()
        if self.iface_dict:
            self.set_interface_in_vm_xml()

    def _post_start_vm(self):
        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(self.main_vm.name))

        self.vm_ip = self.get_vm_ip_address(
            self.main_vm, self.get_vm_mac_address)

        self.restart_vm_network(self.main_vm)
        self.check_vm_network_accessed(self.vm_ip)

    def _post_migrate(self):
        self.check_vm_network_accessed(self.vm_ip, session=self.remote_session)

    def _post_migrate_back(self):
        self.check_vm_network_accessed(self.vm_ip)

    def cleanup(self):
        logging.debug("Start to clean up env in migration_with_ovs_bridge")

        logging.debug("Destroy vms")
        for vm in self.vms:
            vm.destroy()

        if self.network_dict:
            logging.debug("Delete virtual networks on remote host")
            libvirt_network.create_or_del_network(
                self.network_dict, is_del=True, remote_args=self.remote_dict)

            logging.debug("Delete virtual networks on local host")
            libvirt_network.create_or_del_network(
                self.network_dict, is_del=True)

        if self.ovs_bridge_name:
            logging.debug("Delete ovs bridge on local host")
            utils_net.delete_ovs_bridge(self.ovs_bridge_name)

            logging.debug("Delete ovs bridge on remote host")
            utils_net.delete_ovs_bridge(self.ovs_bridge_name,
                                        session=self.remote_session)

        super(migration_with_ovs_bridge, self).cleanup()

    def create_ovs_bridge(self):
        """
        Create ovs bridge on both local and remote host
        """

        logging.debug("Create ovs bridge on local host")
        self.create_ovs_bridge_on_spechost()

        logging.debug("Create ovs bridge on remote host")
        self.create_ovs_bridge_on_spechost(session=self.remote_session)

    def create_ovs_bridge_on_spechost(self, session=None):
        """
        Create ovs bridge on specified host

        :param session: session of host to create ovs bridge on
        """

        logging.debug("Create ovs bridge")
        status, stdout = utils_net.create_ovs_bridge(
            self.ovs_bridge_name, session=session)
        if status:
            raise OvsBridgeCreateError(status, stdout)


def run(test, params, env):
    # Get device type
    device_type = params.get('device_type')
    if not device_type:
        test.error("device_type is not configured")

    # Get migration object by device type
    migration_class_dict = {
            'ovs-bridge': migration_with_ovs_bridge,
            }
    obj_migration = migration_class_dict[device_type](test, env, params)

    try:
        obj_migration.runtest()
    finally:
        obj_migration.cleanup()
