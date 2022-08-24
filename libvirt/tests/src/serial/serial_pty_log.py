import logging as log

from virttest import libvirt_xml
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh

from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices import librarian


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_pty_log_file(file_path, boot_prompt):
    """
    Check if pty log file has vm boot up logs

    :param file_path: the pty log file path
    :param boot_prompt: the expected login prompt
    :return: True or False according the result of finding
    """

    with open(file_path, errors='ignore') as fp:
        contents = fp.read()
    logging.debug("The contents of log file are : %s" % contents)
    ret = contents.find(boot_prompt)
    if ret == -1:
        return False
    return True


def run(test, params, env):
    """
    Test pty type serial with log file
    """

    def prepare_serial_device():
        """
        Prepare a serial device XML according to parameters

        :return: the serial device xml object
        """
        serial = librarian.get('serial')(serial_type)

        serial.target_port = target_port
        serial.target_type = target_type
        serial.target_model = target_model
        serial.log_file = log_file

        return serial

    def update_qemu_conf():
        """
        update some settings in qemu conf file
        """
        qemu_conf.stdio_handler = stdio_handler
        daemon_service.restart()

    remove_devices = eval(params.get('remove_devices', []))
    target_model = params.get('target_model', '')
    serial_type = params.get('serial_dev_type', 'pty')
    log_file = params.get('log_file', '')
    target_type = params.get('target_type', 'isa-serial')
    target_port = params.get('target_port', '0')
    target_model = params.get('target_model', '')
    stdio_handler = params.get('stdio_handler', "logd")
    boot_prompt = params.get('boot_prompt', 'Login Prompts')
    vm_name = params.get("main_vm")

    qemu_conf = utils_config.get_conf_obj("qemu")
    daemon_service = utils_libvirtd.Libvirtd()

    update_qemu_conf()

    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    try:
        vmxml = backup_xml.copy()
        vm = env.get_vm(vm_name)
        vm.undefine()

        for device_type in remove_devices:
            vmxml.remove_all_device_by_type(device_type)
        serial_dev = prepare_serial_device()
        vmxml.add_device(serial_dev)
        vmxml.sync()
        logging.debug("vmxml: %s" % vmxml)

        ret = virsh.define(vmxml.xml, debug=True)
        libvirt.check_exit_status(ret)
        virsh.start(vm_name, ignore_status=False)
        vm.wait_for_login().close()

        # Need to wait for a while to get login prompt
        if not utils_misc.wait_for(
                lambda: check_pty_log_file(log_file, boot_prompt), 6):
            test.fail("Failed to find the vm login prompt from %s" % log_file)

    except Exception as e:
        test.error('Unexpected error: {}'.format(e))
    finally:
        vm.destroy()
        backup_xml.sync()
        qemu_conf.restore()
        daemon_service.restart()
