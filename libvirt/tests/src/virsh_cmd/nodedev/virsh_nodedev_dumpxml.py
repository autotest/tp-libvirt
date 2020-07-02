import logging
import time
import os

from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml import nodedev_xml
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command virsh nodedev-dumpxml.
    step1.get param from params.
    step2.do nodedev dumpxml.
    step3.clean up.
    """

    def dump_nodedev_xml(dev_name, dev_opt="", **dargs):
        """
        Do dumpxml and check the result.

        step1.execute nodedev-dumpxml command.
        step1.compare info in xml with info in sysfs.

        :param dev_name: name of device.
        :param dev_opt: command extra options
        :param dargs: extra dict args
        """
        result = virsh.nodedev_dumpxml(dev_name, options=dev_opt, **dargs)
        libvirt.check_exit_status(result)
        logging.debug('Executing "virsh nodedev-dumpxml %s" finished.', dev_name)
        # Compare info in xml with info in sysfs.
        nodedevice_xml = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name)

        if not nodedevice_xml.validates:
            test.error("nodedvxml of %s is not validated." % (dev_name))
        # Get the dict of key to value in xml.
        # nodedev_dict_xml contain the all keys and values in xml need checking.
        nodedev_dict_xml = nodedevice_xml.get_key2value_dict()

        # Get the dict of key to path in sysfs.
        # nodedev_syspath_dict contain the all keys and the path of file which contain
        #                 information for each key.
        nodedev_syspath_dict = nodedevice_xml.get_key2syspath_dict()

        # Get the values contained in files.
        # nodedev_dict_sys contain the all keys and values in sysfs.
        nodedev_dict_sys = {}
        for key, filepath in list(nodedev_syspath_dict.items()):
            with open(filepath, 'r') as f:
                value = f.readline().rstrip('\n')
            nodedev_dict_sys[key] = value

        # Compare the value in xml and in syspath.
        for key in nodedev_dict_xml:
            xml_value = nodedev_dict_xml.get(key)
            sys_value = nodedev_dict_sys.get(key)

            if not xml_value == sys_value:
                if (key == 'numa_node' and not
                        libvirt_version.version_compare(1, 2, 5)):
                    logging.warning("key: %s in xml is not supported yet" % key)
                else:
                    test.error("key: %s in xml is %s,"
                               "but in sysfs is %s." %
                               (key, xml_value, sys_value))
            else:
                continue

        logging.debug("Compare info in xml and info in sysfs finished"
                      "for device %s.", dev_name)

    def pci_devices_name(device_type):
        """
        Get the address of pci device
        :param device_type: type of device, such as pci, net , storage
        """
        devices_list = virsh.nodedev_list(tree='', cap=device_type)
        devices_name_list = devices_list.stdout.strip().splitlines()
        device_name = devices_name_list[0]
        return device_name

    # Init variables.
    status_error = ('yes' == params.get('status_error', 'no'))
    device_type = params.get('device_type', "")
    device_name = params.get('nodedev_device_name', 'ENTER.YOUR.PCI.DEVICE')
    if device_name.find('ENTER.YOUR.PCI.DEVICE') != -1:
        replace_name = pci_devices_name(device_type).strip()
        device_name = device_name.replace('ENTER.YOUR.PCI.DEVICE', replace_name).strip()
    device_opt = params.get('nodedev_device_opt', "")

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    virsh_dargs = {}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs['unprivileged_user'] = unprivileged_user
        virsh_dargs['uri'] = uri

    # change the polkit rule
    polkit_file = "/etc/polkit-1/rules.d/500-libvirt-acl-virttest.rules"
    if os.path.exists(polkit_file):
        replace_cmd = "sed -i 's/'ENTER.YOUR.PCI.DEVICE'/%s/g' /etc/polkit-1/rules.d/500-libvirt-acl-virttest.rules" % device_name
        cat_cmd = "cat /etc/polkit-1/rules.d/500-libvirt-acl-virttest.rules"
        replace_output = process.run(replace_cmd, shell=True).stdout_text
        cat_output = process.run(cat_cmd, shell=True).stdout_text

    # do nodedev dumpxml.
    try:
        time.sleep(10)
        dump_nodedev_xml(dev_name=device_name, dev_opt=device_opt,
                         **virsh_dargs)
        if status_error:
            test.fail('Nodedev dumpxml successed in negative test.')
    except Exception as e:
        if not status_error:
            test.fail('Nodedev dumpxml failed in positive test.'
                      'Error: %s' % e)
