from uuid import uuid4

from virttest.libvirt_xml.vm_xml import VMXML

from provider.vfio import ccw


def run(test, params, env):
    """
    Test for CCW, esp. DASD disk passthrough on s390x.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    devid = params.get("devid")
    schid = None
    uuid = None
    chpids = None

    plugmethod = params.get("plugmethod")

    try:
        ccw.assure_preconditions()

        if vm.is_alive() and plugmethod == "coldplug":
            vm.destroy()

        schid, chpids = ccw.get_device_info(devid)
        uuid = str(uuid4())

        ccw.set_override(schid)
        ccw.start_device(uuid, schid)
        ccw.attach_hostdev(vm_name, uuid)

        if vm.is_dead() and plugmethod == "coldplug":
            vm.start()
        session = vm.wait_for_login()

        if not ccw.read_write_operations_work(session, chpids):
            test.fail("Read/write operation failing inside guest.")

        if vm.is_alive() and plugmethod == "coldplug":
            vm.destroy()

        ccw.detach_hostdev(vm_name, uuid)

    finally:
        if uuid:
            ccw.stop_device(uuid)
        if schid:
            ccw.unset_override(schid)
        backup_xml.sync()
