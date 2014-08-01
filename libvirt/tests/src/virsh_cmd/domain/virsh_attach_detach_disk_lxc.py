import logging
from autotest.client.shared import error
from autotest.client.shared import utils
from virttest import virsh
from virttest import utils_test
from virttest import qemu_storage
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.staging.service import Factory


def run(test, params, env):
    """
    Test virsh {at|de}tach-disk command for lxc.

    The command can attach new disk/detach disk.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh attach/detach-disk operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_ref = params.get("at_dt_disk_vm_ref", "name")
    at_options = params.get("at_dt_disk_at_options", "")
    dt_options = params.get("at_dt_disk_dt_options", "")
    pre_vm_state = params.get("at_dt_disk_pre_vm_state", "running")
    status_error = "yes" == params.get("status_error", 'no')
    no_attach = params.get("at_dt_disk_no_attach", 'no')

    # Get test command.
    test_cmd = params.get("at_dt_disk_test_cmd", "attach-disk")

    # Disk specific attributes.
    device_source = params.get("at_dt_disk_device_source", "/dev/sdc1")
    device_target = params.get("at_dt_disk_device_target", "vdd")
    test_twice = "yes" == params.get("at_dt_disk_test_twice", "no")
    test_audit = "yes" == params.get("at_dt_disk_check_audit", "no")
    serial = params.get("at_dt_disk_serial", "")
    address = params.get("at_dt_disk_address", "")
    address2 = params.get("at_dt_disk_address2", "")
    if serial:
        at_options += (" --serial %s" % serial)
    if address2:
        at_options_twice = at_options + (" --address %s" % address2)
    if address:
        at_options += (" --address %s" % address)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if vm.is_alive():
        vm.destroy(gracefully=False)
    # Back up xml file.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Create virtual device file if user doesn't prepare a partition.
    test_block_dev = False
    if device_source.count("ENTER"):
        try:
            iscsi_dev = qemu_storage.Iscsidev(params, test.virtdir, "iscsi")
            device_source = iscsi_dev.setup()
            logging.debug("iscsi dev name: %s" % device_source)
            test_block_dev = True
        except error.TestError:
            # We should skip this case
            raise error.TestNAError("Can not get iscsi device name in host")

    if vm.is_alive():
        vm.destroy(gracefully=False)

    # if we are testing audit, we need to start audit servcie first.
    if test_audit:
        auditd_service = Factory.create_service("auditd")
        if not auditd_service.status():
            auditd_service.start()
        logging.info("Auditd service status: %s" % auditd_service.status())

    # If we are testing detach-disk, we need to attach certain device first.
    if test_cmd == "detach-disk" and no_attach != "yes":
        s_attach = virsh.attach_disk(vm_name, device_source, device_target,
                                     "--config").exit_status
        if s_attach != 0:
            logging.error("Attaching device failed before testing detach-disk")

        if test_twice:
            device_target2 = params.get("at_dt_disk_device_target2",
                                        device_target)
            s_attach = virsh.attach_disk(vm_name, device_source,
                                         device_target2,
                                         "--config").exit_status
            if s_attach != 0:
                logging.error("Attaching device failed before testing "
                              "detach-disk test_twice")

    vm.start()

    # Turn VM into certain state.
    if pre_vm_state == "paused":
        logging.info("Suspending %s..." % vm_name)
        if vm.is_alive():
            vm.pause()
    elif pre_vm_state == "shut off":
        logging.info("Shuting down %s..." % vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)

    # Get disk count before test.
    disk_count_before_cmd = vm_xml.VMXML.get_disk_count(vm_name)

    # Test.
    domid = vm.get_id()
    domuuid = vm.get_uuid()

    # Confirm how to reference a VM.
    if vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    else:
        vm_ref = ""

    if test_cmd == "attach-disk":
        status = virsh.attach_disk(vm_ref, device_source, device_target,
                                   at_options, debug=True).exit_status
    elif test_cmd == "detach-disk":
        status = virsh.detach_disk(vm_ref, device_target, dt_options,
                                   debug=True).exit_status
    if test_twice:
        device_target2 = params.get("at_dt_disk_device_target2", device_target)
        if test_cmd == "attach-disk":
            if address2:
                at_options = at_options_twice
            status = virsh.attach_disk(vm_ref, device_source,
                                       device_target2, at_options,
                                       debug=True).exit_status
        elif test_cmd == "detach-disk":
            status = virsh.detach_disk(vm_ref, device_target2, dt_options,
                                       debug=True).exit_status

    # Resume guest after command. On newer libvirt this is fixed as it has
    # been a bug. The change in xml file is done after the guest is resumed.
    if pre_vm_state == "paused":
        vm.resume()

    # Check audit log
    check_audit_after_cmd = True
    if test_audit:
        grep_audit = ('grep "%s" /var/log/audit/audit.log'
                      % test_cmd.split("-")[0])
        cmd = (grep_audit + ' | ' + 'grep "%s" | tail -n1 | grep "res=success"'
               % device_source)
        if utils.run(cmd).exit_status:
            logging.error("Audit check failed")
            check_audit_after_cmd = False

    # Check disk count after command.
    check_count_after_cmd = True
    disk_count_after_cmd = vm_xml.VMXML.get_disk_count(vm_name)
    if test_cmd == "attach-disk":
        if disk_count_after_cmd == disk_count_before_cmd:
            check_count_after_cmd = False
    elif test_cmd == "detach-disk":
        if disk_count_after_cmd < disk_count_before_cmd:
            check_count_after_cmd = False

    # Recover VM state.
    if pre_vm_state == "shut off":
        vm.start()

    # Check disk type after attach.
    check_disk_type = True
    try:
        check_disk_type = vm_xml.VMXML.check_disk_type(vm_name,
                                                       device_source,
                                                       "block")
    except xcepts.LibvirtXMLError:
        # No disk found
        check_disk_type = False

    # Check disk serial after attach.
    check_disk_serial = True
    if serial:
        disk_serial = vm_xml.VMXML.get_disk_serial(vm_name, device_target)
        if serial != disk_serial:
            check_disk_serial = False

    # Check disk address after attach.
    check_disk_address = True
    if address:
        disk_address = vm_xml.VMXML.get_disk_address(vm_name, device_target)
        if utils_test.canonicalize_disk_address(address) !=\
           utils_test.canonicalize_disk_address(disk_address):
            check_disk_address = False

    # Check multifunction address after attach.
    check_disk_address2 = True
    if address2:
        disk_address2 = vm_xml.VMXML.get_disk_address(vm_name, device_target2)
        if utils_test.canonicalize_disk_address(address2) !=\
           utils_test.canonicalize_disk_address(disk_address2):
            check_disk_address2 = False

    # Destroy VM.
    vm.destroy(gracefully=False)

    # Check disk count after VM shutdown (with --config).
    check_count_after_shutdown = True
    disk_count_after_shutdown = vm_xml.VMXML.get_disk_count(vm_name)
    if test_cmd == "attach-disk":
        if disk_count_after_shutdown == disk_count_before_cmd:
            check_count_after_shutdown = False
    elif test_cmd == "detach-disk":
        if disk_count_after_shutdown < disk_count_before_cmd:
            check_count_after_shutdown = False

    # Recover VM.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    backup_xml.sync()
    if test_block_dev:
        iscsi_dev.cleanup()

    # Check results.
    if status_error:
        if not status:
            raise error.TestFail("virsh %s exit with unexpected value."
                                 % test_cmd)
    else:
        if status:
            raise error.TestFail("virsh %s failed." % test_cmd)
        if test_cmd == "attach-disk":
            if at_options.count("config"):
                if not check_count_after_shutdown:
                    raise error.TestFail("Cannot see config attached device "
                                         "in xml file after VM shutdown.")
                if not check_disk_serial:
                    raise error.TestFail("Serial set failed after attach")
                if not check_disk_address:
                    raise error.TestFail("Address set failed after attach")
                if not check_disk_address2:
                    raise error.TestFail("Address(multifunction) set failed"
                                         " after attach")
            else:
                if not check_count_after_cmd:
                    raise error.TestFail("Cannot see device in xml file"
                                         " after attach.")
                if not check_disk_type:
                    raise error.TestFail("Check disk type failed after"
                                         " attach.")
                if not check_audit_after_cmd:
                    raise error.TestFail("Audit hotplug failure after attach")
                if at_options.count("persistent"):
                    if not check_count_after_shutdown:
                        raise error.TestFail("Cannot see device attached "
                                             "with persistent after "
                                             "VM shutdown.")
                else:
                    if check_count_after_shutdown:
                        raise error.TestFail("See non-config attached device "
                                             "in xml file after VM shutdown.")
        elif test_cmd == "detach-disk":
            if dt_options.count("config"):
                if check_count_after_shutdown:
                    raise error.TestFail("See config detached device in "
                                         "xml file after VM shutdown.")
            else:
                if check_count_after_cmd:
                    raise error.TestFail("See device in xml file "
                                         "after detach.")
                if not check_audit_after_cmd:
                    raise error.TestFail("Audit hotunplug failure "
                                         "after detach")

                if dt_options.count("persistent"):
                    if check_count_after_shutdown:
                        raise error.TestFail("See device deattached "
                                             "with persistent after "
                                             "VM shutdown.")
                else:
                    if not check_count_after_shutdown:
                        raise error.TestFail("See non-config detached "
                                             "device in xml file after "
                                             "VM shutdown.")

        else:
            raise error.TestError("Unknown command %s." % test_cmd)
