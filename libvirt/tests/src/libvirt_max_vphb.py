import logging
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    1) get params from cfg file
    2) create a controller with index equal to 1
    3) add controller to xml
    4) start the Vm
       Vm should start with index 1 to 31, for index 32 it should not.
    5) shutdown the Vm
    6) repeat the steps  2 to 5, but each time
       increase the index value in step2
    """
    # get the params from params
    vm_name = params.get("main_vm")
    max_pci = int(params.get("max_pci", "32"))
    model = params.get("model", "ENTER_MODEL")
    if model.count("ENTER_MODEL"):
        test.cancel("Please enter model.")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    try:
        for index in range(1, max_pci+1):
            controller = Controller("controller")
            controller.type = "pci"
            controller.index = index
            controller.model = model
            devices.append(controller)
            try:
                vmxml.set_devices(devices)
                vmxml.sync()
            except Exception as e:
                if index < max_pci:
                    test.fail("Vm fail to define with controller index %s"
                              % index)
                else:
                    logging.debug("PPC supports maximum 32 vphbs(0 to 31)")
                continue
            ret = virsh.start(vm_name, ignore_status=True)
            if not ret.exit_status:
                logging.debug("Vm start with controller index %s" % index)
            elif ret.exit_status:
                test.fail("Vm failed to start with controller index number %s"
                          % index)
            virsh.shutdown(vm_name)
    finally:
        backup_xml.sync()
