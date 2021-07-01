"""
Module to exercize virsh attach-device command with various devices/options
"""

import os
import os.path
import logging
import aexpect
import itertools
import platform
from string import ascii_lowercase

from six import iteritems

from avocado.utils import astring

from virttest import libvirt_version
from virttest import virt_vm, virsh, remote, utils_misc, data_dir
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_pcicontr


# TODO: Move all these helper classes someplace else


class TestParams(object):

    """
    Organize test parameters and decouple from params names
    """

    def __init__(self, params, env, test, test_prefix='vadu_dev_obj_'):
        self.test_prefix = test_prefix
        self.test = test
        self.vmxml = None  # Can't be known yet
        self.virsh = None  # Can't be known yet
        self._e = env
        self._p = params
        self.serial_dir = params.get("serial_dir", "/var/lib/libvirt")

    @property
    def start_vm(self):
        # Required parameter
        return bool('yes' == self._p['start_vm'])

    @property
    def main_vm(self):
        # Required parameter
        return self._e.get_vm(self._p["main_vm"])

    @property
    def file_ref(self):
        default = "normal"
        return self._p.get('vadu_file_ref', default)

    @property
    def dom_ref(self):
        default = "name"
        return self._p.get('vadu_dom_ref', default)

    @property
    def dom_value(self):
        default = None
        return self._p.get('vadu_dom_value', default)

    @property
    def extra(self):
        default = None
        return self._p.get('vadu_extra', default)

    @property
    def status_error(self):
        default = 'no'
        return bool('yes' == self._p.get('status_error', default))

    @property
    def mmconfig(self):
        default = 'no'
        return bool('yes' == self._p.get('vadu_config_option', default))

    @property
    def preboot_function_error(self):
        return bool("yes" == self._p['vadu_preboot_function_error'])

    @property
    def pstboot_function_error(self):
        return bool("yes" == self._p['vadu_pstboot_function_error'])

    @property
    def domain_positional(self):
        default = 'no'
        return bool('yes' == self._p.get('vadu_domain_positional', default))

    @property
    def file_positional(self):
        default = 'no'
        return bool('yes' == self._p.get('vadu_file_positional', default))

    @property
    def devs(self):
        return self._p.objects('vadu_dev_objs')  # mandatory parameter

    def dev_params(self, class_name):
        """
        Return Dictionary after parsing out prefix + class name postfix

        e.g. vadu_dev_obj_meg_VirtualDisk = 100
             ^^^^^^^^^^^^^ ^  ^^^^^^^^^^    ^
        strip   prefix     |  classname     |
                           |                |
                           |                |
        Return        key--+         value--+
        """

        # Roll up all keys with '_class_name' into top-level
        # keys with same name and no '_class_name' postfix.
        #       See Params.object_params() docstring
        object_params = self._p.object_params(class_name)
        # Return variable to hold modified key names
        device_params = {}
        for prefixed_key, original_value in list(iteritems(object_params)):
            # They get unrolled, but originals always left behind, skip them
            if prefixed_key.count(class_name):
                continue
            if prefixed_key.startswith(self.test_prefix):
                stripped_key = prefixed_key[len(self.test_prefix):]
                device_params[stripped_key] = original_value
        # The 'count' key required by all VADU AttachDeviceBase subclasses
        if 'count' not in list(device_params.keys()):
            # stick prefix back on so error message has meaning
            self.test.error('%scount is a required parameter'
                            % (self.test_prefix))
        return device_params

    @staticmethod
    def cleanup(test_dev_list):
        xcpt_list = []
        for device in test_dev_list:
            try:
                device.cleanup()
                # Attempt to finish entire list before raising
                # any exceptions that occurred
            # ignore pylint W0703 - exception acumulated and raised below
            except Exception as xcept_obj:
                xcpt_list.append(xcept_obj)
        if xcpt_list:
            raise RuntimeError("One or more exceptions occurred during "
                               "cleanup: %s" % str(xcpt_list))


