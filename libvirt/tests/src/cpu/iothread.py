import logging
import re
import os

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest import utils_misc
from virttest import data_dir

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError

ORG_IOTHREAD_POOL = {}
UPDATE_IOTHREAD_POOL = {}


def run(test, params, env):
    """
    Test iothreads related tests

    1) configuration tests for iothreadids/iothreads/iothreadpin/iothreadsched
    2) check for iothreadadd/del/pin operation
    3) check for iothread with disk attached
    4) set and check iothread parameters when vm is running
    5) configure iothread_quota/iothread_period for vm
       without defining iothreads

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def update_iothread_xml(define_error=False):
        """
        Update xml for test
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        del vmxml.cputune
        del vmxml.iothreadids
        del vmxml.iothreads

        vm_is_active = vm.is_alive()

        # Set iothreads first
        if iothread_ids:
            ids_xml = vm_xml.VMIothreadidsXML()
            ids_xml.iothread = iothread_ids.split()
            vmxml.iothreadids = ids_xml
        # Set cputune
        if any([iothreadpins, iothreadscheds, iothread_quota, iothread_period]):
            cputune_xml = vm_xml.VMCPUTuneXML()
            if iothreadpins:
                io_pins = []
                for pins in iothreadpins.split():
                    thread, cpuset = pins.split(':')
                    io_pins.append({"iothread": thread,
                                    "cpuset": cpuset})
                cputune_xml.iothreadpins = io_pins
            if iothreadscheds:
                io_scheds = []
                for sched in iothreadscheds.split():
                    thread, scheduler = sched.split(":")
                    io_scheds.append({"iothreads": thread,
                                      "scheduler": scheduler})
                cputune_xml.iothreadscheds = io_scheds
            if iothread_period:
                cputune_xml.iothread_period = int(iothread_period)
            if iothread_quota:
                cputune_xml.iothread_quota = int(iothread_quota)

            vmxml.cputune = cputune_xml

        # Set iothread
        if iothread_num:
            vmxml.iothreads = int(iothread_num)

        logging.debug("Pre-test xml is %s", vmxml)
        if not define_error:
            vmxml.sync()
            if vm_is_active:
                vm.start()
                vm.wait_for_login().close()
        else:
            result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_exit_status(result, True)
            if err_msg:
                libvirt.check_result(result, err_msg)

    def get_default_cpuset():
        """
        Get default cpuset

        :return: default cpuset value
        """
        default_cpuset = ""
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        try:
            default_cpuset = vmxml.cpuset
        except LibvirtXMLNotFoundError:
            cmd = "lscpu | awk '/On-line CPU/ {print $NF}'"
            default_cpuset = process.run(cmd, shell=True).stdout_text.strip()
        logging.debug("default cpuset is %s", default_cpuset)
        return default_cpuset

    def default_iothreadinfo():
        """
        Generate default iothreadinfo output from xml settings

        :return: default iothreadinfo dict
        """
        exp_info = {}
        cpu_affinity = get_default_cpuset()

        if iothread_ids:
            for iothread_id in iothread_ids.split():
                exp_info[iothread_id] = cpu_affinity
        if iothread_num:
            if iothread_ids:
                iothreadid_list = iothread_ids.split()
                if int(iothread_num) > len(iothreadid_list):
                    needs = int(iothread_num) - len(iothreadid_list)
                    for id in range(1, max(int(iothread_num),
                                           max([int(x)
                                                for x in iothreadid_list])) + 1):
                        if needs > 0:
                            if str(id) not in iothreadid_list:
                                exp_info[str(id)] = cpu_affinity
                                needs = needs - 1
                        else:
                            break
            else:
                for id in range(1, int(iothread_num)+1):
                    exp_info[str(id)] = cpu_affinity

        logging.debug("exp_iothread_info is %s", exp_info)
        return exp_info

    def update_expected_iothreadinfo(org_info, id, act="add", cpuset=None):
        """
        Update expected iothreadinfo dict

        :param org_info: original iothreadinfo dict
        :param id: thread id
        :param act: action to do, it may be "add", "del" or "updated"
        :param cpuset: cpuset to be updated
        """
        if act == "add":
            org_info[id] = get_default_cpuset()
        elif act == "del":
            if id in org_info:
                del org_info[id]
            else:
                logging.debug("No such key {} in {}".format(id, org_info))
        elif act == "update":
            if not cpuset:
                cpuset = get_default_cpuset()
            org_info[id] = cpuset
        else:
            logging.error("Incorrect action!")

    def get_iothread_pool(vm_name, thread_id):
        """
        Get iothread pool values for the specified iothread id

        :param vm_name: name of vm
        :param thread_id: thread id
        :return: iothread pool time values
        """
        iothread_pool = {}
        domstats_output = virsh.domstats(vm_name, "--iothread", debug=True)

        for item in re.findall("iothread."+thread_id+".poll.*",
                               domstats_output.stdout):
            iothread_pool[item.split("=")[0]] = item.split("=")[1]

        logging.debug("iothread pool values for thread id {} are {}."
                      .format(thread_id, iothread_pool))
        return iothread_pool

    def exec_iothreaddel():
        """
        Run "virsh iothreaddel" and check if xml is updated correctly

        :raise: test.fail if virsh command failed
        """
        logging.debug("doing iothread del")
        result = virsh.iothreaddel(vm_name, iothreaddel,
                                   debug=True, ignore_status=True)
        libvirt.check_exit_status(result, status_error)
        if not status_error:
            update_expected_iothreadinfo(exp_iothread_info,
                                         iothreaddel, "del")
            xml_info = vm_xml.VMXML.new_from_dumpxml(vm_name)
            try:
                iothreads = xml_info.iothreadids.iothread
            except LibvirtXMLNotFoundError:
                logging.debug("No iothreadids in xml")
            else:
                if iothreaddel in iothreads:
                    test.fail("The iothread id {} is not removed from xml."
                              .format(iothreaddel))
        else:
            if err_msg:
                libvirt.check_result(result, err_msg)

    def exec_iothreadadd():
        """
        Run "virsh iothreadadd" and check xml

        :raise: test.fail if virsh command failed
        """

        virsh.iothreadadd(vm_name, iothreadadd, debug=True)
        update_expected_iothreadinfo(exp_iothread_info,
                                     iothreadadd, "add")
        # Check xml
        xml_info = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if iothreadadd not in xml_info.iothreadids.iothread:
            test.fail("The iothread id {} is not added into xml"
                      .format(iothreadadd))

    def exec_iothreadpin():
        """
        Run "virsh iothreadpin" and check xml

        :raise: test.fail if virsh command failed
        """

        thread_id, cpuset = iothreadpin.split()
        virsh.iothreadpin(vm_name, thread_id, cpuset, debug=True)
        update_expected_iothreadinfo(exp_iothread_info,
                                     thread_id, "update", cpuset)
        # Check xml
        xml_info = vm_xml.VMXML.new_from_dumpxml(vm_name)
        item = {'cpuset': cpuset, 'iothread': thread_id}
        if item not in xml_info.cputune.iothreadpins:
            test.fail("Unable to get {} from xml".format(item))

    def exec_iothreadset():
        """
        Run "virsh iothreadset" and check if iothread pool values are updated
        or not

        :raise: test.fail if the result of virsh command is not as expected
        """
        # The command "virsh iothreadset" needs vm in running stats
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()

        # Check domstats before run virsh iothreadset
        global ORG_IOTHREAD_POOL
        ORG_IOTHREAD_POOL = get_iothread_pool(vm_name, iothreadset_id)
        result = virsh.iothreadset(vm_name, iothreadset_id, iothreadset_val,
                                   debug=True, ignore_status=True)

        libvirt.check_exit_status(result, status_error)
        if err_msg:
            libvirt.check_result(result, expected_fails=err_msg)

        # Check domstats again
        global UPDATE_IOTHREAD_POOL
        UPDATE_IOTHREAD_POOL = get_iothread_pool(vm_name, iothreadset_id)
        check_iothread_pool(ORG_IOTHREAD_POOL, UPDATE_IOTHREAD_POOL,
                            status_error)

        # Check if the values are updated as expected
        if not status_error:
            lst = iothreadset_val.split()
            exp_pool = {re.sub('--', "iothread."+iothreadset_id+".",
                        lst[i]): lst[i + 1] for i in range(0, len(lst), 2)}
            check_iothread_pool(UPDATE_IOTHREAD_POOL, exp_pool, True)

    def exec_attach_disk(vm_name, source, target, thread_id,
                         ignore_status=False):
        """
        Attach disk with iothread and check the result

        :param vm_name: name of guest
        :param source: source of disk device
        :param target: target of disk device
        :param thread_id: thread id
        :param ignore_status: True - not raise exception when failed
                              False - raise exception when failed
        :raise: test.fail
        """

        result = virsh.attach_disk(vm_name, source, target,
                                   "--iothread "+thread_id,
                                   ignore_status=ignore_status, debug=True)
        libvirt.check_exit_status(result, ignore_status)
        if not ignore_status:
            act_id = vmxml.get_disk_attr(vm_name, target, "driver", "iothread")
            if thread_id != act_id:
                test.fail("The iothread id in xml is incorrect. Expected: {} "
                          "Actual: {}".format(thread_id, act_id))
        else:
            if err_msg:
                libvirt.check_result(result, err_msg)

    def exec_detach_disk(vm_name, target, disk_path):
        """
        Detach disk with iothread and check the result

        :param vm_name: name of guest
        :param target: target of disk device
        :param disk_path: disk image path
        :param dargs: standardized virsh function API keywords
        :raise: test.fail if disk is not detached
        """
        virsh.detach_disk(vm_name, disk_path, debug=True)

        def _check_disk(target):
            return target not in vm.get_blk_devices()

        if not utils_misc.wait_for(lambda: _check_disk(target), 10):
            test.fail("Disk {} is not detached.".format(target))

    def exec_iothreaddel_without_detach_disk(vm_name, disk_path, disk_target,
                                             disk_thread_id):
        """
        Test iothreaddel without detach disk which is attached with iothread

        :param vm_name: name of guest
        :param disk_path: disk image path
        :param disk_target: target of disk source
        :param disk_thread_id: thread id to be attached
        """
        exec_iothreadadd()
        exec_attach_disk(vm_name, disk_path, disk_target, disk_thread_id)
        exec_iothreaddel()

    def check_iothread_pool(org_pool, act_pool, is_equal=False):
        """
        Compare the iothread pool values between orginal and actual ones

        :param org_pool: original pool
        :param act_pool: actual pool
        :param is_equal: True to assume they are some values
                         False to check if they are different
        :raise: test.fail if result does not show as expected
        """
        if (org_pool == act_pool) != is_equal:
            err_info = ("The iothread pool values haven't been updated!"
                        "Expected: {}, Actual: {}".format(org_pool, act_pool))
            if is_equal:
                err_info = ("The iothread pool values have been updated "
                            "unexpectly! Expected: {}, Actual: {}"
                            .format(org_pool, act_pool))
            test.fail(err_info)

    def check_schedinfo():
        """
        Check schedinfo operation
        """
        def _exec_schedinfo(items, update_error=False):
            """
            Run "virsh schedinfo" command and check result

            :param items: items to be matched
            :param update_error: True - raise exception when items are updated
                                 False - raise exception when items are
                                         not updated
            :raise: test.fail when "virsh schedinfo" command failed
            """
            result = virsh.schedinfo(vm_name, debug=True)
            libvirt.check_exit_status(result)
            if update_error:
                items.update({"iothread_period": 100000})
                if libvirt_version.version_compare(7, 4, 0):
                    items.update({"iothread_quota": 17592186044415})
                else:
                    items.update({"iothread_quota": -1})
            for key, val in items.items():
                if not re.findall(key+'\s*:\s+'+str(val), result.stdout):
                    test.fail("Unable to find expected value {}:{} from {}"
                              .format(key, val, result))

        items = {}
        if iothread_quota:
            items["iothread_quota"] = int(iothread_quota)
        if iothread_period:
            items["iothread_period"] = int(iothread_period)

        if not items:
            test.error("schedinfo: Nothing to check!")

        _exec_schedinfo(items)
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
        _exec_schedinfo(items, True)

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    iothread_num = params.get("iothread_num")
    iothread_ids = params.get("iothread_ids")
    iothreadpins = params.get("iothreadpins")
    iothreaddel = params.get("iothreaddel")
    iothreadadd = params.get("iothreadadd")
    iothreadpin = params.get("iothreadpin")
    iothreadset_id = params.get("iothreadset_id")
    iothreadset_val = params.get("iothreadset_val")
    iothreadscheds = params.get("iothreadscheds")
    iothread_quota = params.get("iothread_quota")
    iothread_period = params.get("iothread_period")

    # For attach/detach disk test
    create_disk = "yes" == params.get("create_disk", "no")
    disk_size = params.get("disk_size", "30M")
    disk_format = params.get("disk_format", "qcow2")
    disk_target = params.get("disk_target", "vdb")
    disk_img = params.get("disk_img", "test_disk.qcow2")
    disk_thread_id = params.get("disk_thread_id", "1")

    pre_vm_stats = params.get("pre_vm_stats")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    restart_vm = "yes" == params.get("restart_vm", "no")
    start_vm = "yes" == params.get("start_vm", "no")
    test_operations = params.get("test_operations")

    status_error = "yes" == params.get("status_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    err_msg = params.get("err_msg")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if iothreadset_id and not libvirt_version.version_compare(5, 0, 0):
            test.cancel('This version of libvirt does\'nt support '
                        'virsh command: iothreadset')

        if pre_vm_stats == "running":
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login().close()
        else:
            if vm.is_alive():
                vm.destroy()

        # Update xml for test
        if define_error:
            update_iothread_xml(True)
        else:
            update_iothread_xml()
            exp_iothread_info = default_iothreadinfo()

            # For disk attach/detach test
            if create_disk:
                disk_path = os.path.join(data_dir.get_tmp_dir(), disk_img)
                image_cmd = "qemu-img create -f %s %s %s" % (disk_format,
                                                             disk_path,
                                                             disk_size)
                logging.info("Create image for disk: %s", image_cmd)
                process.run(image_cmd, shell=True)

            if test_operations:
                for action in test_operations.split(","):
                    if action == "iothreaddel":
                        exec_iothreaddel()
                    elif action == "iothreadadd":
                        exec_iothreadadd()
                    elif action == "iothreadpin":
                        exec_iothreadpin()
                    elif action == "iothreadset":
                        exec_iothreadset()
                    elif action == "checkschedinfo":
                        check_schedinfo()
                    elif action == "attachdisk":
                        exec_attach_disk(vm_name, disk_path, disk_target,
                                         disk_thread_id,
                                         ignore_status=status_error)
                    elif action == "detachdisk":
                        exec_detach_disk(vm_name, disk_target, disk_path)
                    elif action == "deletewithoutdetach":
                        exec_iothreaddel_without_detach_disk(vm_name, disk_path,
                                                             disk_target,
                                                             disk_thread_id)
                    else:
                        test.error("Unknown operation: %s" % action)

            if restart_libvirtd:
                utils_libvirtd.libvirtd_restart()
                if iothreadset_id and iothreadset_val:
                    after_restart_domstas = get_iothread_pool(vm_name,
                                                              iothreadset_id)
                    check_iothread_pool(UPDATE_IOTHREAD_POOL,
                                        after_restart_domstas, True)

            # Check if vm could start successfully
            if start_vm:
                if vm.is_alive():
                    vm.destroy()
                result = virsh.start(vm_name, debug=True)
                libvirt.check_exit_status(result, status_error)
                if err_msg:
                    libvirt.check_result(result, expected_fails=err_msg)

            if not status_error:
                iothread_info = libvirt.get_iothreadsinfo(vm_name)
                if exp_iothread_info != iothread_info:
                    test.fail("Unexpected value! Expect {} but get {}."
                              .format(exp_iothread_info, iothread_info))
                if restart_vm:
                    logging.debug("restarting vm")
                    if vm.is_alive():
                        vm.destroy()
                    vm.start()
                    vm.wait_for_login()
                    if iothreadset_id and iothreadset_val:
                        restart_vm_domstas = get_iothread_pool(vm_name,
                                                               iothreadset_id)
                        check_iothread_pool(ORG_IOTHREAD_POOL,
                                            restart_vm_domstas, True)

    finally:
        logging.debug("Recover test environment")
        if vm.is_alive():
            vm.destroy()

        bkxml.sync()
