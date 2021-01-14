import os
import logging
import re
import shutil

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_secret
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_embedded_qemu
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test embedded qemu driver:
    1.Start guest by virt-qemu-run;
    2.Start guest with luks disk by virt-qemu-run;
    3.Options test for virt-qemu-run;
    """
    log = []

    def _logger(line):
        """
        Callback function to log embeddedqemu output.
        """
        log.append(line)

    def _check_log():
        """
        Check whether the output meets expectation
        """
        if re.findall(expected_pattern, '\n'.join(log)):
            return True
        else:
            return False

    def _confirm_terminate():
        """
        Confirm qemu process exits successuflly after 'ctrl+c'
        """
        cmd = 'pgrep qemu | wc -l'
        output = int(process.run(cmd, shell=True).stdout_text.strip())
        if output == virt_qemu_run.qemu_pro_num:
            return True
        else:
            return False

    def add_luks_disk(secret_type, sec_encryption_uuid):
        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        disk_xml = Disk(type_name=disk_type)
        disk_xml.device = "disk"
        disk_source = disk_xml.new_disk_source(
                **{"attrs": {'file': img_path}})
        disk_xml.driver = {"name": "qemu", "type": img_format,
                           "cache": "none"}
        disk_xml.target = {"dev": disk_target, "bus": disk_bus}
        encryption_dict = {"encryption": 'luks',
                           "secret": {"type": secret_type,
                                      "uuid": sec_encryption_uuid}}
        disk_source.encryption = disk_xml.new_encryption(**encryption_dict)
        disk_xml.source = disk_source
        logging.debug("disk xml is:\n%s" % disk_xml)
        vmxml.add_device(disk_xml)
        logging.debug("guest xml: %s", vmxml.xml)
        return vmxml

    arg_str = params.get("embedded_arg", "")
    expected_pattern = params.get("logger_pattern", "")
    root_dir = params.get("root_dir", "/var/tmp/virt_qemu_run_dir")
    config_path = params.get('expected_config_path', "")
    terminate_guest = params.get("terminate_guest", "no") == "yes"
    expected_secret = params.get("expected_secret", "no") == "yes"
    no_valuefile = params.get("no_valuefile", "no") == "yes"
    expected_root_dir = params.get("expected_root_dir", "no") == "yes"
    expected_help = params.get("expected_help", "no") == "yes"
    status_error = params.get("status_error", "no") == "yes"

    disk_target = params.get("disk_target", "vdd")
    disk_type = params.get("disk_type", "file")
    disk_bus = params.get("disk_bus", "virtio")
    img_path = params.get("sec_volume", "/var/lib/libvirt/images/luks.img")
    img_format = params.get("img_format", "qcow2")
    img_cap = params.get("img_cap", "1")

    secret_type = params.get("secret_type", "passphrase")
    secret_password = params.get("secret_password", "redhat")
    extra_luks_parameter = params.get("extra_parameter")
    secret_value_file = "/tmp/secret_value"
    secret_file = "/tmp/secret.xml"

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    virt_qemu_run = libvirt_embedded_qemu.EmbeddedQemuSession(
        logging_handler=_logger,
    )

    try:
        if expected_secret:
            libvirt.create_local_disk(disk_type="file", extra=extra_luks_parameter,
                                      path=img_path, size=int(img_cap), disk_format=img_format)
            utils_secret.clean_up_secrets()
            sec_params = {"sec_usage": "volume",
                          "sec_volume": img_path,
                          "sec_desc": "Secret for volume."
                          }
            sec_uuid = libvirt.create_secret(sec_params)
            if sec_uuid:
                try:
                    virsh.secret_dumpxml(sec_uuid, to_file=secret_file)
                except process.CmdError as e:
                    test.error(str(e))

            process.run("echo -n %s > %s" % (secret_password, secret_value_file), shell=True)
            vmxml = add_luks_disk(secret_type, sec_uuid)
            if not no_valuefile:
                arg_str += ' -s %s,%s' % (secret_file, secret_value_file)
            else:
                arg_str += ' -s %s' % secret_file
        if expected_root_dir:
            arg_str += ' -r %s' % (root_dir)
        if not expected_help:
            arg_str += ' %s' % (vmxml.xml)

        logging.debug("Start virt-qemu-run process with options: %s", arg_str)
        virt_qemu_run.start(arg_str=arg_str, wait_for_working="yes")

        if expected_pattern:
            if not utils_misc.wait_for(lambda: _check_log(), 60, 10):
                test.fail('Expected output %s not found in log: \n%s' %
                          (expected_pattern, '\n'.join(log)))

        if log:
            logging.debug("virt-qemu-run log:")
            for line in log:
                logging.debug(line)

        if not expected_help and not status_error:
            if expected_pattern != "domain type":
                expected_pattern = "guest running"
                if not utils_misc.wait_for(lambda: _check_log(), 60, 20):
                    test.fail("Start qemu process unexpected failed")

    finally:
        virt_qemu_run.tail.sendcontrol('c')
        if not utils_misc.wait_for(lambda: _confirm_terminate, 60, 10):
            test.fail("process did not exit successfully")
        virt_qemu_run.exit()
        process.run('pkill -9 qemu-kvm', ignore_status=True, shell=True)
        utils_secret.clean_up_secrets()
        if os.path.exists(secret_value_file):
            os.remove(secret_value_file)
        if os.path.exists(secret_file):
            os.remove(secret_file)
        if os.path.exists(img_path):
            os.remove(img_path)
        if os.path.exists(root_dir):
            shutil.rmtree(root_dir, ignore_errors=False)