class TestDeviceBase(object):

    """
    Base class for test devices creator and verification subclasses
    """

    # Class-specific unique string
    identifier = None
    # Parameters that come in from Cartesian:
    count = 0  # number of devices to make/test
    # flag for use in test and by operate() & function() methods
    booted = False

    def __init__(self, test_params, test):
        """
        Setup one or more device xml for a device based on TestParams instance
        """
        if self.__class__.identifier is None:
            identifier = utils_misc.generate_random_string(4)
            self.__class__.identifier = identifier
        # how many of this type of device to make
        self.test_params = test_params
        self.test = test
        # Copy params for this class into attributes
        cls_name = self.__class__.__name__
        # Already have test-prefix stripped off
        for key, value in list(iteritems(self.test_params.dev_params(cls_name))):
            # Any keys with _anything are not used by this class
            if key.count('_') > 0:
                logging.debug("Removing key: %s from params for class %s",
                              test_params.test_prefix + key, cls_name)
                continue
            # Attempt to convert numbers
            try:
                setattr(self, key, int(value))
            except ValueError:
                setattr(self, key, value)
        if self.count < 1:
            self.test.error("Configuration for class %s count must "
                            "be specified and greater than zero")
        logging.info("Setting up %d %s device(s)", self.count, cls_name)
        # Setup each device_xml instance
        self._device_xml_list = [self.init_device(index)
                                 # test_params.dev_params() enforces count
                                 for index in range(0, self.count)]

    def cleanup(self):
        """
        Remove any temporary files or processes created for testing
        """
        pass

    @property
    def device_xmls(self):
        """
        Return list of device_xml instances
        """
        return self._device_xml_list

    @property
    def operation_results(self):
        """
        Return a list of True/False lists for operation state per device
        """
        return [self.operate(index) for index in range(self.count)]

    @property
    def function_results(self):
        """
        Return a list of True/False lists for functional state per device
        """
        return [self.function(index) for index in range(self.count)]

    @staticmethod
    def good_results(results_list):
        """
        Return True if all member lists contain only True values
        """
        for outer in results_list:
            for inner in outer:
                if inner is False:
                    return False
        return True

    @staticmethod
    def bad_results(results_list):
        """
        Return True if all member lists contain only False values
        """
        for outer in results_list:
            for inner in outer:
                if inner is True:
                    return False
        return True

    # These should be overridden in subclasses
    def init_device(self, index):
        """
        Initialize and return instance of device xml for index
        """
        raise NotImplementedError

    def operate(self, index):
        """
        Return True/False (good/bad) result of operating on a device
        """
        # N/B: Take care of self.started
        raise NotImplementedError

    def function(self, index):
        """
        Return True/False device functioning
        """
        # N/B: Take care of self.test_params.start_vm
        raise NotImplementedError


def make_vadu_dargs(test_params, xml_filepath, test):
    """
    Return keyword argument dict for virsh attach, detach, update functions

    @param: test_params: a TestParams object
    @param: xml_filepath: Full path to device XML file (may not exist)
    """
    dargs = {}
    # Params value for domain reference (i.e. specific name, number, etc).
    if test_params.dom_value is None:  # No specific value set
        if test_params.dom_ref == "name":  # reference by runtime name
            domain = test_params.main_vm.name
        elif test_params.dom_ref == "id":
            domain = test_params.main_vm.get_id()
        elif test_params.dom_ref == "uuid":
            domain = test_params.main_vm.get_uuid()
        elif test_params.dom_ref == "bad_domain_hex":
            domain = "0x%x" % int(test_params.main_vm.get_id())
        elif test_params.dom_ref == "none":
            domain = None
        else:
            test.error("Parameter vadu_dom_ref or "
                       "vadu_dom_value are required")
    else:  # Config. specified a vadu_dom_value
        domain = test_params.dom_value

    if test_params.file_ref == "normal":  # The default
        file_value = xml_filepath  # Use un-altered path
    elif test_params.file_ref == "empty":  # empty string
        file_value = ""  # Empty string argument will be passed!
    elif test_params.file_ref == "missing":
        file_value = os.path.join("path", "does", "not", "exist")
    elif test_params.file_ref == "none":
        file_value = None  # No file specified
    else:
        test.error("Parameter vadu_file_ref is reuqired")

    if test_params.domain_positional:  # boolean
        dargs['domainarg'] = domain
    else:
        dargs['domain_opt'] = domain

    if test_params.file_positional:
        dargs['filearg'] = file_value
    else:
        dargs['file_opt'] = file_value

    if test_params.mmconfig:
        dargs['flagstr'] = "--config"
    else:
        dargs['flagstr'] = ""

    if test_params.extra is not None:
        dargs['flagstr'] += " %s" % test_params.extra
    return dargs


