import logging as log
import time
import os

from uuid import uuid1

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_misc
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder, \
    APMaskHelper, load_vfio_ap, unload_vfio_ap
from tempfile import mktemp

# minimal supported hwtype
HWTYPE = 11


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def find_devices_by_cap(test, cap_type):
    """
    Find device by capability

    :param test: test object
    :param cap_type: capability type
    """
    result = virsh.nodedev_list(cap=cap_type, debug=True)
    if result.exit_status:
        test.fail(result.stderr)

    device_names = result.stdout.strip().splitlines()
    return device_names


def create_nodedev_from_xml(uuid, adapter, domain):
    """
    Create a device defined by an XML file on the node

    :param uuid: id for mediated device
    :param adapter: adapter of crypto device in host
    :param domain: domain of crypto device in host
    """
    device_xml = """
<device>
<parent>ap_matrix</parent>
<capability type='mdev'>
<uuid>%s</uuid>
<type id='vfio_ap-passthrough'/>
<attr name="assign_adapter" value="0x%s"/>
<attr name="assign_domain" value="0x%s"/>
</capability>
</device>
""" % (uuid, adapter, domain)
    logging.debug("Prepare the nodedev XML: %s", device_xml)
    device_file = mktemp()
    with open(device_file, 'w') as xml_object:
        xml_object.write(device_xml)
    virsh.nodedev_create(device_file, debug=True)
    return device_file


def check_device_was_created(test, uuid, adapter, domain):
    """
    Check if the device was created successfully

    :param test: test object
    :param uuid: id for mediated device
    :param adapter: adapter of crypto device in host
    :param domain: domain of crypto device in host
    """
    devices_cmd = ("cat /sys/devices/vfio_ap/matrix/%s/matrix" % uuid)
    status, output = utils_misc.cmd_status_output(
        devices_cmd, verbose=True)
    mdev_created_successfully = output == "%s.%s" % (adapter, domain)
    if not mdev_created_successfully:
        raise test.fail("mdev device was not create successfully through "
                        "nodev-API")


def destroy_nodedev(dev_name):
    """
    Destroy (stop) a device on the node

    :param dev_name: name of mediated device
    """
    virsh.nodedev_destroy(dev_name, debug=True)
    # Sleep a few seconds to allow device be released completely
    # Here virsh nodedev-event(not virsh event) may help, but add additional complexity since
    # it need separate thread to use virsh nodedev-event, otherwise main thread
    # will be blocked
    time.sleep(10)


def check_device_was_destroyed(test):
    """
    Check if the device was created successfully

    :param test: test object
    """
    if find_devices_by_cap(test, 'mdev'):
        test.fail("The mdev device is still found after destroyed which is "
                  "not expected")


def run(test, params, env):
    '''
    1. Check if the crypto device in host valiable for passthrough
    2. Passthrough the crypto device
    2. Create the mdev
    3. Confirm the mdev was created successfully
    4. Confirm device availability in guest
    5. Destroy the mdev
    6. Confirm the mdev was destroyed successfully

    NOTE: It can take a while after loading vfio_ap for the
          matrix device to become available due to current
          performance issues with the API if there are several
          mdev definitions already available. The test supposes
          no other mdev devices have been defined yet in order
          to avoid complexity in the test code.

    :param test: test object
    :param params: Dict with test parameters
    :param env: Dict with the test environment
    :return:
    '''

    libvirt_version.is_libvirt_feature_supported(params)
    matrix_cap = 'ap_matrix'
    device_file = None
    mask_helper = None

    info = CryptoDeviceInfoBuilder.get()
    if int(info.entries[0].hwtype) < HWTYPE:
        test.cancel("vfio-ap requires HWTYPE bigger than %s." % HWTYPE)
    uuid = str(uuid1())
    adapter = info.entries[0].card
    domain = info.entries[1].domain
    try:
        if not find_devices_by_cap(test, matrix_cap):
            load_vfio_ap()
        if find_devices_by_cap(test, matrix_cap):
            devices = [info.domains[0]]
            mask_helper = APMaskHelper.from_infos(devices)
            device_file = create_nodedev_from_xml(uuid, adapter, domain)
        else:
            raise test.fail("Could not get %s correctly through nodedev-API" %
                            matrix_cap)
        check_device_was_created(test, uuid, adapter, domain)
        # the test assumes there's no other mdev
        dev_name = find_devices_by_cap(test, 'mdev')[0]
        destroy_nodedev(dev_name)
        check_device_was_destroyed(test)
    finally:
        if mask_helper:
            mask_helper.return_to_host_all()
        unload_vfio_ap()
        if device_file:
            os.remove(device_file)
