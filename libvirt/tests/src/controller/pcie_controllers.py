import os
import re
import time
import logging

from virttest import virsh
from virttest import data_dir

from virttest.utils_libvirtd import Libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_pcicontr


def run(test, params, env):
    """
    Test the PCIe controllers' options
    1. Backup guest xml before the tests
    2. Modify guest xml and define the guest
    3. Start guest
    4. Hotplug if needed
    5. Do checking
    6. Destroy guest and restore guest
    """

    def get_disk_bus(disk_dev=None):
        """
        Get the bus list of guest disks

        :param disk_dev: The specified disk device
        :return: list for disks' buses
        """
        disk_bus_list = []

        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
        disk_dev_list = cur_vm_xml.get_disk_blk(vm_name)
        if disk_dev and disk_dev not in disk_dev_list:
            return disk_bus_list
        for disk_index in range(0, len(disk_dev_list)):
            disk_target = disk_dev if disk_dev else disk_dev_list[disk_index]
            disk_bus = cur_vm_xml.get_disk_attr(vm_name, disk_target, 'address', 'bus')
            disk_bus_list.append(disk_bus)
            if disk_dev:
                break
        logging.debug("Return disk bus list: {}".format(disk_bus_list))
        return disk_bus_list

    def check_guest_disks(ishotplug):
        """
        Check guest disks in different ways

        :param ishotplug: True for hotplug, False for hotunplug
        :raise: test.fail if some errors happen
        """
        def _find_disk_by_cmd():
            """
            Check disk using virsh command

            :return: True if the disk is found, otherwise False
            """
            ret = virsh.domblklist(vm_name, **virsh_options)
            target_disks = re.findall(r"[v,s]d[a-z]", ret.stdout.strip())
            logging.debug(target_disks)

            for one_disk in target_disks:
                if target_dev in one_disk:
                    logging.debug("Found the disk '{}'".format(target_dev))
                    return True
            logging.debug("Can't find the disk '{}'".format(target_dev))
            return False

        def _find_disk_in_xml():
            """
            Check disk in guest xml

            :return: True if the disk is found with right bus
                     False if the disk is not found
            :raise: test.fail if the disk's bus is incorrect
            """
            bus_list = get_disk_bus(target_dev)
            if len(bus_list) == 0:
                return False
            if bus_list[0] != '0x%02x' % int(contr_index):
                test.fail("The found disk's bus is expected to be {}, "
                          "but {} found".format('0x%02x' % int(contr_index),
                                                bus_list[0]))
            return True

        virsh_options.update({'ignore_status': False})
        # Firstly check virsh.domblklist
        found_by_cmd = _find_disk_by_cmd()
        found_in_xml = _find_disk_in_xml()
        msg1 = "Can't find the device with target_dev '{}' by cmd".format(target_dev)
        msg2 = "Found the device with target_dev '{}' unexpectedly by cmd".format(target_dev)
        msg3 = "The device with target_dev '{}' was not detached successfully in xml".format(target_dev)
        msg4 = "The device with target_dev '{}' was detached unexpectedly in xml".format(target_dev)
        if ((ishotplug and not status_error and not found_by_cmd) or
           (not ishotplug and status_error and not found_by_cmd)):
            test.fail(msg1)
        if ((ishotplug and status_error and found_by_cmd) or
           (not ishotplug and not status_error and found_by_cmd)):
            test.fail(msg2)
        if ((ishotplug and not status_error and not found_in_xml) or
           (not ishotplug and not status_error and found_in_xml)):
            test.fail(msg3)
        if ((ishotplug and status_error and found_in_xml) or
           (not ishotplug and status_error and not found_in_xml)):
            test.fail(msg4)

    def check_inside_guest(ishotplug):
        """
        Check devices within the guest

        :param ishotplug: True for hotplug, False for hotunplug
        :raise: test.fail if the result is not expected
        """
        def _check_disk_in_guest():
            """
            Compare the disk numbers within the guest

            :return: True if new disk is found, otherwise False
            """
            new_disk_num = len(vm.get_disks())
            if new_disk_num > ori_disk_num:
                logging.debug("New disk is found in vm")
                return True
            logging.debug("New disk is not found in vm")
            return False

        vm_session = vm.wait_for_login()
        status = _check_disk_in_guest()
        vm_session.close()
        msg1 = "Can't find the device in the guest"
        msg2 = "Found the device in the guest unexpectedly"
        if ((ishotplug and not status_error and not status) or
                (not ishotplug and status_error and not status)):
            test.fail(msg1)
        if ((ishotplug and status_error and status) or
                (not ishotplug and not status_error and status)):
            test.fail(msg2)

    def check_guest_contr():
        """
        Check the controller in guest xml

        :raise: test.fail if the controller does not meet the expectation
        """
        cntl = None
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
        for cntl in cur_vm_xml.devices.by_device_tag('controller'):
            if (cntl.type == 'pci' and
               cntl.model == contr_model and
               cntl.index == contr_index):
                logging.debug(cntl.target)
                cntl_hotplug = cntl.target.get('hotplug')
                logging.debug("Got controller's hotplug:%s", cntl_hotplug)
                if cntl_hotplug != hotplug_option:
                    test.fail("The controller's hotplug option is {}, "
                              "but expect {}".format(cntl_hotplug,
                                                     hotplug_option))
                break
        if not cntl:
            test.fail("The controller with index {} is not found".format(contr_index))

    def check_multi_attach(bus_list):
        """
        Check the result of multiple attach devices to the VM

        :param bus_list: List which includes the buses of vm disks
        :raise: test.fail if the result is unexpected
        """
        msg_pattern = "The disk is {} expected to be attached to " \
                      "the controller with index '{}'"
        is_found = False
        if hotplug_option == 'on':
            for one_bus in bus_list:
                is_found = is_found | (one_bus == '0x%02x' % int(contr_index))
            if not is_found:
                test.fail(msg_pattern.format('', contr_index))
            else:
                logging.debug("Found a disk attached to the controller "
                              "with index '{}".format(contr_index))
        else:
            for one_bus in bus_list:
                is_found = one_bus == '0x%02x' % int(contr_index)
                if is_found:
                    test.fail(msg_pattern.format('not', contr_index))
            logging.debug("No disk is found to attach to the "
                          "controller with index '{}'".format(contr_index))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    setup_controller = params.get("setup_controller", 'yes') == 'yes'
    check_within_guest = params.get("check_within_guest", 'yes') == 'yes'
    check_disk_xml = params.get("check_disk_xml", 'no') == 'yes'
    check_cntl_xml = params.get("check_cntl_xml", 'no') == 'yes'
    contr_model = params.get("controller_model", 'pcie-root-port')
    contr_target = params.get("controller_target")
    hotplug_option = params.get("hotplug_option")
    hotplug = params.get("hotplug", 'yes') == 'yes'
    define_option = params.get("define_option")
    attach_extra = params.get("attach_extra")
    target_dev = params.get("target_dev")
    err_msg = params.get("err_msg")
    status_error = params.get("status_error", "no") == 'yes'
    restart_daemon = params.get("restart_daemon", "no") == 'yes'
    save_restore = params.get("save_restore", "no") == 'yes'
    hotplug_counts = params.get("hotplug_counts")
    addr_twice = params.get("addr_twice", 'no') == 'yes'
    contr_index = None

    virsh_options = {'debug': True, 'ignore_status': False}

    image_path_list = []
    vm = env.get_vm(vm_name)
    vm_xml_obj = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml_obj.copy()
    try:
        if check_within_guest:
            if not vm.is_alive():
                virsh.start(vm_name, **virsh_options)
            ori_disk_num = len(vm.get_disks())
            logging.debug("The original disk number in vm is %d", ori_disk_num)
            virsh.destroy(vm_name)

        if setup_controller:
            contr_dict = {'controller_type': 'pci',
                          'controller_model': contr_model,
                          'controller_target': contr_target}
            contr_obj = libvirt.create_controller_xml(contr_dict)
            vm_xml_obj.add_device(contr_obj)
            logging.debug("Add a controller: %s" % contr_obj)

        virsh.define(vm_xml_obj.xml, options=define_option, **virsh_options)
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        ret_indexes = libvirt_pcicontr.get_max_contr_indexes(vm_xml,
                                                             'pci',
                                                             contr_model)
        if not ret_indexes or len(ret_indexes) < 1:
            test.error("Can't find the controller index for model "
                       "'{}'".format(contr_model))
        contr_index = ret_indexes[0]
        if attach_extra and attach_extra.count('--address '):
            attach_extra = attach_extra % ("%02x" % int(contr_index))
        if err_msg and err_msg.count('%s'):
            err_msg = err_msg % contr_index
        if not save_restore:
            disk_max = int(hotplug_counts) if hotplug_counts else 1
            for disk_inx in range(0, disk_max):
                image_path = os.path.join(data_dir.get_tmp_dir(),
                                          'disk{}.qcow2'.format(disk_inx))
                image_path_list.append(image_path)
                libvirt.create_local_disk("file", image_path, '10M',
                                          disk_format='qcow2')
        if not hotplug and not save_restore:
            # Do coldplug before hotunplug to prepare the virtual device
            virsh.attach_disk(vm_name, image_path, target_dev,
                              extra=attach_extra,
                              **virsh_options)
        virsh.start(vm_name, **virsh_options)

        logging.debug("Test VM XML after starting:"
                      "\n%s", VMXML.new_from_dumpxml(vm_name))
        vm.wait_for_login().close()

        if restart_daemon:
            daemon_obj = Libvirtd()
            daemon_obj.restart()

        if save_restore:
            save_path = os.path.join(data_dir.get_tmp_dir(), 'rhel.save')
            virsh.save(vm_name, save_path, **virsh_options)
            time.sleep(10)
            virsh.restore(save_path, **virsh_options)
        # Create virtual device xml
        if hotplug:
            virsh_options.update({'ignore_status': True})
            attach_times = 1 if not hotplug_counts else int(hotplug_counts)

            if attach_times == 1:
                ret = virsh.attach_disk(vm_name, image_path_list[0], target_dev,
                                        extra=attach_extra,
                                        **virsh_options)
                libvirt.check_result(ret, expected_fails=err_msg)
            else:
                for attach_inx in range(0, attach_times):
                    disk_dev = 'vd{}'.format(chr(98 + attach_inx))
                    ret = virsh.attach_disk(vm_name, image_path_list[attach_inx], disk_dev,
                                            extra=attach_extra,
                                            **virsh_options)
                    if ret.exit_status and not addr_twice:
                        break
                libvirt.check_result(ret, expected_fails=err_msg)
        if not hotplug and check_within_guest:
            virsh_options.update({'ignore_status': True})
            ret = virsh.detach_disk(vm_name, target_dev, **virsh_options)
            libvirt.check_result(ret, expected_fails=err_msg)
        logging.debug(VMXML.new_from_dumpxml(vm_name))
        if check_disk_xml:
            time.sleep(5)
            check_guest_disks(hotplug)
        if check_cntl_xml:
            check_guest_contr()
        if hotplug_counts and not addr_twice:
            check_multi_attach(get_disk_bus())
        if check_within_guest:
            check_inside_guest(hotplug)

    finally:
        vm_xml_backup.sync()