class AttachDeviceBase(TestDeviceBase):

    """
    All operation behavior is same  all device types in this module
    """

    def operate(self, index):
        """
        Return True/False (good/bad) result of operating on a device
        """
        vadu_dargs = make_vadu_dargs(self.test_params,
                                     self.device_xmls[index].xml,
                                     self.test)
        # Acts as a dict for it's own API params
        self.test_params.virsh['debug'] = True
        vadu_dargs.update(self.test_params.virsh)
        options = vadu_dargs.get('flagstr')
        if options:
            opt_list = options.split()
            for opt in opt_list:
                if not virsh.has_command_help_match("attach-device", opt) and\
                   not self.test_params.status_error:
                    self.test.cancel("Current libvirt version doesn't "
                                     "support '%s' for attach-device"
                                     " command" % opt)
        cmdresult = self.test_params.virsh.attach_device(**vadu_dargs)
        self.test_params.virsh['debug'] = False
        # Command success is not enough, must also confirm activity worked

        # output XML no matter attach pass or not
        logging.debug("Attached XML:")
        for line in str(self.device_xmls[index]).splitlines():
            logging.debug("%s", line)

        if (cmdresult.exit_status == 0):
            if (cmdresult.stdout.strip().count('attached successfully') or
                    cmdresult.stderr.strip().count('attached successfully')):
                return True
        else:
            # See analyze_negative_results - expects return of true
            if self.test_params.status_error:
                return True
            else:
                return False

    # Overridden in classes below
    def init_device(self, index):
        raise NotImplementedError

    def function(self, index):
        raise NotImplementedError


class SerialFile(AttachDeviceBase):

    """
    Simplistic File-backed isa-serial device test helper

    Consumes Cartesian object parameters:
        count - number of devices to make
        model - target model of device
    """

    identifier = None
    type_name = "file"

    def make_filepath(self, index):
        """Return full path to unique filename per device index"""
        # auto-cleaned at end of test
        if self.type_name == 'file':
            if libvirt_version.version_compare(3, 2, 0):
                serial_dir = '/var/lib/libvirt'
            else:
                serial_dir = self.test_params.serial_dir
        else:
            serial_dir = data_dir.get_tmp_dir()

        return os.path.join(serial_dir, 'serial_%s_%s-%d.log'
                            % (self.type_name, self.identifier, index))

    @staticmethod
    def make_source(filepath):
        """Create filepath on disk"""
        with open(filepath, "wb") as source_file:
            return

    def init_device(self, index):
        filepath = self.make_filepath(index)
        self.make_source(filepath)
        serialclass = self.test_params.vmxml.get_device_class('serial')
        serial_device = serialclass(type_name=self.type_name,
                                    virsh_instance=self.test_params.virsh)
        serial_device.add_source(path=filepath)
        # Assume default domain serial device on port 0 and index starts at 0
        serial_device.add_target(port=str(index + 1))
        if hasattr(self, 'models'):
            this_model = self.models.split(" ")[index]
            if 'sclp' in this_model:
                serial_device.update_target(index=0, port=str(index+1), type='sclp-serial')
            serial_device.target_model = this_model
        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            serial_device.alias = {'name': self.alias + str(index)}
        return serial_device

    def cleanup(self):
        for index in range(0, self.count):
            try:
                os.unlink(self.make_filepath(index))
            except OSError:
                pass  # Don't care if not there

    def function(self, index):
        # TODO: Try to read/write some serial data
        # Just a stub for now
        logging.info("STUB: Serial device functional test passed: %s",
                     str(not self.test_params.status_error))
        # Return an error if an error is expected
        return not self.test_params.status_error


