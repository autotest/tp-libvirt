import os
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest import element_tree as ElementTree


SOURCE_LIST = ['file', 'dev', 'dir', 'name']


def get_disk_info(vm_name, options):
    """
    Return disk info dict.

    :param vm_name: vm name
    :param options: domblkinfo command options
    :return: dict of disk info
    """
    disk_info_dict = {}
    if "--inactive" in options:
        option = "--inactive"
    else:
        option = ""
    sourcelist = vm_xml.VMXML.get_disk_source(vm_name, option)
    new_disk = disk.Disk()

    for i in range(len(sourcelist)):
        new_disk['xml'] = ElementTree.tostring(sourcelist[i])
        logging.debug("Current disk xml is: %s" % new_disk.xmltreefile)
        for key in new_disk.source.attrs.keys():
            if key in SOURCE_LIST:
                source_path = new_disk.source.attrs[key]
        disk_info_dict[i] = [new_disk.type_name, new_disk.device,
                             new_disk.target['dev'],
                             source_path]

    return disk_info_dict


def run(test, params, env):
    """
    Test command: virsh domblklist.
    1.Prepare test environment.
    2.Run domblklist and check
    3.Do attach disk and rerun domblklist with check
    4.Clean test environment.
    """

    def domblklist_test():
        """
        Run domblklist and check result, raise error if check fail.
        """
        output_disk_info = {}
        result = virsh.domblklist(vm_ref, options,
                                  ignore_status=True, debug=True)
        status = result.exit_status
        output = result.stdout.strip()

        # Check status_error
        if status_error == "yes":
            if status == 0:
                raise error.TestFail("Run successfully with wrong command!")
        elif status_error == "no":
            if status == 1:
                raise error.TestFail("Run failed with right command")
            # Check disk information.
            disk_info = get_disk_info(vm_name, options)
            logging.debug("The disk info dict from xml is: %s" % disk_info)

            output_list = output.split('\n')
            for i in range(2, len(output_list)):
                output_disk_info[i-2] = output_list[i].split()
            logging.debug("The disk info dict from command output is: %s"
                          % output_disk_info)

            if "--details" in options:
                if disk_info != output_disk_info:
                    raise error.TestFail("The output did not match with disk"
                                         " info from xml")
            else:
                for i in range(len(disk_info.keys())):
                    disk_info[i] = disk_info[i][2:]
                if disk_info != output_disk_info:
                    raise error.TestFail("The output did not match with disk"
                                         " info from xml")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Get all parameters from configuration.
    vm_ref = params.get("domblklist_vm_ref")
    options = params.get("domblklist_options", "")
    status_error = params.get("status_error", "no")
    front_dev = params.get("domblkinfo_front_dev", "vdd")
    test_attach_disk = os.path.join(test.virtdir, "tmp.img")
    extra = ""

    domid = vm.get_id()
    domuuid = vm.get_uuid()
    vm_state = vm.state()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref == "uuid":
        vm_ref = domuuid

    # run domblklist and check
    domblklist_test()

    if status_error == "no":
        try:
            # attach disk and check
            source_file = open(test_attach_disk, 'wb')
            source_file.seek((512 * 1024 * 1024) - 1)
            source_file.write(str(0))
            source_file.close()
            # since bug 1049529, --config will work with detach when
            # domain is running, so change it back using --config here
            if "--inactive" in options or vm_state == "shut off":
                extra = "--config"
            virsh.attach_disk(vm_name, test_attach_disk, front_dev, extra,
                              debug=True)
            domblklist_test()
        finally:
            virsh.detach_disk(vm_name, front_dev, extra, debug=True)
            if os.path.exists(test_attach_disk):
                os.remove(test_attach_disk)
