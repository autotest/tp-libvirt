from virttest import virsh
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test guest agent's lifecycle
    """
    @virsh.EventTracker.wait_event
    def prepare_guest_agent(name, wait_for_event=True,
                            event_type='agent-lifecycle.*connected',
                            event_timeout=10, **dargs):
        """
        Check events when preparing guest agent

        :param name: Name of domain.
        :param options: options to pass to reboot command
        :param wait_for_event: wait until an event of the given type comes
        :param event_type: type of the event
        :param event_timeout: timeout for virsh event command
        :param dargs: standardized function keywords
        """
        vm.prepare_guest_agent(**dargs)

    @virsh.EventTracker.wait_event
    def set_ga_status(name, wait_for_event=True,
                      event_type='agent-lifecycle.*connected',
                      event_timeout=10, **dargs):
        """
        Check events when setting guest agent status

        :param name: Name of domain.
        :param options: options to pass to reboot command
        :param wait_for_event: wait until an event of the given type comes
        :param event_type: type of the event
        :param event_timeout: timeout for virsh event command
        :param dargs: standardized function keywords
        """
        vm.set_state_guest_agent(**dargs)

    def test_events():
        """
        Check guest agent's lifecycle events

        1. Prepare a guest with guest agent
        2. Stop the guest agent service
        3. Start the guest agent service inside the guest
        4. Reboot the guest
        5. Shutdown the guest
        """
        prepare_guest_agent(vm_name, with_pm_utils=False)
        set_ga_status(vm_name, event_type='agent-lifecycle.*disconnected',
                      start=False)
        set_ga_status(vm_name, start=True)

        virsh.reboot(vm.name, options="--mode agent", wait_for_event=True,
                     event_type="disconnected.*\n.*reboot.*(\n.*)*connected",
                     debug=True, ignore_status=False)
        virsh.shutdown(
            vm.name, options="--mode agent", wait_for_event=True,
            event_type="disconnected.*(\n.*)*Shutdown Finished.*(\n.*)*Stopped",
            debug=True, ignore_status=False)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        run_test()

    finally:
        vm.destroy()
        backup_xml.sync()