class SerialPipe(SerialFile):

    """
    Simplistic pipe-backed isa-serial device
    """

    identifier = None
    type_name = "pipe"

    @staticmethod
    def make_source(filepath):
        try:
            os.unlink(filepath)
        except OSError:
            pass
        os.mkfifo(filepath)

    def init_device(self, index):
        return super(SerialPipe, self).init_device(index)  # stub for now


class SerialPty(AttachDeviceBase):

    """
    Simple console pty device
    """

    type_name = "pty"

    def init_device(self, index):
        serialclass = self.test_params.vmxml.get_device_class('serial')
        serial_device = serialclass(type_name=self.type_name,
                                    virsh_instance=self.test_params.virsh)
        # Assume default domain serial device on port 0 and index starts at 0
        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            serial_device.alias = {'name': self.alias + str(index)}
        return serial_device

    def function(self, index):
        return not self.test_params.status_error


class Console(AttachDeviceBase):

    """
    Simple console device
    """

    def init_device(self, index):
        consoleclass = self.test_params.vmxml.get_device_class('console')
        console_device = consoleclass(type_name=self.type,
                                      virsh_instance=self.test_params.virsh)
        # Assume default domain console device on port 0 and index starts at 0
        console_device.add_target(type=self.targettype, port=str(index + 1))
        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            console_device.alias = {'name': self.alias + str(index)}
        return console_device

    def function(self, index):
        return not self.test_params.status_error


def _init_device_Serial2Console(self, index):
    """
    Helper function to merge init_device from two parent
    classes SerialFile/Pipe and Console.

    :param self: instance of ConsoleFile/Pipe
    :param index: device index
    """

    self.type = self.type_name
    console_device = Console.init_device(self, index)
    filepath = self.make_filepath(index)
    self.make_source(filepath)
    console_device.add_source(path=filepath)
    return console_device


class ConsoleFile(Console, SerialFile):
    """
    Simplistic pipe-backed console
    """

    def init_device(self, index):
        return _init_device_Serial2Console(self, index)


class ConsolePipe(Console, SerialPipe):
    """
    Simplistic file-backed console
    """

    def init_device(self, index):
        return _init_device_Serial2Console(self, index)


class Channel(AttachDeviceBase):

    """
    Simple channel device
    """

    def init_device(self, index):
        channelclass = self.test_params.vmxml.get_device_class('channel')
        channel_device = channelclass(type_name=self.type,
                                      virsh_instance=self.test_params.virsh)
        if hasattr(self, 'sourcemode') and hasattr(self, 'sourcepath'):
            channel_device.add_source(mode=self.sourcemode,
                                      path=self.sourcepath)
        if hasattr(self, 'targettype') and hasattr(self, 'targetname'):
            channel_device.add_target(type=self.targettype,
                                      name=self.targetname)
        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            channel_device.alias = {'name': self.alias + str(index)}
        return channel_device

    def function(self, index):
        return not self.test_params.status_error


class Controller(AttachDeviceBase):

    """
    Simple controller device
    """

    def init_device(self, index):
        controllerclass = self.test_params.vmxml.get_device_class('controller')
        controller_device = controllerclass(type_name=self.type,
                                            virsh_instance=self.test_params.virsh)
        controller_device.model = self.model

        if not libvirt_version.version_compare(1, 3, 5):
            controller_devices = []
            devices = self.test_params.vmxml.get_devices()
            try:
                controller_devices = devices.by_device_tag('controller')
            except xcepts.LibvirtXMLError:
                # Handle case without any controller tags
                pass
            controller_device.index = str(0)
            for controller in controller_devices:
                if controller['type'] == controller_device.type:
                    controller_device.index = str(int(controller_device.index) + 1)

        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            controller_device.alias = {'name': self.alias + str(index)}
        return controller_device

    def function(self, index):
        return not self.test_params.status_error


