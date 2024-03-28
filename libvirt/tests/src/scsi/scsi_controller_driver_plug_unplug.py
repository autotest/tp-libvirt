#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng<dzheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import time

from virttest import virsh

from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_test import libvirt


def prepare_vm_xml(params, test):
    """
    Prepare vm xml for the test

    :param params: dict, test parameters
    :param test: test object
    """
    iothread_num = params.get('iothread_num')
    iothread_id = params.get('iothread_id')
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    devices = vmxml.get_devices()
    controllers = vmxml.get_devices(device_type='controller')
    for one_device in controllers:
        if one_device.type == 'scsi':
            test.log.debug("Remove the existing scsi controller")
            devices.remove(one_device)
    vmxml.set_devices(devices)
    if iothread_num:
        vmxml.setup_attrs(iothreads=int(iothread_num), iothreadids={'iothread': [{'id': iothread_id}]})
    vmxml.sync()
    libvirt_pcicontr.reset_pci_num(vm_name)
    test.log.debug("The VM xml after preparation:"
                   "\n%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))


def check_dumpxml(vm_name, options, controllers_dicts, expect_exist, test):
    """
    Check vm xml by virsh dumpxml

    :param vm_name: str, vm name
    :param options: str, virsh dumpxml options
    :param controllers_dicts: dict, controller's configurations
    :param expect_exist: boolean, True if expect existence, otherwise False
    :param test: test object
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name, options=options)
    controllers = vmxml.get_devices(device_type="controller")
    for one_ctl_dict in controllers_dicts:
        found = False
        for one_ctl in controllers:
            if (one_ctl.type == one_ctl_dict['type'] and
               one_ctl.model == one_ctl_dict['model'] and
               one_ctl.index == one_ctl_dict['index']):
                found = True
                break
        if found != expect_exist:
            test.fail("Expect controller (%s) %s exist "
                      "in vm xml" % (one_ctl_dict,
                                     '' if expect_exist else 'not'))
        test.log.debug("Verify controller (%s) in VM "
                       "xml - PASS", one_ctl_dict)


def _create_controllers(params):
    """
    Create controllers for the tests

    :param params: dict, test parameters
    :return: tuple, (list of controller objects,
                     list of controller parameters)
    """
    contr_indexes = eval(params.get('contr_index'))
    iothread_id = params.get('iothread_id')
    same_address = eval(params.get('same_address', '{}'))
    controller_dicts = []
    controllers = []
    for an_index in contr_indexes:
        controller_dict = params.get('controller_dict')
        controller_dict = eval(controller_dict % an_index)
        if iothread_id:
            controller_dict['driver'] = {'iothread': '%s' % iothread_id}
        if same_address:
            controller_dict['address'] = {'attrs': same_address}
        ctrl = Controller(type_name=controller_dict['type'])
        ctrl.setup_attrs(**controller_dict)
        controller_dicts.append(controller_dict)
        controllers.append(ctrl)
    return (controllers, controller_dicts)


def test_default(vm_name, params, test):
    """
    The common test function for plug and unplug

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param test: test object
    """
    virsh_dargs = {'ignore_status': False, 'debug': True}
    controllers, controller_dicts = _create_controllers(params)
    for ctrl in controllers:
        test.log.info("Step: Coldplug a controller %s", ctrl)
        virsh.attach_device(vm_name, ctrl.xml, flagstr='--config', **virsh_dargs)
    check_dumpxml(vm_name, '--inactive', controller_dicts, True, test)
    for ctrl in controllers:
        test.log.info("Step: Hotplug a controller %s", ctrl)
        virsh.attach_device(vm_name, ctrl.xml, **virsh_dargs)
    check_dumpxml(vm_name, '', controller_dicts, True, test)
    for ctrl in controllers:
        test.log.info("Step: Coldunplug a controller %s", ctrl)
        virsh.detach_device(vm_name, ctrl.xml, flagstr='--config', **virsh_dargs)
    time.sleep(10)
    check_dumpxml(vm_name, '--inactive', controller_dicts, False, test)
    for ctrl in controllers:
        test.log.info("Step: Hotunplug a controller %s", ctrl)
        virsh.detach_device(vm_name, ctrl.xml, **virsh_dargs)
    time.sleep(10)
    check_dumpxml(vm_name, '', controller_dicts, False, test)


def test_two_contrs_with_driver_hotplug_same_index(vm_name, params, test):
    """
    Test hot plug with two controllers with driver in same index

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param test: test object
    """
    virsh_dargs = {'ignore_status': False, 'debug': True}
    same_index = params.get('same_index')
    params['contr_index'] = '[%s, %s]' % (same_index, same_index)
    controllers, _ = _create_controllers(params)
    test.log.info("Step: Hotplug a controller %s", controllers[0])
    virsh.attach_device(vm_name, controllers[0].xml, **virsh_dargs)
    virsh_dargs.update({'ignore_status': True})
    test.log.info("Step: Hotplug a controller %s", controllers[1])
    ret = virsh.attach_device(vm_name, controllers[1].xml, **virsh_dargs)
    err_msg = params.get('err_msg')
    libvirt.check_result(ret, expected_fails=err_msg)


def test_two_contrs_with_driver_hotplug_same_address(vm_name, params, test):
    """
    Test hot plug with two controllers with driver in same address

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param test: test object
    """
    virsh_dargs = {'ignore_status': False, 'debug': True}
    controllers, _ = _create_controllers(params)
    test.log.info("Step: Hotplug a controller %s", controllers[0])
    virsh.attach_device(vm_name, controllers[0].xml, **virsh_dargs)
    virsh_dargs.update({'ignore_status': True})
    test.log.info("Step: Hotplug a controller %s", controllers[1])
    ret = virsh.attach_device(vm_name, controllers[1].xml, **virsh_dargs)
    err_msg = params.get('err_msg')
    libvirt.check_result(ret, expected_fails=err_msg)


def run(test, params, env):
    """
    Test scsi controller plug unplug operations with different configurations.

    1.Prepare VM xml.
    2.Do plug unplug scsi controller
    3.Check the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    contr_num = params.get("contr_num")
    driver_config = params.get("driver_config")
    test_scenario = params.get("scenarios")
    case_name = "test_%s_%s_%s" % (contr_num, driver_config, test_scenario)
    run_test_case = eval(case_name) if case_name in globals() else test_default

    try:
        test.log.info("Step: Prepare the VM xml")
        prepare_vm_xml(params, test)
        test.log.info("Step: Start the VM")
        vm.start()
        vm.wait_for_login().close()
        run_test_case(vm_name, params, test)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vmxml_backup.sync()
