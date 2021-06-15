import ast
import aexpect
import logging

from avocado.utils import process

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices import rng
from virttest.utils_test import libvirt
from virttest import utils_misc
from virttest import virsh, libvirt_version
from virttest import migration_template as mt


class DeviceNotFoundError(mt.Error):
    """
    Raise when device is not found in vm
    """

    def __init__(self, device_type):
        self.device_type = device_type

    def __str__(self):
        super_str = super(DeviceNotFoundError, self).__str__()
        return (super_str +
                ('Device %s is not found in vm\n' % self.device_type))


class DeviceNotRemovedError(mt.Error):
    """
    Raise when device is not removed in vm(e.g. after detach)
    """

    def __init__(self, device_type):
        self.device_type = device_type

    def __str__(self):
        super_str = super(DeviceNotRemovedError, self).__str__()
        return (super_str +
                ('Device %s is not removed from vm\n' % self.device_type))


class RngReadTimeoutError(mt.Error):
    """
    Raise when read from rng device times out
    """

    def __init__(self, cmd):
        self.cmd = cmd

    def __str__(self):
        super_str = super(RngReadTimeoutError, self).__str__()
        return (super_str +
                ('Rng read cmd "%s" times out\n' % self.cmd))


class RngReadUnknownError(mt.Error):
    """
    Raise when unknown error happened when read from rng device
    """

    def __init__(self, cmd, output):
        self.cmd = cmd
        self.output = output

    def __str__(self):
        super_str = super(RngReadUnknownError, self).__str__()
        return (super_str +
                ('Rng read failed, cmd is: %s \n, output is %s\n'
                 % (self.cmd, self.output)))


class DeviceXMLNotFoundError(mt.Error):
    """
    Raise when device is not found in domain dumpxml
    """

    def __str__(self):
        super_str = super(DeviceXMLNotFoundError, self).__str__()
        return (super_str +
                "Device xml is not found in domain xml")


class DeviceXMLNotRemovedError(mt.Error):
    """
    Raise when device is not removed in domain dumpxml(e.g. after detach)
    """

    def __str__(self):
        super_str = super(DeviceXMLNotRemovedError, self).__str__()
        return (super_str +
                "Device xml is not removed from domain xml")


class DeviceXMLMismatchError(mt.Error):
    """
    Raise when device attributes in domain dumpxml is not same as set values
    """

    def __str__(self):
        super_str = super(DeviceXMLMismatchError, self).__str__()
        return (super_str +
                "Device domain xml is not same as set")


class MigrationVirtualDevicesBase(mt.MigrationTemplate):
    """
    Base class for migration with virtual devices
    """

    def hotplug_device(self, vm, device_xml, *check_funcs):
        """
        Hotplug device to vm, check device

        :param vm: vm object
        :param device_xml: device xml to be hotplugged
        :param check_funcs: funcs to check the hotplugged device in dumpxml
        """
        logging.debug("Hotplug device: %s to vm: %s", device_xml, vm.name)
        virsh_instance = virsh.Virsh(uri=vm.connect_uri)
        ret = virsh_instance.attach_device(vm.name, device_xml.xml,
                                           flagstr="--live",
                                           wait_remove_event=True,
                                           debug=True, ignore_status=True)
        libvirt.check_exit_status(ret)

        # Check device after hotplug
        for func in check_funcs:
            func()

    def hotunplug_device(self, vm, device_xml, *check_funcs):
        """
        Hotunplug device from vm

        :param vm: vm object
        :param device_xml: device xml to be hotunplugged
        :param check_funcs: funcs to check the hotunplugged device in dumpxml
        """
        logging.debug("Hotunplug device xml: %s from vm: %s",
                      device_xml, vm.name)
        virsh_instance = virsh.Virsh(uri=vm.connect_uri)
        ret = virsh_instance.detach_device(vm.name, device_xml.xml,
                                           flagstr="--live",
                                           debug=True, ignore_status=True)
        libvirt.check_exit_status(ret)

        # Check device after hotunplug
        for func in check_funcs:
            func()