class VirtualDiskBasic(AttachDeviceBase):

    """
    Simple File-backed virtio/usb/sata/scsi/ide raw disk device
    """

    identifier = None
    count = 0  # number of devices to make
    meg = 0  # size of device in megabytes (1024**2)
    devidx = 1  # devnode name index to start at (0 = vda/sda, 1 = vdb/sdb, etc)
    devtype = 'file'
    targetbus = "virtio"
    test_data_list = []  # list of all created disks

    @staticmethod
    def devname_suffix(index):
        """
        Return letter code for index position, a, b, c...aa, ab, ac...
        """
        # http://stackoverflow.com/questions/14381940/
        # python-pair-alphabets-after-loop-is-completed/14382997#14382997
        def multiletters():
            """Generator of count-by-letter strings"""
            for num in itertools.count(1):
                for prod in itertools.product(ascii_lowercase, repeat=num):
                    yield ''.join(prod)
        return next(itertools.islice(multiletters(), index, index + 1))

    def devname(self, index):
        """
        Return disk target name
        """
        sd_count = 0
        vd_count = 0
        hd_count = 0

        devices = self.test_params.vmxml.get_devices()
        disk_devices = devices.by_device_tag('disk')
        for disk in disk_devices:
            if disk.target['dev'].startswith('sd'):
                sd_count += 1
            if disk.target['dev'].startswith('vd'):
                vd_count += 1
            if disk.target['dev'].startswith('hd'):
                hd_count += 1

        if self.targetbus in ['usb', 'scsi', 'sata']:
            devname_prefix = "sd"
            self.devidx = sd_count
        elif self.targetbus == "virtio":
            devname_prefix = "vd"
            self.devidx = vd_count
        elif self.targetbus == "ide":
            devname_prefix = "hd"
            self.devidx = hd_count
        else:
            self.test.cancel("Unsupport bus '%s' in this test" %
                             self.targetbus)
        return devname_prefix + self.devname_suffix(self.devidx + index)

    def make_image_file_path(self, index):
        """Create backing file for test disk device"""
        return os.path.join(data_dir.get_tmp_dir(),
                            'disk_%s_%s_%d.raw'
                            % (self.__class__.__name__,
                               self.identifier,
                               index))

    def make_image_file(self, index):
        """Create sparse backing file by writing it's full path at it's end"""
        # Truncate file
        image_file_path = self.make_image_file_path(index)
        with open(image_file_path, 'wb') as image_file:
            byte_size = self.meg * 1024 * 1024
            # Make sparse file byte_size long (starting from 0)
            image_file.truncate(byte_size)
            # Write simple unique data to file before end
            image_file.seek(byte_size - len(image_file_path) - 1)
            # newline required by aexpect in function()
            image_file.write((image_file_path + '\n').encode(encoding=astring.ENCODING))

    def init_device(self, index):
        """
        Initialize and return instance of device xml for index
        """
        self.make_image_file(index)
        disk_class = self.test_params.vmxml.get_device_class('disk')
        disk_device = disk_class(type_name=self.devtype,
                                 virsh_instance=self.test_params.virsh)
        disk_device.driver = {'name': 'qemu', 'type': 'raw'}
        # No source elements by default
        source_properties = {'attrs':
                             {'file': self.make_image_file_path(index)}}
        source = disk_device.new_disk_source(**source_properties)
        disk_device.source = source  # Modified copy, not original
        dev_name = self.devname(index)
        disk_device.target = {'dev': dev_name, 'bus': self.targetbus}
        # libvirt will automatically add <address> element
        if hasattr(self, 'alias') and libvirt_version.version_compare(3, 9, 0):
            disk_device.alias = {'name': self.alias + str(index)}
        return disk_device

    def cleanup(self):
        for index in range(0, self.count):
            try:
                os.unlink(self.make_image_file_path(index))
            except OSError:
                pass  # Don't care if not there

    def function(self, index):
        """
        Return True/False (good/bad) result of a device functioning
        """
        dev_name = '/dev/' + self.devname(index)
        # Host image path is static known value
        test_data = self.make_image_file_path(index)
        if test_data not in self.test_data_list:
            self.test_data_list.append(test_data)
        byte_size = self.meg * 1024 * 1024
        # Place test data at end of device to also confirm sizing
        offset = byte_size - len(test_data)
        logging.info('Trying to read test data, %dth device %s, '
                     'at offset %d.', index + 1, dev_name, offset)
        session = None

        # Since we know we're going to fail, no sense waiting for the
        # default timeout to expire or login()'s code to get_address
        # (currently 300 seconds) or any other timeout code.  With the
        # guest not alive, just return failure. Not doing so caused a
        # a 96 minute pause per test with 16 devices all waiting for
        # the timeouts to occur and probably a days worth of log messages
        # waiting for something to happen that can't.
        if not self.test_params.main_vm.is_alive():
            logging.debug("VirtualDiskBasic functional test skipping login "
                          "vm is not alive.")
            return False

        try:
            session = self.test_params.main_vm.login()

            # The device may not be ready on guest,
            # just wait at most 5 seconds here
            utils_misc.wait_for(lambda:
                                not session.cmd_status('ls %s' % dev_name), 5)

            # aexpect combines stdout + stderr, throw away stderr
            output = session.cmd_output('tail -c %d %s'
                                        % (len(test_data) + 1, dev_name))
            session.close()
        except (virt_vm.VMAddressError, remote.LoginError,
                aexpect.ExpectError, aexpect.ShellError):
            try:
                session.close()
            except AttributeError:
                pass   # session == None
            logging.debug("VirtualDiskBasic functional test raised an exception")
            return False
        else:
            gotit = [data for data in self.test_data_list if output.strip('\n') in data]
            logging.info("Test data detected in device: %s",
                         gotit)
            if not gotit:
                logging.debug("Expecting: '%s' in %s", test_data, self.test_data_list)
                logging.debug("Received: '%s'", output)
                return False
            else:
                return True


