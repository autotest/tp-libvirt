import logging
import uuid
import os

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_split_daemons
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    Test host_uuid parameter in libvird.conf.

    1) Change host_uuid in libvirtd.conf;
    2) Restart libvirt daemon;
    3) Check if libvirtd successfully started;
    4) Check current host UUID by `virsh capabilities`;
    """
    def get_dmi_uuid():
        """
        Retrieve the UUID of DMI, which is usually used as libvirt daemon
        host UUID.

        :return : DMI UUID if it can be located or None if can't.
        """
        uuid_paths = [
            '/sys/devices/virtual/dmi/id/product_uuid',
            '/sys/class/dmi/id/product_uuid',
        ]
        for path in uuid_paths:
            if os.path.isfile(path):
                with open(path) as dmi_fp:
                    uuid = dmi_fp.readline().strip().lower()
                    return uuid

    uuid_type = params.get("uuid_type", "lowercase")
    expected_result = params.get("expected_result", "success")
    new_uuid = params.get("new_uuid", "")

    # We are expected to get an standard UUID format on success.
    if expected_result == 'success':
        expected_uuid = str(uuid.UUID(new_uuid))

    config = utils_config.LibvirtdConfig()
    if utils_split_daemons.is_modular_daemon():
        config = utils_config.VirtQemudConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        orig_uuid = capability_xml.CapabilityXML()['uuid']
        logging.debug('Original host UUID is %s' % orig_uuid)

        if uuid_type == 'not_set':
            # Remove `host_uuid` in libvirtd.conf.
            del config.host_uuid
        elif uuid_type == 'unterminated':
            # Change `host_uuid` in libvirtd.conf.
            config.set_raw('host_uuid', '"%s' % new_uuid)
        elif uuid_type == 'unquoted':
            config.set_raw('host_uuid', new_uuid)
        elif uuid_type == 'single_quoted':
            config.set_raw('host_uuid', "'%s'" % new_uuid)
        else:
            config.host_uuid = new_uuid

        # Restart libvirtd to make change valid. May raise ConfigError
        # if not succeed.
        if not libvirtd.restart():
            if expected_result != 'unbootable':
                test.fail('Libvirtd is expected to be started '
                          'with host_uuid = %s' % config['host_uuid'])
            return

        if expected_result == 'unbootable':
            test.fail('Libvirtd is not expected to be started '
                      'with host_uuid = %s' % config['host_uuid'])

        cur_uuid = capability_xml.CapabilityXML()['uuid']
        logging.debug('Current host UUID is %s' % cur_uuid)

        if expected_result == 'success':
            if cur_uuid != expected_uuid:
                test.fail(
                    "Host UUID doesn't changed as expected"
                    " from %s to %s, but %s" % (orig_uuid, expected_uuid,
                                                cur_uuid))
        # libvirtd should use system DMI UUID for all_digit_same or
        # not_set host_uuid.
        elif expected_result == 'dmi_uuid':
            dmi_uuid = get_dmi_uuid()
            logging.debug("DMI UUID is %s." % dmi_uuid)

            if dmi_uuid is not None and cur_uuid != dmi_uuid:
                test.fail(
                    "Host UUID doesn't changed from "
                    "%s to DMI UUID %s as expected, but %s" % (
                        orig_uuid, dmi_uuid, cur_uuid))
    finally:
        config.restore()
        if not libvirtd.is_running():
            libvirtd.start()
