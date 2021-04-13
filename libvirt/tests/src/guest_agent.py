import logging
import os
import shutil

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_selinux
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def check_ga_state(vm, vm_name):
    """
    Check the guest agent state from guest xml

    :param vm: The vm to be checked
    :param vm_name: The vm's name
    :return: the guest agent state
    """
    # The session is just to make sure the guest
    # is fully boot up
    vm.wait_for_login().close()
    cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    channels = cur_xml.get_agent_channels()
    for channel in channels:
        state = channel.find('./target').get('state')
    logging.debug("The guest agent state is %s", state)
    agent_status = state == "connected"
    return agent_status


def check_ga_function(vm_name, status_error, hotunplug_ga):
    """
    Check whether guest agent function can work as expected

    :param vm_name: The vm's name
    :param status_error: Expect status error or not
    :param hotunplug_ga: hotunplug guest agent device or not
    """
    error_msg = []
    if status_error:
        error_msg.append("QEMU guest agent is not connected")
    if hotunplug_ga:
        error_msg.append("QEMU guest agent is not configured")
    result = virsh.domtime(vm_name, ignore_status=True, debug=True)
    libvirt.check_result(result, expected_fails=error_msg,
                         any_error=status_error)


def get_ga_xml(vm, vm_name):
    """
    Get the xml snippet of guest agent

    :param vm: The vm to get xml from
    :param vm_name: The vm's name
    :return: the the xml snippet of guest agent
    """
    cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    channels = cur_xml.get_devices('channel')
    ga_xml = None
    for channel in channels:
        target = channel['xmltreefile'].find('./target')
        if target is not None:
            name = target.get('name')
            if name and name.startswith("org.qemu.guest_agent"):
                ga_xml = channel.xml
                break
    return ga_xml


def run(test, params, env):

    vm_name = params.get("main_vm")
    status_error = ("yes" == params.get("status_error", "no"))
    start_ga = ("yes" == params.get("start_ga", "yes"))
    prepare_channel = ("yes" == params.get("prepare_channel", "yes"))
    src_path = params.get("src_path")
    tgt_name = params.get("tgt_name", "org.qemu.guest_agent.0")
    restart_libvirtd = ("yes" == params.get("restart_libvirtd"))
    suspend_resume_guest = ("yes" == params.get("suspend_resume_guest"))
    hotunplug_ga = ("yes" == params.get("hotunplug_ga"))
    label = params.get("con_label")
    vm = env.get_vm(vm_name)

    if src_path:
        socket_file_dir = os.path.dirname(src_path)
        if not os.path.exists(socket_file_dir):
            os.mkdir(socket_file_dir)
        shutil.chown(socket_file_dir, "qemu", "qemu")
        utils_selinux.set_context_of_file(filename=socket_file_dir,
                                          context=label)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    vmxml.remove_agent_channels()
    vmxml.sync()

    try:
        if prepare_channel:
            vm.prepare_guest_agent(start=start_ga, channel=True,
                                   source_path=src_path)

        if restart_libvirtd:
            utils_libvirtd.libvirtd_restart()

        if suspend_resume_guest:
            virsh.suspend(vm_name, debug=True)
            virsh.resume(vm_name, debug=True)

        if hotunplug_ga:
            ga_xml = get_ga_xml(vm, vm_name)
            result = virsh.detach_device(vm_name, ga_xml)
            if result.exit_status:
                test.fail("hotunplug guest agent device failed, %s"
                          % result)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            if vmxml.get_agent_channels():
                test.fail("hotunplug guest agent device failed as "
                          "guest agent xml still exists")
        else:
            if start_ga != check_ga_state(vm, vm_name):
                test.fail("guest agent device is not in correct state")

        check_ga_function(vm_name, status_error, hotunplug_ga)
    finally:
        vm.destroy()
        backup_xml.sync()
