import os
import logging

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.video import Video


def video_device_setup(vm_name, video_type):
    """
    Setup domain with second video device

    :param vm_name: vm name string
    :param video_type: video type string
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name=vm_name)
    new_video = Video('video')
    new_video.model_type = video_type
    vmxml.add_device(new_video)
    logging.debug("The new domain xml is:\n%s" % vmxml.xmltreefile)
    vmxml.undefine()
    vmxml.define()


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
    screen_num = params.get("screen_number")
    options = params.get("options", "")
    video_type = params.get("video_model_type", "qxl")
    # This is defined only for shipping RHEL environments.
    multiple_screen = "yes" == params.get("multiple_screen", "no")
    status_error = params.get("status_error")
    vm_uuid = vm.get_uuid()
    vm_id = ""
    if screen_num:
        options += "--screen %s" % screen_num
    if filename:
        options += " --file %s" % filename

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    if vm.is_alive():
        vm.destroy()

    # Enable multiple screen on RHEL will run tests with screen_num as 1,
    # disable it will skip tests with screen_num as 1.
    if multiple_screen:
        # Backup of original vm and set second screen
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        video_device_setup(vm_name, video_type)
    else:
        if screen_num == "1":
            test.cancel("Multiple screen is not enabled")

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
                                   unprivileged_user=unprivileged_user,
                                   uri=uri, debug=True)
    status = result.exit_status
    output = result.stdout.strip()

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successful with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command.")

    # Recover state of vm.
    if vm_state == "paused":
        vm.resume()

    # Recover env
    if vm.is_alive():
        vm.destroy()
    if multiple_screen:
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