class MigrationWithRng(MigrationVirtualDevicesBase):
    """
    Do migration with rng device

    :param hotplug: hotplug device or not
    :param rng_source_mode: vm rng device source mode
    :param backend_type: vm rng device backend type
    :param rng_model: vm rng device model
    :param backend_model: vm rng device backend model
    :param backend_dev: vm rng device backend dev
    :param backend_source: vm rng device backend source
    :param backend_protocol: vm rng device backend protocol
    """

    def __init__(self, test, env, params, *args, **dargs):
        super(MigrationWithRng, self).__init__(test, env, params, *args,
                                               **dargs)

        self.hotplug = params.get("hotplug", "no")
        self.rng_source_mode = params.get("rng_source_mode")
        self.backend_type = params.get("backend_type")
        self.rng_model = params.get("rng_model", "virtio")
        self.backend_model = params.get("backend_model", "random")
        self.backend_dev = params.get("backend_dev", "")
        self.backend_source = params.get("backend_source", "")
        self.backend_protocol = params.get("backend_protocol")

        # Builtin backend model is only supported by libvirt>=6.2.0
        # and qemu-kvm>=4.2.0
        if (self.backend_model == "builtin" and
                (not libvirt_version.version_compare(6, 2, 0)
                    or not utils_misc.compare_qemu_version(4, 2, 0, False))):
            self.test.cancel("backend model builtin is not supported"
                             "by this hypervisor version")

        # Create the rng_xml to be set
        self.rng_xml = self._create_rng_xml()

        # Variable: background jobs to be killed in cleanup()
        self.bgjobs = []

    def _pre_start_vm(self):
        # Need to create rng server before vm starts if
        # vm rng mode is 'client'
        if self._vm_rng_mode() == 'client':
            self._create_tcp_rng_source(self.migrate_source_host,
                                        mode='server')

        # Remove rng devices from vm xml defination
        self._remove_rng_from_vmxml(self.main_vm)

    def _post_start_vm(self):
        # Hotplug device
        if self.hotplug == "yes":
            logging.debug("Hotplug rng device after vm starts")
            self.hotplug_device(self.main_vm, self.rng_xml)

            # Check rng in dumpxml
            self.wait_for_check_rng_in_dumpxml(self.main_vm, self.rng_xml,
                                               True)

            # Need to create rng client if vm rng mode is 'server'
            if self._vm_rng_mode() == 'server':
                self._create_tcp_rng_source(self.migrate_source_host,
                                            mode='client')

            # Check rng in vm
            self.check_rng_in_vm(self.main_vm, self.backend_type,
                                 True)

    def _post_migrate(self):
        # Check rng in dumpxml
        self.wait_for_check_rng_in_dumpxml(self.main_vm, self.rng_xml, True)

        # Need to create rng client if vm rng mode is 'server'
        if self._vm_rng_mode() == 'server':
            self._create_tcp_rng_source(self.migrate_dest_host,
                                        mode='client')

        # Check rng in vm
        self.check_rng_in_vm(self.main_vm, self.backend_type, True)

        if self.migrate_vm_back == 'no':
            # Hotunplug rng here if migrate_vm_back is no
            logging.debug("Hotunplug rng device after migration")
            self.hotunplug_device(self.main_vm, self.rng_xml)

            # Check rng in dumpxml
            self.wait_for_check_rng_in_dumpxml(self.main_vm, self.rng_xml,
                                               False)

            # Check rng in vm
            self.check_rng_in_vm(self.main_vm, self.backend_type,
                                 False)

    def _post_migrate_back(self):
        # Check rng in dumpxml
        self.wait_for_check_rng_in_dumpxml(self.main_vm, self.rng_xml, True)

        # Need to create rng client if vm rng mode is 'server'
        if self._vm_rng_mode() == 'server':
            self._create_tcp_rng_source(self.migrate_source_host,
                                        mode='client')

        # Check rng in vm after migration back
        self.check_rng_in_vm(self.main_vm, self.backend_type, True)

        # Hotunplug rng device
        logging.debug("Hotunplug rng device after migration back")
        self.hotunplug_device(self.main_vm, self.rng_xml)

        # Check rng in dumpxml
        self.wait_for_check_rng_in_dumpxml(self.main_vm, self.rng_xml, False)

        # Check rng in vm
        self.check_rng_in_vm(self.main_vm, self.backend_type, False)

    def cleanup(self):
        """
        Clean up env
        """
        super(MigrationWithRng, self).cleanup()

        # Kill background jobs
        logging.debug("Kill background jobs")
        for job in self.bgjobs:
            job.kill_func()

    def _create_rng_xml(self):
        """
        Create rng xml
        """

        rng_xml = rng.Rng()
        rng_xml.rng_model = self.rng_model
        backend = rng.Rng.Backend()
        backend.backend_model = self.backend_model
        backend_source_list = self.backend_source.split()
        if self.backend_type:
            backend.backend_type = self.backend_type
        if self.backend_dev:
            backend.backend_dev = self.backend_dev
        if backend_source_list:
            source_list = [ast.literal_eval(source) for source in
                           backend_source_list]
            if self.backend_type == 'tcp':
                for source in source_list:
                    if not source.get('host') and source['mode'] == 'connect':
                        source.update(host=self.local_ip)
                    if not source.get('host') and source['mode'] == 'bind':
                        source.update(host='0.0.0.0')
            backend.source = source_list
        if self.backend_protocol:
            backend.backend_protocol = self.backend_protocol
        rng_xml.backend = backend

        return rng_xml

    def _create_tcp_rng_source(self, server, mode=None):
        """
        Create an external source for tcp backend rng device.
        Also open port of rng server in firewalld

        :param server: ip of the rng tcp server
        :param mode: the mode of the external rng source: 'client' or 'server'
                     Note:
                     Set it to 'client' if vm rng backend_type is 'bind';
                     Set it to 'server' if vm rng backend_type is 'connect'
        """
        logging.debug("Create rng source for tcp backend")
        # Enable tcp server listen port in firewall
        if server == self.local_ip:
            logging.debug("Open port of rng tcp server on local host")
            self.open_port_in_iptables('1024')
            self.opened_ports_local.append('1024')
        else:
            logging.debug("Open port of rng tcp server on remote host")
            self.open_port_in_iptables('1024',
                                       server_dict=self.remote_dict,
                                       session=self.remote_session)
            self.opened_ports_remote.append('1024')

        # Create rng source
        if mode == 'server':
            cmd = "cat /dev/urandom | nc -k -l %s 1024" % server
        elif mode == 'client':
            cmd = "cat /dev/urandom | nc %s 1024" % server
        else:
            self.test.error("Uknown rng source mode")
            return
        rngjob = utils_misc.AsyncJob(cmd, kill_func=self.func_kill_rng_source)
        self.bgjobs.append(rngjob)

    @staticmethod
    def func_kill_rng_source():
        cmd = 'pkill -9 nc'
        process.run(cmd, ignore_status=True)

    def _remove_rng_from_vmxml(self, vm):
        """
        Remove rng device from domain xml defination

        :param vm: vm object
        """
        logging.debug("Remove rng device from domain xml defination")

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        vmxml.remove_all_device_by_type("rng")
        vmxml.sync()

    def wait_for_check_rng_in_dumpxml(self, vm, xml_set, rng_present,
                                      timeout=120):
        """
        Wait for checking rng xml in domain dumpxml

        :param vm: vm object
        :param xml_set: rng xml object
        :param rng_present: expected rng presence in domain xml
        :param timeout: time(seconds) to keep trying

        :raise DeviceXMLNotFoundError if rng not found in domain xml
               and rng_present=True
        :raise DeviceXMLNotRemovedError if rng found in domain xml
               and rng_present=False
        :raise DeviceXMLMismatchError if rng attributes in domain dumpxml
               are not same as set values
        """
        logging.debug("Check rng in domain dumpxml")
        # Check rng presence in dumpxml
        if not self.wait_for_check_rng_presence_in_dumpxml(vm, rng_present,
                                                           timeout=timeout):
            if rng_present:
                raise DeviceXMLNotFoundError()
            else:
                raise DeviceXMLNotRemovedError()

        # Compare rng attributes in dumpxml with set values
        if rng_present and not self.check_rng_values_in_dumpxml(vm, xml_set):
            raise DeviceXMLMismatchError()

    def wait_for_check_rng_presence_in_dumpxml(self, vm, rng_present,
                                               timeout=120):
        """
        Wait for checking rng presence in domain xml until timeout

        :param vm: vm object
        :param rng_present: expected rng presence in domain xml
        :param timeout: time(seconds) to keep trying

        :return boolean: True if rng presence is expected else False
        """
        logging.debug("Wait for checking rng presence in dumpxml,\
                       expected: %s, timeout: %ss",
                      rng_present, timeout)
        func = self.check_rng_presence_in_dumpxml
        return utils_misc.wait_for(lambda: func(vm) == rng_present, timeout)

    def check_rng_presence_in_dumpxml(self, vm):
        """
        Check rng presence in domain xml

        :param vm: vm object

        :return: boolean: True if rng is present in dumpxml else False
        """
        logging.debug("Check rng presence in domain xml")

        virsh_instance = virsh.Virsh(uri=vm.connect_uri)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                              virsh_instance=virsh_instance)
        # Get all current xml rng devices
        xml_devices = vmxml.devices
        rng_devices = xml_devices.by_device_tag("rng")
        logging.debug("rng_devices is %s", rng_devices)

        return True if rng_devices else False

    def check_rng_values_in_dumpxml(self, vm, xml_set):
        """
        Check the set rng xml with dumpxml by comparing the attributes

        :param vm: vm object
        :param xml_set: rng xml object

        :return: boolean: True if check succeeds else False
        """
        logging.debug("Check rng values in dumpxml")

        virsh_instance = virsh.Virsh(uri=vm.connect_uri)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                              virsh_instance=virsh_instance)
        # Get all current xml rng devices
        xml_devices = vmxml.devices
        rng_devices = xml_devices.by_device_tag("rng")
        logging.debug("rng_devices is %s", rng_devices)

        rng_index = xml_devices.index(rng_devices[-1])
        xml_get = xml_devices[rng_index]

        def get_compare_values(xml_set, xml_get, rng_attr):
            """
            Get set and get value to compare

            :param xml_set: setting xml object
            :param xml_get: getting xml object
            :param rng_attr: attribute of rng device
            :return: set and get value in xml
            """
            try:
                set_value = xml_set[rng_attr]
            except xcepts.LibvirtXMLNotFoundError:
                set_value = None
            try:
                get_value = xml_get[rng_attr]
            except xcepts.LibvirtXMLNotFoundError:
                get_value = None
            logging.debug("get xml_set value(%s) is %s, get xml_get value is\
                          %s", rng_attr, set_value, get_value)
            return (set_value, get_value)

        match = True
        for rng_attr in xml_set.__slots__:
            set_value, get_value = get_compare_values(xml_set, xml_get,
                                                      rng_attr)
            logging.debug("rng_attr=%s, set_value=%s, get_value=%s",
                          rng_attr, set_value, get_value)
            if set_value and set_value != get_value:
                if rng_attr == 'backend':
                    for bak_attr in xml_set.backend.__slots__:
                        set_backend, get_backend = get_compare_values(
                                xml_set.backend, xml_get.backend, bak_attr)
                        if set_backend and set_backend != get_backend:
                            if bak_attr == 'source':
                                set_source = xml_set.backend.source
                                get_source = xml_get.backend.source
                                find = False
                                for i in range(len(set_source)):
                                    for j in get_source:
                                        set_item = set_source[i].items()
                                        get_item = j.items()
                                        if set(set_item).issubset(get_item):
                                            find = True
                                            break
                                    if not find:
                                        logging.debug("set source(%s) not in\
                                                      get source(%s)",
                                                      set_source[i],
                                                      get_source)
                                        match = False
                                        break
                                    else:
                                        continue
                            else:
                                logging.debug("set backend(%s)- %s not equal\
                                               to get backend-%s",
                                              rng_attr, set_backend,
                                              get_backend)
                                match = False
                                break
                        else:
                            continue
                        if not match:
                            break
                else:
                    logging.debug("set value(%s)-%s not equal to get value-%s",
                                  rng_attr, set_value, get_value)
                    match = False
                    break
            else:
                continue
            if not match:
                break

        return match

    @staticmethod
    @mt.vm_session_handler
    def check_rng_in_vm(vm, backend_type, rng_present):
        """
        Check rng device in vm

        :param vm: vm object
        :param backend_type: rng device backend type: tcp, udp, rdm, builtin

        :raise: DeviceNotFoundError if no rng device in vm and rng_present=True
        :raise: DeviceNotRemovedError if rng device is found in vm and
                rng_present=False
        :raise: RngReadTimeoutError if read from rng device times out
        :raise: RngReadUnknownError if unknown error happened when read rng
                device
        """

        logging.debug("Check rng device in vm")
        check_cmd = "dd if=/dev/hwrng of=/dev/null count=2 bs=2"
        timeout = 10

        try:
            status, output = vm.session.cmd_status_output(check_cmd, timeout)
            logging.debug("cmd exit status: %s, cmd output: %s",
                          status, output)

            if status == 0:
                if rng_present:
                    return
                else:
                    raise DeviceNotRemovedError("rng")
            else:
                if not rng_present and "No such device" in output:
                    return
                elif rng_present:
                    raise DeviceNotFoundError("rng")
                else:
                    raise RngReadUnknownError(check_cmd, output)
        except aexpect.exceptions.ShellTimeoutError:
            vm.session.sendcontrol('c')
            if not rng_present:
                raise DeviceNotRemovedError("rng")
            elif backend_type == 'udp':
                logging.debug("UDP rng device check cmd times out as expected")
                return
            else:
                raise RngReadTimeoutError(check_cmd)

    def _vm_rng_mode(self):
        """
        Get vm rng device source mode

        :return string: 'server' if vm rng_source_mode is 'bind'
                        'client' if vm rng_source_mode is 'connect'
                        None if vm rng_source_mode is other value
        """
        mode = None
        if self.rng_source_mode == 'bind':
            mode = 'server'
        elif self.rng_source_mode == 'connect':
            mode = 'client'
        else:
            pass

        return mode


def run(test, params, env):
    # Get device type
    device_type = params.get('device_type')
    if not device_type:
        test.error("device_type is not configured")

    # Get migration object by device type
    migration_class_dict = {'rng': MigrationWithRng}
    obj_migration = migration_class_dict[device_type](test, env, params)

    try:
        obj_migration.runtest()
    finally:
        obj_migration.cleanup()
