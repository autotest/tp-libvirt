import os
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.video import Video


def run(test, params, env):
    """
    Test command: virsh screenshot.

    1) Add second video device to domain.
    2) Run test for virsh screenshot.
    3) Recover env
    """
    # Get parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_ref = params.get("vm_ref", "domname")
    vm_state = params.get("vm_state", "running")
    filename = params.get("filename", "")
    options = params.get("options", "")
    video_type = params.get("video_model_type", "qxl")
    status_error = params.get("status_error")
    vm_uuid = vm.get_uuid()
    vm_id = ""
    if filename:
        options += " --file %s" % filename

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if vm.is_alive():
        vm.destroy()

    # Config vm for second video device
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name=vm_name)
    new_video = Video('video')
    new_video.model_type = video_type
    vmxml.add_device(new_video)
    logging.debug("The new domain xml is:\n%s" % vmxml.xmltreefile)
    vmxml.undefine()
    vmxml.define()

    # Prepare vm state for test
    if vm_state != "shutoff":
        vm.start()
        vm.wait_for_login()
        vm_id = vm.get_id()
    if vm_state == "paused":
        vm.pause()

    # Prepare options
    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domuuid":
        vm_ref = vm_uuid
    elif vm_ref == "domid":
        vm_ref = vm_id
    elif vm_id and vm_ref == "hex_id":
        vm_ref = hex(int(vm_id))
    elif 'invalid' in vm_ref:
        if params.get('invalid_id'):
            vm_ref = params.get('invalid_id')
        elif params.get('invalid_uuid'):
            vm_ref = params.get('invalid_uuid')

    # Run test command
    result = virsh.screenshot_test(vm_ref, options, ignore_status=True,
                                   debug=True)
    status = result.exit_status
    output = result.stdout.strip()

    # check status_error
    if status_error == "yes":
        if status == 0:
            raise error.TestFail("Run successful with wrong command!")
    elif status_error == "no":
        if status != 0:
            raise error.TestFail("Run failed with right command.")

    # Recover state of vm.
    if vm_state == "paused":
        vm.resume()

    # Recover env
    if vm.is_alive():
        vm.destroy()
    vmxml_backup.undefine()
    vmxml_backup.define()
    if os.path.exists(filename):
        os.remove(filename)
    if not filename:
        # get screenshot filename from output
        scr_file = output.split(',')[0].split(' ')[-1]
        cwd = os.getcwd()
        file_path = "%s/%s" % (cwd, scr_file)
        logging.debug("Screenshot file path is: %s" % file_path)
        if os.path.exists(file_path):
            os.remove(file_path)
