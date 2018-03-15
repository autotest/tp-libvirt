import os
import logging

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import data_dir
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    Test libvirtd_config parameter in /etc/sysconfig/libvirtd.

    1) Change libvirtd_config in sysconfig;
    2) Change host_uuid in newly defined libvirtd.conf file;
    3) Restart libvirt daemon;
    4) Check if libvirtd successfully started;
    5) Check if host_uuid updated accordingly;
    """
    def get_init_name():
        """
        Internal function to determine what executable is PID 1,
        :return: executable name for PID 1, aka init
        """
        with open('/proc/1/comm') as fp:
            name = fp.read().strip()
            return name

    libvirtd_config = params.get('libvirtd_config', 'not_set')
    expected_result = params.get('expected_result', 'success')

    if get_init_name() == 'systemd':
        logging.info('Init process is systemd, '
                     'LIBVIRTD_CONFIG should not working.')
        expected_result = 'unchanged'

    sysconfig = utils_config.LibvirtdSysConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    config_path = ""
    check_uuid = '13371337-1337-1337-1337-133713371337'
    try:
        if libvirtd_config == 'not_set':
            del sysconfig.LIBVIRTD_CONFIG
        elif libvirtd_config == 'exist_file':
            config_path = os.path.join(data_dir.get_tmp_dir(), 'test.conf')
            open(config_path, 'a').close()

            config = utils_config.LibvirtdConfig(config_path)
            config.host_uuid = check_uuid

            sysconfig.LIBVIRTD_CONFIG = config_path
        else:
            sysconfig.LIBVIRTD_CONFIG = libvirtd_config

        if not libvirtd.restart():
            if expected_result != 'unbootable':
                test.fail('Libvirtd is expected to be started '
                          'with LIBVIRTD_CONFIG = '
                          '%s' % sysconfig.LIBVIRTD_CONFIG)
        if expected_result == 'unbootable':
            test.fail('Libvirtd is not expected to be started '
                      'with LIBVIRTD_CONFIG = '
                      '%s' % sysconfig.LIBVIRTD_CONFIG)
        cur_uuid = capability_xml.CapabilityXML()['uuid']
        if cur_uuid == check_uuid:
            if expected_result == 'unchange':
                test.fail('Expected host UUID is not changed, '
                          'but got %s' % cur_uuid)
        else:
            if expected_result == 'change':
                test.fail('Expected host UUID is %s, but got %s' %
                          (check_uuid, cur_uuid))

    finally:
        if libvirtd_config == 'exist_file':
            config.restore()
            if os.path.isfile(config_path):
                os.remove(config_path)
        sysconfig.restore()
        libvirtd.restart()