def operational_action(test_params, test_devices, operational_results):
    """
    Call & store result list from operate() method on every device
    """
    if test_params.status_error:
        logging.info("Running operational tests: Failure is expected!")
    else:
        logging.info("Running operational tests")
    for device in test_devices:
        operational_results.append(device.operation_results)  # list of bools
    # STUB:
    # for device in test_devices:
    #    if test_params.status_error:
    #        operational_results = [False] * device.count
    #    else:
    #        operational_results = [True] * device.count


def preboot_action(test_params, test_devices, preboot_results):
    """
    Call & store result of function() method on every device
    """
    if test_params.preboot_function_error:
        logging.info("Running pre-reboot functional tests: Failure expected!")
    else:
        logging.info("Running pre-reboot functional tests")
    for device in test_devices:
        preboot_results.append(device.function_results)  # list of bools
    # STUB:
    # for device in test_devices:
    #    if test_params.status_error:
    #        preboot_results = [False] * device.count
    #    else:
    #        preboot_results = [True] * device.count


def postboot_action(test_params, test_devices, pstboot_results):
    """
    Call & store result of function() method on every device
    """
    if test_params.pstboot_function_error:
        logging.info("Running post-reboot functional tests: Failure expected!")
    else:
        logging.info("Running post-reboot functional tests")
    for device in test_devices:
        pstboot_results.append(device.function_results)  # list of bools
    # STUB:
    # for device in test_devices:
    #    if test_params.status_error:
    #        pstboot_results = [False] * device.count
    #    else:
    #        pstboot_results = [True] * device.count


# Save a little typing
all_true = TestDeviceBase.good_results
all_false = TestDeviceBase.bad_results


def analyze_negative_results(test_params, operational_results,
                             preboot_results, pstboot_results):
    """
    Analyze available results, return error message if fail
    """
    if operational_results:
        if not all_true(operational_results):
            return ("Negative testing operational test failed")
    if preboot_results and test_params.start_vm:
        if not all_false(preboot_results):
            return ("Negative testing pre-boot functionality test passed")
    if pstboot_results:
        if not all_false(pstboot_results):
            return ("Negative testing post-boot functionality "
                    "test passed")


