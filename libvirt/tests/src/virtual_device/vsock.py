import re
import random
import string
import threading
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_test
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.vsock import Vsock


def run(test, params, env):
    """
    Test vsock device:

    1.Edit/start guest with vsock device.
    2.Hotplug/hotunplug vsock device.
    3.Coldplug/Coldunplug vsock device
    """
    def env_setup():
        session = vm.wait_for_login()
        cmd = [
            'rpm -q lsof || yum -y install lsof',
            'rpm -q git || yum -y install git',
            'rpm -q make || yum -y install make',
            'rpm -q gcc && yum -y reinstall gcc || yum -y install gcc',
            'rm -rf /tmp/nc-vsock',
            'git clone %s /tmp/nc-vsock' % git_repo,
            'cd /tmp/nc-vsock/ && make',
        ]
        for i in range(len(cmd)):
            status1 = session.cmd_status(cmd[i])
            logging.debug(status1)
            status2 = process.run(cmd[i], shell=True).exit_status
            logging.debug(status2)
            if (status1 + status2) != 0:
                test.cancel("Failed to install pkg %s" % cmd[i])
        session.close()

    def validate_data_transfer_by_vsock():
        env_setup()

        def _vsock_server():
            session.cmd("/tmp/nc-vsock/nc-vsock -l 1234 > /tmp/testfile")

        server = threading.Thread(target=_vsock_server)
        session = vm.wait_for_login()
        server.start()
        process.run('echo "test file" > /tmp/testfile', shell=True)
        output = process.run("/tmp/nc-vsock/nc-vsock %d 1234 < /tmp/testfile" % int(cid), shell=True).stdout_text
        logging.debug(output)
        server.join(5)
        data_host = process.run("sha256sum /tmp/testfile", shell=True).stdout_text
        logging.debug(session.cmd_status_output("cat /tmp/testfile")[1])
        data_guest = session.cmd_status_output("sha256sum /tmp/testfile")[1]
        logging.debug(data_guest[1])
        if data_guest != data_host:
            test.fail("Data transfer error with vsock device\n")
        session.close()

    def managedsave_restore():
        result = virsh.managedsave(vm_name, debug=True)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        result = virsh.start(vm_name)
        utils_test.libvirt.check_exit_status(result, expect_error=False)

    start_vm = params.get("start_vm", "no")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    auto_cid = params.get("auto_cid", "no")
    status_error = params.get("status_error", "no") == "yes"
    edit_xml = params.get("edit_xml", "no") == "yes"
    option = params.get("option", "")
    git_repo = params.get("git_repo", "")
    invalid_cid = params.get("invalid_cid", "no") == "yes"
    managedsave = params.get("managedsave", "no") == "yes"
    no_vsock = params.get("no_vsock", "no") == "yes"
    vsock_num = params.get("num")
    commuication = params.get("communication", "no") == "yes"

    # Backup xml file
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Add vsock device to domain
    vmxml.remove_all_device_by_type('vsock')
    vsock_dev = Vsock()
    vsock_dev.model_type = "virtio"
    if process.run("modprobe vhost_vsock").exit_status != 0:
        test.fail("Failed to load vhost_vsock module")
    if invalid_cid:
        cid = "-1"
    else:
        cid = random.randint(3, 10)
        vsock_dev.cid = {'auto': auto_cid, 'address': cid}
        chars = string.ascii_letters + string.digits + '-_'
        alias_name = 'ua-' + ''.join(random.choice(chars) for _ in list(range(64)))
        vsock_dev.alias = {'name': alias_name}
    logging.debug(vsock_dev)
    if start_vm == "no" and vm.is_alive():
        virsh.destroy()
    try:
        if edit_xml:
            edit_status1 = libvirt.exec_virsh_edit(
                vm_name, [(r":/<devices>/s/$/%s" %
                           re.findall(r"<vsock.*<\/vsock>",
                                      str(vsock_dev), re.M
                                      )[0].replace("/", "\/"))])
            edit_status2 = True
            if vsock_num == 2:
                edit_status2 = libvirt.exec_virsh_edit(
                    vm_name, [(r":/<devices>/s/$/%s" %
                               re.findall(r"<vsock.*<\/vsock>",
                                          str(vsock_dev), re.M
                                          )[0].replace("/", "\/"))])
                logging.debug(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

            if status_error:
                if edit_status1 or edit_status2:
                    test.fail("virsh edit should fail\n")
            else:
                if not edit_status1:
                    test.fail("Failed to edit vm xml with vsock device\n")
                else:
                    result = virsh.start(vm_name, debug=True)
                    utils_test.libvirt.check_exit_status(result, expect_error=False)
        else:
            session = vm.wait_for_login()
            session.close()
            result = virsh.attach_device(vm_name, file_opt=vsock_dev.xml, flagstr=option, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
            if option == "--config":
                result = virsh.start(vm_name, debug=True)
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug(vmxml)
            vsock_list = vmxml.devices.by_device_tag("vsock")
            cid = vsock_list[0].cid['address']
            if 0 == len(vsock_list):
                test.fail("No vsock device found in live xml\n")
            if commuication:
                validate_data_transfer_by_vsock()
            if managedsave and not no_vsock:
                managedsave_restore()

            def _detach_completed():
                status = process.run("lsof /dev/vhost-vsock", ignore_status=True, shell=True).exit_status
                return status == 1

            result = virsh.detach_device(vm_name, file_opt=vsock_dev.xml, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
            utils_misc.wait_for(_detach_completed, timeout=20)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            vsock_list = vmxml.get_devices("vsock")
            if vsock_list:
                test.fail("Still find vsock device in live xml after hotunplug\n")
            if managedsave and no_vsock:
                managedsave_restore()
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