def analyze_positive_results(test_params, operational_results,
                             preboot_results, pstboot_results):
    """
    Analyze available results, return error message if fail
    """
    if operational_results:
        if not all_true(operational_results):
            return ("Positive operational test failed")
    if preboot_results and test_params.start_vm:
        if not all_true(preboot_results):
            if not test_params.preboot_function_error:
                return ("Positive pre-boot functionality test failed")
            # else: An error was expected
    if pstboot_results:
        if not all_true(pstboot_results):
            if not test_params.pstboot_function_error:
                return ("Positive post-boot functionality test failed")


def analyze_results(test_params, test, operational_results=None,
                    preboot_results=None, pstboot_results=None):
    """
    Analyze available results, raise error message if fail
    """
    fail_msg = None  # Pass: None, Fail: failure reason
    if test_params.status_error:  # Negative testing
        fail_msg = analyze_negative_results(test_params, operational_results,
                                            preboot_results, pstboot_results)
    else:  # Positive testing
        fail_msg = analyze_positive_results(test_params, operational_results,
                                            preboot_results, pstboot_results)
    if fail_msg is not None:
        test.fail(fail_msg)


def remove_chardevs(vm_name, vmxml):
    """
    Removes chardevs from domain xml to avoid errors due to
    restrictions on the number of chardevs with same target type,
    e.g. on s390x
    :param vm_name: domain name
    :param vmxml: domain xml
    :return: None
    """
    virsh.destroy(vm_name, debug=True)
    chardev_types = ['serial', 'console']
    for chardev_type in chardev_types:
        vmxml.del_device(chardev_type, by_tag=True)
    vmxml.sync()
    virsh.start(vm_name, debug=True, ignore_status=False)


def remove_non_disks(vm_name, vmxml):
    """
    Remove non-disk disks before test to avoid influence.
    None-disk disks such as cdrom with bus 'SATA' will be recognized
    as 'cdrom', not 'sda' as expected, therefore the attached SATA
    disk will not be recognized as 'sdb' as expected.
    :param vm_name: domain name
    :param vmxml: domain xml
    :return: None
    """
    disks = vmxml.devices.by_device_tag('disk')
    for disk in disks:
        if disk.device != 'disk':
            virsh.detach_disk(vm_name, disk.target['dev'],
                              extra='--current',
                              debug=True)


def update_controllers_ppc(vm_name, vmxml):
    """
    Update controller settings of vmxml to fit for test

    :param vm_name: domain name
    :param vmxml: domain xml
    :return:
    """
    machine = platform.machine().lower()
    if 'ppc64le' in machine:
        # Remove old scsi controller, replace with virtio-scsi controller
        vmxml.del_controller('scsi')
        vmxml.sync()
        virsh.start(vm_name, debug=True, ignore_status=False)


def run(test, params, env):
    """
    Test virsh {at|de}tach-device command.

    1) Prepare test environment and its parameters
    2) Operate virsh on one or more devices
    3) Check functionality of each device
    4) Check functionality of mmconfig option
    5) Restore domain
    6) Handle results
    """
    vm_name = params.get('main_vm')
    machine_type = params.get("machine_type", "pc")
    backup_vm_xml = vmxml = VMXML.new_from_inactive_dumpxml(vm_name)

    dev_obj = params.get("vadu_dev_objs")
    vadu_vdb = int(params.get("vadu_dev_obj_count_VirtualDiskBasic", "0"))
    vadu_dom_ref = params.get("vadu_dom_ref", "dom_ref")
    status_error = "yes" == params.get("status_error", "no")
    vadu_domain_positional = "yes" == params.get("vadu_domain_positional", "no")
    vadu_file_positional = "yes" == params.get("vadu_file_positional", "no")
    vadu_preboot_error = "yes" == params.get("vadu_preboot_function_error", "no")

    # Skip chardev hotplug on rhel6 host as it is not supported
    if "Serial" in dev_obj:
        if not libvirt_version.version_compare(1, 1, 0):
            test.cancel("You libvirt version not supported"
                        " attach/detach Serial devices")
    # Prepare test environment and its parameters
    test_params = TestParams(params, env, test)

    # Reset pci controllers num to fix error "No more available PCI slots"
    if params.get("reset_pci_controllers_nums", "no") == "yes" and "VirtualDiskBasic" in dev_obj:
        # Only apply change on some cases with feature:
        # block.multi_virtio_file..hot_attach_hot_vm..name_ref.file_positional.domain_positional
        # block.multi_virtio_file..hot_attach_hot_vm_current.name_ref.file_positional.domain_positional
        # Those cases often fialed on aarch64 due to error "No more available PCI slots"
        if vadu_vdb == 16 and not status_error \
            and not vadu_preboot_error and 'name' in vadu_dom_ref \
                and vadu_file_positional and vadu_domain_positional:

            previous_state_running = test_params.main_vm.is_alive()
            if previous_state_running:
                test_params.main_vm.destroy(gracefully=True)
            libvirt_pcicontr.reset_pci_num(vm_name, 24)
            logging.debug("Guest XMl with adding many controllers: %s", test_params.main_vm.get_xml())
            if previous_state_running:
                test_params.main_vm.start()

    remove_non_disks(vm_name, vmxml)
    update_controllers_ppc(vm_name, vmxml)

    if params.get("remove_all_chardev", "no") == "yes":
        remove_chardevs(vm_name, vmxml)

    logging.info("Preparing initial VM state")

    if test_params.start_vm:
        # Make sure VM is working
        test_params.main_vm.verify_alive()
        test_params.main_vm.wait_for_login().close()
    else:  # VM not suppose to be started
        if test_params.main_vm.is_alive():
            test_params.main_vm.destroy(gracefully=True)
    # Capture backup of original XML early in test
    test_params.vmxml = VMXML.new_from_inactive_dumpxml(
        test_params.main_vm.name)
    # All devices should share same access state
    test_params.virsh = virsh.Virsh(ignore_status=True)
    logging.info("Creating %d test device instances", len(test_params.devs))
    # Create test objects from cfg. class names via subclasses above
    test_devices = [globals()[class_name](test_params, test)  # instantiate
                    for class_name in test_params.devs]  # vadu_dev_objs
    operational_results = []
    preboot_results = []
    pstboot_results = []
    try:
        operational_action(test_params, test_devices, operational_results)
        # Fail early if attach-device return value is not expected
        analyze_results(test_params, test,
                        operational_results=operational_results)

        #  Can't do functional testing with a cold VM, only test hot-attach
        preboot_action(test_params, test_devices, preboot_results)

        logging.info("Preparing test VM state for post-boot functional testing")
        if test_params.start_vm:
            # Hard-reboot required
            test_params.main_vm.destroy(gracefully=True,
                                        free_mac_addresses=False)
        try:
            logging.debug("vmxml %s", VMXML.new_from_inactive_dumpxml(vm_name))
            test_params.main_vm.start()
        except virt_vm.VMStartError as details:
            test.fail('VM Failed to start for some reason!: %s' % details)
        # Signal devices reboot is finished
        for test_device in test_devices:
            test_device.booted = True
        logging.debug("Current VMXML %s", test_params.main_vm.get_xml())
        test_params.main_vm.wait_for_login().close()
        postboot_action(test_params, test_devices, pstboot_results)
        analyze_results(test_params, test,
                        preboot_results=preboot_results,
                        pstboot_results=pstboot_results)
    finally:
        logging.info("Restoring VM from backup, then checking results")
        test_params.main_vm.destroy(gracefully=False,
                                    free_mac_addresses=False)
        test_params.vmxml.undefine()
        test_params.vmxml.restore()  # Recover the original XML
        test_params.vmxml.define()
        if not test_params.start_vm:
            # Test began with not start_vm, shut it down.
            test_params.main_vm.destroy(gracefully=True)
        # Device cleanup can raise multiple exceptions, do it last:
        logging.info("Cleaning up test devices")
        try:
            test_params.cleanup(test_devices)
        except RuntimeError as e:
            logging.debug("Error cleaning up devices: %s", e)
        backup_vm_xml.sync()
