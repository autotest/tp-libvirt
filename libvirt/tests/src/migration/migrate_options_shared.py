import os
import logging
import time
import math
import re
import threading
import platform
import tempfile
import copy
import datetime
import subprocess

from avocado.utils import process
from avocado.utils import memory
from avocado.utils import distro
from avocado.utils import cpu as cpuutil
from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import utils_misc
from virttest import utils_split_daemons
from virttest import defaults
from virttest import data_dir
from virttest import virsh
from virttest import libvirt_version
from virttest import libvirt_remote
from virttest import remote
from virttest import utils_package
from virttest import utils_iptables
from virttest import utils_secret
from virttest import utils_conn
from virttest import utils_config
from virttest import xml_utils
from virttest import migration

from virttest.utils_iptables import Iptables
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_conn import TLSConnection
from virttest.utils_libvirt import libvirt_config
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test virsh migrate command.
    """

    def set_feature(vmxml, feature, value):
        """
        Set guest features for PPC

        :param state: the htm status
        :param vmxml: guest xml
        """
        features_xml = vm_xml.VMFeaturesXML()
        if feature == 'hpt':
            hpt_xml = vm_xml.VMFeaturesHptXML()
            hpt_xml.resizing = value
            features_xml.hpt = hpt_xml
        elif feature == 'htm':
            features_xml.htm = value
        vmxml.features = features_xml
        vmxml.sync()

    def trigger_hpt_resize(session):
        """
        Check the HPT order file and dmesg

        :param session: the session to guest

        :raise: test.fail if required message is not found
        """
        hpt_order_path = "/sys/kernel/debug/powerpc/hpt_order"
        hpt_order = session.cmd_output('cat %s' % hpt_order_path).strip()
        hpt_order = int(hpt_order)
        logging.info('Current hpt_order is %d', hpt_order)
        hpt_order += 1
        cmd = 'echo %d > %s' % (hpt_order, hpt_order_path)
        cmd_result = session.cmd_status_output(cmd)
        result = process.CmdResult(stderr=cmd_result[1],
                                   stdout=cmd_result[1],
                                   exit_status=cmd_result[0])
        libvirt.check_exit_status(result)
        dmesg = session.cmd('dmesg')
        dmesg_content = params.get('dmesg_content').split('|')
        for content in dmesg_content:
            if content % hpt_order not in dmesg:
                test.fail("'%s' is missing in dmesg" % (content % hpt_order))
            else:
                logging.info("'%s' is found in dmesg", content % hpt_order)

    def check_virsh_command_and_option(command, option=None):
        """
        Check if virsh command exists

        :param command: the command to be checked
        :param option: the command option to be checked
        """
        msg = "This version of libvirt does not support "
        if not virsh.has_help_command(command):
            test.cancel(msg + "virsh command '%s'" % command)

        if option and not virsh.has_command_help_match(command, option):
            test.cancel(msg + "virsh command '%s' with option '%s'" % (command,
                                                                       option))

    def add_ctrls(vm_xml, dev_type="pci", dev_index="0", dev_model="pci-root"):
        """
        Add multiple devices

        :param dev_type: the type of the device to be added
        :param dev_index: the maximum index of the device to be added
        :param dev_model: the model of the device to be added
        """
        for inx in range(0, int(dev_index) + 1):
            newcontroller = Controller("controller")
            newcontroller.type = dev_type
            newcontroller.index = inx
            newcontroller.model = dev_model
            logging.debug("New device is added:\n%s", newcontroller)
            vm_xml.add_device(newcontroller)
        vm_xml.sync()

    def add_tpm(vm, tpm_args):
        """
        Add tpm device to vm

        :param vm: The guest
        :param tpm_args: Parameters for tpm device
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        vmxml.remove_all_device_by_type('tpm')
        tpm_dev = libvirt.create_tpm_dev(tpm_args)
        logging.debug("tpm xml is %s", tpm_dev)
        vmxml.add_device(tpm_dev)
        vmxml.sync()

    def do_migration(vm, dest_uri, options, extra):
        """
        Execute the migration with given parameters
        :param vm: the guest to be migrated
        :param dest_uri: the destination uri for migration
        :param options: options next to 'migrate' command
        :param extra: options in the end of the migrate command line

        :return: CmdResult object
        """
        logging.info("Sleeping 10 seconds before migration")
        time.sleep(10)
        # Migrate the guest.
        virsh_args.update({"ignore_status": True})
        migration_res = vm.migrate(dest_uri, options, extra, **virsh_args)
        if int(migration_res.exit_status) != 0:
            logging.error("Migration failed for %s.", vm_name)
            return migration_res

        if vm.is_alive():  # vm.connect_uri was updated
            logging.info("VM is alive on destination %s.", dest_uri)
        else:
            test.fail("VM is not alive on destination %s" % dest_uri)

        # Throws exception if console shows panic message
        vm.verify_kernel_crash()
        return migration_res

    def cleanup_libvirtd_log(log_file):
        """
        Remove existing libvirtd log file on source and target host.

        :param log_file: log file with absolute path
        """
        if os.path.exists(log_file):
            logging.debug("Delete local libvirt log file '%s'", log_file)
            os.remove(log_file)
        cmd = "rm -f %s" % log_file
        logging.debug("Delete remote libvirt log file '%s'", log_file)
        remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)

    def run_stress_in_vm():
        """
        The function to load stress in VM
        """
        stress_args = params.get("stress_args", "--cpu 8 --io 4 "
                                 "--vm 2 --vm-bytes 128M "
                                 "--timeout 20s")
        try:
            vm_session.cmd('stress %s' % stress_args)
        except Exception as detail:
            logging.debug(detail)

    def control_migrate_speed(to_speed=1, opts=""):
        """
        Control migration duration

        :param to_speed: the speed value in Mbps to be set for migration
        :return int: the new migration speed after setting
        """
        virsh_args.update({"ignore_status": False})
        old_speed = virsh.migrate_getspeed(vm_name, extra=opts, **virsh_args)
        logging.debug("Current migration speed is %s MiB/s\n", old_speed.stdout.strip())
        logging.debug("Set migration speed to %d MiB/s\n", to_speed)
        cmd_result = virsh.migrate_setspeed(vm_name, to_speed, extra=opts, **virsh_args)
        actual_speed = virsh.migrate_getspeed(vm_name, extra=opts, **virsh_args)
        logging.debug("New migration speed is %s MiB/s\n", actual_speed.stdout.strip())
        return int(actual_speed.stdout.strip())

    def check_setspeed(params):
        """
        Set/get migration speed

        :param params: the parameters used
        :raise: test.fail if speed set does not take effect
        """
        expected_value = int(params.get("migrate_speed", '41943040')) // (1024 * 1024)
        actual_value = control_migrate_speed(to_speed=expected_value)
        params.update({'compare_to_value': actual_value})
        if actual_value != expected_value:
            test.fail("Migration speed is expected to be '%d MiB/s', but '%d MiB/s' "
                      "found" % (expected_value, actual_value))

    def check_output(output, expected_value_list):
        """
        Check if the output match expected value or not

        :param output: actual output
        :param expected_value_list: expected value
        :raise: test.fail if unable to find item(s)
        """
        logging.debug("Actual output is %s", output)
        for item in expected_value_list:
            if not re.findall(item, output):
                test.fail("Unalbe to find {}".format(item))

    def check_interval_not_fixed(search_str, log_file, interval=0.05,
                                 session=None):
        """
        Check the interval of the log output with specific string and expect not
        match the value of param 'interval'.

        :param search_str: String to be searched
        :param log_file: the given file
        :param interval: interval in second
        :param session: ShellSession object of remote host
        :raise: test.fail when the real interval is equal to given value

        """
        cmd = "grep '%s' %s | cut -d '+' -f1" % (search_str, log_file)
        cmdStd, cmdStdout = utils_misc.cmd_status_output(cmd, shell=True,
                                                         session=session)

        if cmdStd:
            test.fail("Unalbe to get {} from {}.".format(search_str, log_file))
        date_list = []
        for line in cmdStdout.splitlines():
            if line:
                # pick up the time of output who has specific string
                # from log_file
                date_list.append(datetime.datetime.strptime(line,
                                                            "%Y-%m-%d %H:%M:%S.%f"))
        if len(date_list) > 1:
            for x in range(len(date_list)-1):
                date_list[x] = (date_list[x+1]-date_list[x]).total_seconds()
            if len(set(date_list[:-1])) == 1 and interval in date_list[:-1]:
                test.fail("{} seconds is unexpected time period.Time duration"
                          "(seconds) is {}".format(interval, date_list[:-1]))
        else:
            test.fail("Need at least 2 items in {}. Unable to get time period."
                      "cmdRes is {}.".format(log_file, cmdStdout))

    def check_domjobinfo(params, option=""):
        """
        Check given item in domjobinfo of the guest is as expected

        :param params: the parameters used
        :param option: options for domjobinfo
        :raise: test.fail if the value of given item is unexpected
        """
        def search_jobinfo(jobinfo):
            """
            Find value of given item in domjobinfo

            :param jobinfo: cmdResult object
            :raise: test.fail if not found
            """
            for item in jobinfo.stdout.splitlines():
                if item.count(jobinfo_item):
                    groups = re.findall(r'[0-9.]+', item.strip())
                    logging.debug("In '%s' search '%s'\n", item, groups[0])
                    if (math.fabs(float(groups[0]) - float(compare_to_value)) //
                       float(compare_to_value) > diff_rate):
                        test.fail("{} {} has too much difference from "
                                  "{}".format(jobinfo_item,
                                              groups[0],
                                              compare_to_value))
                break

        jobinfo_item = params.get("jobinfo_item")
        compare_to_value = params.get("compare_to_value")
        logging.debug("compare_to_value:%s", compare_to_value)
        diff_rate = float(params.get("diff_rate", "0"))
        if not jobinfo_item or not compare_to_value:
            return
        vm_ref = '{}{}'.format(vm_name, option)
        jobinfo = virsh.domjobinfo(vm_ref, **virsh_args)
        search_jobinfo(jobinfo)

        check_domjobinfo_remote = params.get("check_domjobinfo_remote")
        if check_domjobinfo_remote:
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            jobinfo = remote_virsh_session.domjobinfo(vm_ref, **virsh_args)
            search_jobinfo(jobinfo)
            remote_virsh_session.close_session()

    def search_jobinfo_output(jobinfo, items_to_check, postcopy_req=False):
        """
        Check the results of domjobinfo

        :param jobinfo: cmdResult object
        :param items_to_check: expected value
        :param postcopy_req: True for postcopy migration and False for precopy
        :return: False if not found
        """
        expected_value = copy.deepcopy(items_to_check)
        logging.debug("The items_to_check is %s", expected_value)
        for item in jobinfo.splitlines():
            item_key = item.strip().split(':')[0]
            if "all_items" in expected_value and len(item_key) > 0:
                # "Time elapsed w/o network" and "Downtime w/o network"
                # have a chance to be missing, it is normal
                if item_key in ['Downtime w/o network', 'Time elapsed w/o network']:
                    continue
                if expected_value["all_items"][0] == item_key:
                    del expected_value["all_items"][0]
                else:
                    test.fail("The item '%s' should be '%s'" %
                              (item_key, expected_value["all_items"][0]))

            if item_key in expected_value:
                item_value = ':'.join(item.strip().split(':')[1:]).strip()
                if item_value != expected_value.get(item_key):
                    test.fail("The value of {} is {} which is not "
                              "expected".format(item_key,
                                                item_value))
                else:
                    del expected_value[item_key]
            if postcopy_req and item_key == "Postcopy requests":
                if int(item.strip().split(':')[1]) <= 0:
                    test.fail("The value of Postcopy requests is incorrect")

        # Check if all the items in expect_dict checked or not
        if "all_items" in expected_value:
            if len(expected_value["all_items"]) > 0:
                test.fail("Missing item: {} from all_items"
                          .format(expected_value["all_items"]))
            else:
                del expected_value["all_items"]
        if len(expected_value) != 0:
            test.fail("Missing item: {}".format(expected_value))

    def set_migratepostcopy():
        """
        Switch to postcopy during migration
        """
        if not utils_misc.wait_for(
           lambda: not virsh.migrate_postcopy(vm_name, debug=True).exit_status, 5):
            test.fail("Failed to set migration postcopy.")

        if not utils_misc.wait_for(
           lambda: libvirt.check_vm_state(vm_name, "paused",
                                          "post-copy"), 10):
            test.fail("vm status is expected to 'paused (post-copy)'")

    def check_domjobinfo_output(option="", is_mig_compelete=False):
        """
        Check all items in domjobinfo of the guest on both remote and local

        :param option: options for domjobinfo
        :param is_mig_compelete: False for domjobinfo checking during migration,
                            True for domjobinfo checking after migration
        :raise: test.fail if the value of given item is unexpected
        """
        expected_list_during_mig = ["Job type", "Operation", "Time elapsed",
                                    "Data processed", "Data remaining",
                                    "Data total", "Memory processed",
                                    "Memory remaining", "Memory total",
                                    "Memory bandwidth", "Dirty rate", "Page size",
                                    "Iteration", "Constant pages", "Normal pages",
                                    "Normal data", "Expected downtime", "Setup time"]
        if libvirt_version.version_compare(4, 10, 0):
            expected_list_during_mig.insert(13, "Postcopy requests")

        expected_list_after_mig_src = copy.deepcopy(expected_list_during_mig)
        expected_list_after_mig_src[-2] = 'Total downtime'
        expected_list_after_mig_dest = copy.deepcopy(expected_list_after_mig_src)

        # Check version in remote
        if not expected_list_after_mig_dest.count("Postcopy requests"):
            remote_session = remote.remote_login("ssh", server_ip, "22", server_user,
                                                 server_pwd, "#")
            if libvirt_version.version_compare(4, 10, 0, session=remote_session):
                expected_list_after_mig_dest.insert(14, "Postcopy requests")
            remote_session.close()

        expect_dict = {"src_notdone": {"Job type": "Unbounded",
                                       "Operation": "Outgoing migration",
                                       "all_items": expected_list_during_mig},
                       "dest_notdone": {"error": "Operation not supported: mig"
                                                 "ration statistics are availab"
                                                 "le only on the source host"},
                       "src_done": {"Job type": "Completed",
                                    "Operation": "Outgoing migration",
                                    "all_items": expected_list_after_mig_src},
                       "dest_done": {"Job type": "Completed",
                                     "Operation": "Incoming migration",
                                     "all_items": expected_list_after_mig_dest}}
        pc_opt = False
        if postcopy_options:
            pc_opt = True
            if is_mig_compelete:
                expect_dict["dest_done"].clear()
                expect_dict["dest_done"]["Job type"] = "None"
            else:
                set_migratepostcopy()

        vm_ref = '{}{}'.format(vm_name, option)
        src_jobinfo = virsh.domjobinfo(vm_ref, **virsh_args)
        cmd = "virsh domjobinfo {} {}".format(vm_name, option)
        dest_jobinfo = remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)

        if not is_mig_compelete:
            search_jobinfo_output(src_jobinfo.stdout, expect_dict["src_notdone"])
            search_jobinfo_output(dest_jobinfo.stderr, expect_dict["dest_notdone"])
        else:
            search_jobinfo_output(src_jobinfo.stdout, expect_dict["src_done"],
                                  postcopy_req=pc_opt)
            search_jobinfo_output(dest_jobinfo.stdout, expect_dict["dest_done"],
                                  postcopy_req=pc_opt)

    def check_maxdowntime(params):
        """
        Set/get migration maxdowntime

        :param params: the parameters used
        :raise: test.fail if maxdowntime set does not take effect
        """
        expected_value = int(float(params.get("migrate_maxdowntime", '0.3')) * 1000)
        virsh_args.update({"ignore_status": False})
        old_value = int(virsh.migrate_getmaxdowntime(vm_name).stdout.strip())
        logging.debug("Current migration maxdowntime is %d ms", old_value)
        logging.debug("Set migration maxdowntime to %d ms", expected_value)
        virsh.migrate_setmaxdowntime(vm_name, expected_value, **virsh_args)
        actual_value = int(virsh.migrate_getmaxdowntime(vm_name).stdout.strip())
        logging.debug("New migration maxdowntime is %d ms", actual_value)
        if actual_value != expected_value:
            test.fail("Migration maxdowntime is expected to be '%d ms', but '%d ms' "
                      "found" % (expected_value, actual_value))
        params.update({'compare_to_value': actual_value})

    def run_time(init_time=2):
        """
        Compare the duration of func to an expected one

        :param init_time: Expected run time
        :raise: test.fail if func takes more than init_time(second)
        """
        def check_time(func):
            def wrapper(*args, **kwargs):
                start = time.time()
                result = func(*args, **kwargs)
                duration = time.time() - start
                if duration > init_time:
                    test.fail("It takes too long to check {}. The duration is "
                              "{}s which should be less than {}s"
                              .format(func.__doc__, duration, init_time))
                return result
            return wrapper
        return check_time

    def run_domstats(vm_name):
        """
        Run domstats and domstate during migration in source and destination

        :param vm_name: VM name
        :raise: test.fail if domstats does not return in 2s
                or domstate is incorrect
        """
        @run_time()
        def check_source_stats(vm_name):
            """domstats in source"""
            vm_stats = virsh.domstats(vm_name)
            logging.debug("domstats in source: {}".format(vm_stats))

        @run_time()
        def check_dest_stats(vm_name):
            """domstats in target"""
            cmd = "virsh domstats {}".format(vm_name)
            dest_stats = remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)
            logging.debug("domstats in destination: {}".format(dest_stats))

        def _check_dest_state(expected_remote_state):
            """
            Check domstate of vm in target

            :param expected_remote_state: The expected value
            :return: True if expected domstate is not found
            """
            cmd = "virsh domstate {}".format(vm_name)
            remote_vm_state = remote.run_remote_cmd(cmd, cmd_parms,
                                                    runner_on_target,
                                                    ignore_status=False)
            if (len(remote_vm_state.stdout.split()) and
               remote_vm_state.stdout.split()[0] == expected_remote_state):
                return True

        expected_remote_state = "paused"
        expected_source_state = ["paused", "running"]
        if postcopy_options:
            set_migratepostcopy()
            expected_remote_state = "running"
            expected_source_state = ["paused"]

        check_source_stats(vm_name)
        vm_stat = virsh.domstate(vm_name, ignore_status=False)
        if ((not len(vm_stat.stdout.split())) or
           vm_stat.stdout.split()[0] not in expected_source_state):
            test.fail("Incorrect VM stat on source machine: {}"
                      .format(vm_stat.stdout))

        if not utils_misc.wait_for(
           lambda: _check_dest_state(expected_remote_state), 5,
           text="check if dest vm state is %s" % expected_remote_state):
            test.fail("Unable to get expected domstate on destination machine "
                      "in 5s!")

        if postcopy_options:
            vm_stat = virsh.domstate(vm_name, ignore_status=False)
            if ((not len(vm_stat.stdout.split())) or
               vm_stat.stdout.split()[0] != "paused"):
                test.fail("Incorrect VM stat on source machine: {}"
                          .format(vm_stat.stdout))

    def kill_qemu_target():
        """
        Kill qemu process on target host during Finish Phase of migration

        :raise: test.fail if domstate is not "post-copy failed" after
                qemu killed
        """
        if not vm.is_qemu():
            test.cancel("This case is qemu guest only.")
        set_migratepostcopy()
        emulator = new_xml.get_devices('emulator')[0]
        logging.debug("emulator is %s", emulator.path)
        cmd = 'pkill -9 {}'.format(os.path.basename(emulator.path))
        runner_on_target.run(cmd)
        if not utils_misc.wait_for(
           lambda: libvirt.check_vm_state(vm_name, "paused",
                                          "post-copy failed"), 60):
            test.fail("vm status is expected to 'paused (post-copy failed)'")

    def drop_network_connection(block_time):
        """
        Drop network connection from target host for a while and then recover it

        :param block_time: The duration(in seconds) of network broken period
        :raise: test.error if direct rule is not added or removed correctly
        """
        logging.debug("Start to drop network")

        if use_firewall_cmd:
            firewall_cmd.add_direct_rule(firewall_rule)
            direct_rules = firewall_cmd.get(key="all-rules", is_direct=True,
                                            zone=None)
            cmdRes = re.findall(firewall_rule, direct_rules)
            if len(cmdRes) == 0:
                test.error("Rule '%s' is not added" % firewall_rule)
        else:
            Iptables.setup_or_cleanup_iptables_rules(firewall_rule)

        logging.debug("Sleep %d seconds" % int(block_time))
        time.sleep(int(block_time))

        if use_firewall_cmd:
            firewall_cmd.remove_direct_rule(firewall_rule)
            direct_rules = firewall_cmd.get(key="all-rules", is_direct=True,
                                            zone=None)
            cmdRes = re.findall(firewall_rule, direct_rules)
            if len(cmdRes):
                test.error("Rule '%s' is not removed correctly" % firewall_rule)
        else:
            Iptables.setup_or_cleanup_iptables_rules(firewall_rule, cleanup=True)

    def check_established(exp_num, port="49152"):
        """
        Parses netstat output for established connection on remote

        :param exp_num: expected number of the ESTABLISHED connections
        :param port: port to be checked
        :raise: test.fail if the connection number is not equal to $exp_num
        """
        if postcopy_options:
            set_migratepostcopy()

        cmd = "netstat -tunap|grep %s |grep ESTABLISHED | wc -l" % port
        result = remote.run_remote_cmd(cmd, cmd_parms, runner_on_target,
                                       ignore_status=False)

        if exp_num != int(result.stdout.strip()):
            test.fail("The number of established connections is unexpected: %s"
                      % result.stdout.strip())

    def suspend_vm(vm):
        """
        Suspend guest on source host and then check state

        :params vm: Vm
        :raise: test.fail if failed to pause vm or
            the state of vm is not 'paused'
        """
        if not vm.pause():
            test.fail("Failed to suspend vm.")
        if not utils_misc.wait_for(
           lambda: libvirt.check_vm_state(vm.name, "paused"), 10):
            test.fail("vm statue is expected to 'paused'")

    def do_actions_during_migrate(params):
        """
        The entry point to execute action list during migration

        :param params: the parameters used
        :raise: test.error if parameter is invalid
        """
        actions_during_migration = params.get("actions_during_migration")
        if not actions_during_migration:
            test.error("Invalid parameter is provided!")
        for action in actions_during_migration.split(","):
            if action == 'setspeed':
                check_setspeed(params)
            elif action == 'domjobinfo':
                check_domjobinfo(params)
            elif action == 'setmaxdowntime':
                check_maxdowntime(params)
            elif action == 'converge':
                check_converge(params)
            elif action == 'domjobinfo_output_all':
                check_domjobinfo_output()
            elif action == 'checkdomstats':
                run_domstats(vm_name)
            elif action == 'setmigratepostcopy':
                set_migratepostcopy()
            elif action == 'killqemutarget':
                kill_qemu_target()
            elif action == 'checkestablished':
                check_established(expConnNum)
            elif action == 'drop_network_connection':
                drop_network_connection(block_time)
            elif action == 'suspendvm':
                suspend_vm(vm)
            elif action == 'cancel_concurrent_migration':
                cancel_bg_migration()
            time.sleep(3)

    def do_actions_after_migrate(params):
        """
        The entry point to execute action list on remote vm after migration

        :param params: the parameters used
        :raise: test.error if parameter is invalid
        """
        actions_after_migration = params.get("actions_after_migration")
        if not actions_after_migration:
            test.error("Invalid parameter is provided!")
        remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
        savefile = params.get("save_file", "/tmp/save.file")
        for action in actions_after_migration.split(","):
            if action == 'save_restore':
                ret = remote_virsh_session.save(vm_name, savefile, debug=True)
                libvirt.check_exit_status(ret)
                time.sleep(3)

                ret = remote_virsh_session.restore(savefile, debug=True)
                remote.run_remote_cmd('rm -f %s' % savefile,
                                      cmd_parms, runner_on_target)
                libvirt.check_exit_status(ret)
                if not libvirt.check_vm_state(vm_name, state="running",
                                              uri=dest_uri):
                    test.fail("Can't get the expected vm state 'running'")
            elif action == "checkdomstate":
                ret = remote_virsh_session.domstate(vm_name, debug=True)\
                    .stdout_text.strip()
                exp_state = "running" if not pause_vm_before_mig else "paused"
                if ret != exp_state:
                    test.fail("The vm state on target host should "
                              "be '%s', but '%s' found" % (exp_state, ret))

            time.sleep(3)
        remote_virsh_session.close_session()

    def attach_channel_xml():
        """
        Create channel xml and attach it to guest configuration
        """
        # Check if pty channel exists already
        for elem in new_xml.devices.by_device_tag('channel'):
            if elem.type_name == channel_type_name:
                logging.debug("{0} channel already exists in guest. "
                              "No need to add new one".format(channel_type_name))
                return

        params = {'channel_type_name': channel_type_name,
                  'target_type': target_type,
                  'target_name': target_name}
        channel_xml = libvirt.create_channel_xml(params)
        virsh.attach_device(vm_name, channel_xml.xml,
                            flagstr="--config", ignore_status=False)
        logging.debug("New VMXML with channel:\n%s", virsh.dumpxml(vm_name))

    def check_timeout_postcopy(params):
        """
        Check the vm state on target host after timeout
        when --postcopy and --timeout-postcopy are used.
        The vm state is expected as running.

        :param params: the parameters used
        """
        timeout = int(params.get("timeout_postcopy", 10))
        time.sleep(timeout + 1)
        remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
        vm_state = remote_virsh_session.domstate(vm_name).stdout_text.strip()
        if vm_state != "running":
            remote_virsh_session.close_session()
            test.fail("After timeout '%s' seconds, "
                      "the vm state on target host should "
                      "be 'running', but '%s' found" % (timeout, vm_state))
        remote_virsh_session.close_session()

    def check_converge(params):
        """
        Handle option '--auto-converge --auto-converge-initial
        --auto-converge-increment '.
        'Auto converge throttle' in domjobinfo should start with
        the initial value and increase with correct increment
        and max value is 99.

        :param params: The parameters used
        :raise: exceptions.TestFail when unexpected or no throttle
                       is found
        """
        initial = int(params.get("initial", 20))
        increment = int(params.get("increment", 10))
        max_converge = int(params.get("max_converge", 99))
        allow_throttle_list = [initial + count * increment
                               for count in range(0, (100 - initial) // increment + 1)
                               if (initial + count * increment) < 100]
        allow_throttle_list.append(max_converge)
        logging.debug("The allowed 'Auto converge throttle' value "
                      "is %s", allow_throttle_list)

        throttle = 0
        jobtype = "None"

        while throttle < 100:
            cmd_result = virsh.domjobinfo(vm_name, debug=True,
                                          ignore_status=True)
            if cmd_result.exit_status:
                logging.debug(cmd_result.stderr)
                # Check if migration is completed
                if "domain is not running" in cmd_result.stderr:
                    args = vm_name + " --completed"
                    cmd_result = virsh.domjobinfo(args, debug=True,
                                                  ignore_status=True)
                    if cmd_result.exit_status:
                        test.error("Failed to get domjobinfo and domjobinfo "
                                   "--completed: %s" % cmd_result.stderr)
                else:
                    test.error("Failed to get domjobinfo: %s" % cmd_result.stderr)
            jobinfo = cmd_result.stdout
            for line in jobinfo.splitlines():
                key = line.split(':')[0]
                if key.count("Job type"):
                    jobtype = line.split(':')[-1].strip()
                elif key.count("Auto converge throttle"):
                    throttle = int(line.split(':')[-1].strip())
                    logging.debug("Auto converge throttle:%s", str(throttle))
            if throttle and throttle not in allow_throttle_list:
                test.fail("Invalid auto converge throttle "
                          "value '%s'" % throttle)
            if throttle == 99:
                logging.debug("'Auto converge throttle' reaches maximum "
                              "allowed value 99")
                break
            if jobtype == "None" or jobtype == "Completed":
                logging.debug("Jobtype:%s", jobtype)
                if not throttle:
                    test.fail("'Auto converge throttle' is "
                              "not found in the domjobinfo")
                break
            time.sleep(5)

    def get_usable_compress_cache(pagesize):
        """
        Get a number which is bigger than pagesize and is power of two.

        :param pagesize: the given integer
        :return: an integer satisfying the criteria
        """
        def calculate(num):
            result = num & (num - 1)
            return (result == 0)

        item = pagesize
        found = False
        while (not found):
            item += 1
            found = calculate(item)
        logging.debug("%d is smallest one that is bigger than '%s' and "
                      "is power of 2", item, pagesize)
        return item

    def update_qemu_conf_on_local_and_remote():
        """
        Update qemu.conf on both local and remote hosts
        """

        conf_dict = eval(params.get("qemu_conf_dict", '{}'))
        conf_dest_dict = params.get("qemu_conf_dest_dict", '{}')

        update_conf_on_local_and_remote("qemu", "qemu",
                                        conf_dict, conf_dest_dict)

    def update_libvirtd_conf_on_local_and_remote():
        """
        Update libvirtd conf file on both local and remote hosts
        """

        conf_dict = eval(params.get("libvirtd_conf_dict", '{}'))
        conf_dest_dict = params.get("libvirtd_conf_dest_dict", '{}')
        conf_type = params.get("libvirtd_conf_type")
        conf_type_dest = params.get("libvirtd_conf_type_dest")

        update_conf_on_local_and_remote(conf_type, conf_type_dest,
                                        conf_dict, conf_dest_dict)

    def update_log_conf_on_local_and_remote():
        """
        Update log conf file on both local and remote hosts
        """

        conf_dict = eval(params.get("log_conf_dict", '{}'))
        conf_dest_dict = params.get("log_conf_dest_dict", '{}')
        conf_type = params.get("log_conf_type")
        conf_type_dest = params.get("log_conf_type_dest")

        update_conf_on_local_and_remote(conf_type, conf_type_dest,
                                        conf_dict, conf_dest_dict)

    def update_conf_on_local_and_remote(conf_type, conf_type_dest,
                                        conf_dict, conf_dest_dict):
        """
        Update libvirt related conf files on both local and remote hosts

        :param conf_type: String type, conf type on local host
        :param conf_dict: Dict of parameters to set on local host
        :param conf_type_dest: String type, conf type on remote host
        :param conf_dest_dict: String type that contains dict of parameters
                               to be set on remote host
        """

        if conf_dict:
            logging.info("Update conf on local host")
            updated_conf_local = update_config_file(
                conf_type, conf_dict, remote=False, remote_params=None
                )
            local_conf_obj_list.append(updated_conf_local)

        if eval(conf_dest_dict):
            logging.info("Update conf on remote host")
            updated_conf_remote = update_config_file(
                conf_type_dest, conf_dest_dict, remote=True,
                remote_params=params
                )
            remote_conf_obj_list.append(updated_conf_remote)

    def get_conf_type(conf_type):
        """
        Convert the configred conf_type to the actual conf_type\
        according to test env.
        Note: The conf_type configured in <test>.cfg is always set to the value
              in modular daemon mode. Need to convert it to the actual value
              according to the test env.

        :param conf_type: Configured conf_type, String type
                          like virtlogd, virtproxyd, etc
        :return conf_type: Actual conf_type, String type,
                           like virtlogd, virtproxyd, etc
        """

        if (not utils_split_daemons.is_modular_daemon() and
            conf_type in ["virtqemud", "virtproxyd", "virtnetworkd",
                          "virtstoraged", "virtinterfaced", "virtnodedevd",
                          "virtnwfilterd", "virtsecretd"]):
            return "libvirtd"
        else:
            return conf_type

    def get_conf_file_path(conf_type):
        """
        Get conf file path by the conf type

        :param conf_type: conf type, like libvirtd, qemu, virtproxyd, etc
        :return conf file path
        """

        return utils_config.get_conf_obj(conf_type).conf_path

    def update_config_file(conf_type, conf_dict, remote=False,
                           remote_params=None):
        """
        Update the specified configuration file with dict

        :param conf_type: String type, conf type
        :param conf_dict: Dict of parameters to set
        :param remote: True to update only remote
                       False to update only local
        :param remote_params: Dict of remote host parameters, which should
                              include: server_ip, server_user, server_pwd
        :return: utils_config.LibvirtConfigCommon object if remote is False, or
                 remote.RemoteFile objects if remote is True
        """

        updated_conf = None

        if not remote:
            logging.debug("Update local conf, conf type is %s, dict is %s",
                          conf_type, conf_dict)
            updated_conf = libvirt.customize_libvirt_config(
                conf_dict, config_type=conf_type,
                remote_host=False, extra_params=None
                )
        else:
            logging.debug("Update remote conf, conf type is %s, dict is %s",
                          conf_type, conf_dict)
            actual_conf_type = get_conf_type(conf_type)
            file_path = get_conf_file_path(actual_conf_type)
            updated_conf = libvirt_remote.update_remote_file(
                remote_params, conf_dict, file_path)

        return updated_conf

    def time_diff_between_vm_host(localvm=True):
        """
        check the time difference between vm and source host
        :param localvm: True if vm is not migrated yet
        """
        if localvm:
            vm_time = virsh.domtime(vm_name, debug=True).stdout
            vm_time_value = int(vm_time.strip().split(":")[-1])
        else:
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            vm_time = remote_virsh_session.domtime(vm_name, debug=True).stdout
            vm_time_value = int(vm_time.strip().split(":")[-1])
            remote_virsh_session.close_session()

        host_time_value = int(time.time())
        time_diff = host_time_value - vm_time_value
        logging.debug("Time difference between source host and vm is %s", time_diff)
        return time_diff

    def cancel_bg_migration():
        """
        Cancel one of the simultaneous migration processes
        """
        cmd = "virsh migrate %s %s %s %s" % (vm_name, dest_uri, options, extra)
        logging.debug("Start migrating from background: %s", cmd)
        p = subprocess.Popen(cmd, shell=True, universal_newlines=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Sleep 10s to wait for migration to start
        time.sleep(10)

        logging.debug("Stopping migration process: %s", p.pid)
        p.terminate()
        stdout, stderr = p.communicate()
        logging.debug("status:[%d], stdout:[%s], stderr:[%s]",
                      p.returncode, stdout, stderr)

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    # Params for NFS shared storage
    shared_storage = params.get("migrate_shared_storage", "")
    if shared_storage == "":
        default_guest_asset = defaults.get_default_guest_os_info()['asset']
        default_guest_asset = "%s.qcow2" % default_guest_asset
        shared_storage = os.path.join(params.get("nfs_mount_dir"),
                                      default_guest_asset)
        logging.debug("shared_storage:%s", shared_storage)

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = params.get("nfs_mount_dir")

    # Local variables
    virsh_args = {"debug": True}
    virsh_opt = params.get("virsh_opt", "")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")
    client_pwd = params.get("client_pwd")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options")
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")
    log_file = params.get("libvirt_log", "/var/log/libvirt/libvirtd.log")
    check_complete_job = "yes" == params.get("check_complete_job", "no")
    check_domjobinfo_results = "yes" == params.get("check_domjobinfo_results")
    check_log_interval = params.get("check_log_interval")
    comp_cache_size = params.get("comp_cache_size")
    maxdowntime_before_mig = "yes" == params.get("set_maxdowntime_before_migration",
                                                 "no")
    check_default_maxdowntime = params.get("check_default_maxdowntime")
    check_event_output = params.get("check_event_output")
    check_tls_destination = "yes" == params.get("check_tls_destination", "no")
    contrl_index = params.get("new_contrl_index", None)
    asynch_migration = "yes" == params.get("asynch_migrate", "no")
    grep_str_remote_log = params.get("grep_str_remote_log", "")
    grep_str_not_in_remote_log = params.get("grep_str_not_in_remote_log", "")
    grep_str_not_in_local_log = params.get("grep_str_not_in_local_log", "")
    grep_str_local_log = params.get("grep_str_local_log", "")
    grep_str_local_log_1 = params.get("grep_str_local_log_1", "")
    disable_verify_peer = "yes" == params.get("disable_verify_peer", "no")
    status_error = "yes" == params.get("status_error", "no")
    stress_in_vm = "yes" == params.get("stress_in_vm", "no")
    low_speed = params.get("low_speed", None)
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")
    timer_migration = "yes" == params.get("timer_migration", "no")
    concurrent_migration = "yes" == params.get("concurrent_migration", "no")

    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    cmd_parms = {'server_ip': server_ip, 'server_user': server_user,
                 'server_pwd': server_pwd}
    hpt_resize = params.get("hpt_resize", None)
    htm_state = params.get("htm_state", None)
    vcpu_num = params.get("vcpu_num")
    block_time = params.get("block_time", 30)
    parallel_cn_nums = params.get("parallel_cn_nums")
    tpm_args = eval(params.get("tpm_args", '{}'))
    src_secret_value = params.get("src_secret_value")
    dst_secret_value = params.get("dst_secret_value")
    update_tpm_secret = "yes" == params.get("update_tpm_secret", "no")
    break_network_connection = "yes" == params.get("break_network_connection",
                                                   "no")
    pause_vm_before_mig = "yes" == params.get("pause_vm_before_migration", "no")

    # For pty channel test
    add_channel = "yes" == params.get("add_channel", "no")
    channel_type_name = params.get("channel_type_name", None)
    target_type = params.get("target_type", None)
    target_name = params.get("target_name", None)
    cmd_run_in_remote_guest = params.get("cmd_run_in_remote_guest", None)
    cmd_run_in_remote_guest_1 = params.get("cmd_run_in_remote_guest_1", None)
    cmd_run_in_remote_host = params.get("cmd_run_in_remote_host", None)
    cmd_run_in_remote_host_1 = params.get("cmd_run_in_remote_host_1", None)
    cmd_run_in_remote_host_2 = params.get("cmd_run_in_remote_host_2", None)
    cmd_in_vm_after_migration = params.get("cmd_in_vm_after_migration")
    remote_dargs = {'server_ip': server_ip, 'server_user': server_user,
                    'server_pwd': server_pwd,
                    'file_path': "/etc/libvirt/libvirt.conf"}

    # For qemu command line checking
    qemu_check = params.get("qemu_check", None)

    xml_check_after_mig = params.get("guest_xml_check_after_mig", None)

    # params for cache matrix test
    cache = params.get("cache")
    remove_cache = "yes" == params.get("remove_cache", "no")
    err_msg = params.get("err_msg")
    extra_args = {'func_params': params,
                  'status_error': params.get("status_error", "no"),
                  'err_msg': err_msg}
    arch = platform.machine()
    if any([hpt_resize, contrl_index, htm_state]) and 'ppc64' not in arch:
        test.cancel("The case is PPC only.")

    # For TLS
    tls_recovery = params.get("tls_auto_recovery", "yes")
    # qemu config
    qemu_conf_dict = None
    # libvirtd config
    libvirtd_conf_dict = None

    # remote shell session
    remote_session = None

    remote_virsh_session = None
    vm = None
    vm_session = None
    libvirtd_conf = None
    qemu_conf = None
    mig_result = None
    test_exception = None
    is_TestError = False
    is_TestFail = False
    is_TestCancel = False

    # Objects to be cleaned up in the end
    local_conf_obj_list = []
    remote_conf_obj_list = []
    objs_list = []
    tls_obj = None

    expConnNum = 0
    tpm_sec_uuid = None
    dest_tmp_sec_uuid = None
    remove_dict = {}
    remote_libvirt_file = None
    src_libvirt_file = None

    # Local variables
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    bk_uri = vm.connect_uri

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    try:
        if parallel_cn_nums or extra.count("parallel"):
            if not libvirt_version.version_compare(5, 2, 0):
                test.cancel("This libvirt version doesn't support "
                            "multifd feature.")
        if check_tls_destination:
            if not libvirt_version.version_compare(5, 6, 0):
                test.cancel("This libvirt version doesn't support "
                            "tls-destination option.")
        # only emulator backend type in migration for now
        if tpm_args and tpm_args.get("backend_type") == "emulator":
            if not utils_misc.compare_qemu_version(4, 0, 0, is_rhev=False):
                test.cancel("This qemu version doesn't support "
                            "vtpm emulator backend.")
        if concurrent_migration and not libvirt_version.version_compare(6, 0, 0):
            test.cancel("This libvirt version doesn't support "
                        "concurrent migration.")

        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        if break_network_connection:
            if distro.detect().name == 'rhel' and int(distro.detect().version) < 8:
                use_firewall_cmd = False
                firewall_rule = ["INPUT -s {}/32 -j DROP".format(server_ip)]
            else:
                if not utils_package.package_install("firewalld"):
                    test.error("Failed to install firewalld.")
                use_firewall_cmd = True
                firewall_cmd = utils_iptables.Firewall_cmd()
                firewall_rule = ("ipv4 filter INPUT 0 --source {} -j DROP"
                                 .format(server_ip))
            logging.debug("firewall rule is '%s'", firewall_rule)

        # Change the configuration files if needed before starting guest
        # For qemu.conf
        if extra.count("--tls"):
            # Setup TLS
            tls_obj = TLSConnection(params)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
                tls_obj.auto_recover = True
                tls_obj.conn_setup()

        # Clean up existing libvirtd log
        cleanup_libvirtd_log(log_file)

        # Setup libvirt related conf files
        update_qemu_conf_on_local_and_remote()
        update_libvirtd_conf_on_local_and_remote()
        update_log_conf_on_local_and_remote()

        # Prepare required guest xml before starting guest
        if contrl_index:
            new_xml.remove_all_device_by_type('controller')
            logging.debug("After removing controllers, current XML:\n%s\n", new_xml)
            add_ctrls(new_xml, dev_index=contrl_index)

        if add_channel:
            attach_channel_xml()

        if hpt_resize:
            if cpuutil.get_cpu_vendor_name() != 'power8':
                test.cancel('HPT cases are for Power8 only.')
            set_feature(new_xml, 'hpt', hpt_resize)

        if htm_state:
            set_feature(new_xml, 'htm', htm_state)

        if vcpu_num:
            vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu_num))

        if cache:
            params["driver_cache"] = cache
        if remove_cache:
            params["enable_cache"] = "no"

        if tpm_args:
            if update_tpm_secret:
                auth_sec_dict = {"sec_ephemeral": "no",
                                 "sec_private": "yes",
                                 "sec_desc": "sample vTPM secret",
                                 "sec_usage": "vtpm",
                                 "sec_name": "VTPM_example"}
                utils_secret.clean_up_secrets()
                tpm_sec_uuid = libvirt.create_secret(auth_sec_dict)
                logging.debug("tpm sec uuid on source: %s", tpm_sec_uuid)
                tpm_args.update({"encryption_secret": tpm_sec_uuid})
                add_tpm(vm, tpm_args)
                if src_secret_value:
                    virsh.secret_set_value(tpm_sec_uuid, src_secret_value,
                                           encode=True, debug=True)
                if not remote_virsh_session:
                    remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
                utils_secret.clean_up_secrets(remote_virsh_session)
                logging.debug("create secret on target")
                auth_sec_dict.update({"sec_uuid": tpm_sec_uuid})
                dest_tmp_sec_uuid = libvirt.create_secret(auth_sec_dict,
                                                          remote_virsh_dargs)
                logging.debug("tpm sec uuid on target: %s", dest_tmp_sec_uuid)
                if dst_secret_value:
                    if not remote_virsh_session:
                        remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)

                    remote_virsh_session.secret_set_value(
                        dest_tmp_sec_uuid, dst_secret_value, encode=True,
                        debug=True, ignore_status=True)
                    remote_virsh_session.close_session()

            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            for loc in ['source', 'target']:
                session = None
                if loc == 'target':
                    session = remote_session
                if not utils_package.package_install(["swtpm", "swtpm-tools"], session):
                    test.error("Failed to install swtpm packages on {} host."
                               .format(loc))
            remote_session.close()

        # Change the disk of the vm to shared disk and then start VM
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check qemu command line after guest is started
        if qemu_check:
            check_content = qemu_check
            if hpt_resize:
                check_content = "%s%s" % (qemu_check, hpt_resize)
            if htm_state:
                check_content = "%s%s" % (qemu_check, htm_state)
            libvirt.check_qemu_cmd_line(check_content)

        # Check local guest network connection before migration
        vm_session = vm.wait_for_login()
        migration_test.ping_vm(vm, params)

        if check_event_output:
            cmd = "event --loop --all"
            logging.debug("Running virsh command: %s", cmd)
            # Run 'virsh event' on source
            virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                               auto_close=True)
            virsh_session.sendline(cmd)
            # Run 'virsh event' on target
            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            remote_session.sendline("virsh " + cmd)

        if comp_cache_size:
            if (not (int(comp_cache_size) & (int(comp_cache_size)-1)) and
               int(comp_cache_size) > memory.get_page_size()):
                res = virsh.migrate_compcache(vm_name, comp_cache_size,
                                              **virsh_args).stdout_text.strip()
                regx = (r"Compression cache: %.3f M"
                        % (int(comp_cache_size)/(1024*1024)))
                if not re.findall(regx, res):
                    test.fail("Failed to find '%s' in '%s'." % (regx, res))
            else:
                # TODO: non-power-of-2 value or smaller than default pagesize
                test.error("%s is not power of 2 or smaller than "
                           "default pagesize." % comp_cache_size)

        # Preparation for the running guest before migration
        if hpt_resize and hpt_resize != 'disabled':
            trigger_hpt_resize(vm_session)

        if tpm_args:
            if not utils_package.package_install("tpm2-tools", vm_session):
                test.error("Failed to install tpm2-tools in vm")

        if stress_in_vm:
            pkg_name = 'stress'
            logging.debug("Check if stress tool is installed")
            pkg_mgr = utils_package.package_manager(vm_session, pkg_name)
            if not pkg_mgr.is_installed(pkg_name):
                logging.debug("Stress tool will be installed")
                if not pkg_mgr.install():
                    test.error("Package '%s' installation fails" % pkg_name)

            stress_thread = threading.Thread(target=run_stress_in_vm,
                                             args=())
            stress_thread.start()

        # Check maxdowntime before migration
        if check_default_maxdowntime:
            res = virsh.migrate_getmaxdowntime(vm_name,
                                               **virsh_args).stdout.strip()
            if check_default_maxdowntime != res:
                test.fail("Unable to get expected maxdowntime! Expected: {},"
                          "Actual: {}.".format(check_default_maxdowntime, res))
        if maxdowntime_before_mig:
            check_maxdowntime(params)

        if timer_migration:
            source_vm_host_time_diff = time_diff_between_vm_host(localvm=True)

        if extra.count("timeout-postcopy"):
            action_during_mig = check_timeout_postcopy
        if params.get("actions_during_migration"):
            action_during_mig = do_actions_during_migrate
        if extra.count("comp-xbzrle-cache"):
            cache = get_usable_compress_cache(memory.get_page_size())
            extra = "%s %s" % (extra, cache)

        if parallel_cn_nums or extra.count("parallel"):
            if parallel_cn_nums is not None:
                extra = "%s %s" % (extra, str(parallel_cn_nums))
                # The expected number of ESTABLISHED connections is equal to
                # the specified parallel connection number + 1.
                # The default parallel connection number is 2.
                expConnNum = int(parallel_cn_nums) + 1
            else:
                expConnNum = 3

        # For --postcopy enable
        postcopy_options = params.get("postcopy_options")
        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)

        if remove_cache or (cache and cache not in ["none", "directsync"]):
            if not status_error:
                if not (libvirt_version.version_compare(5, 6, 0) and
                   utils_misc.compare_qemu_version(4, 0, 0, False)):
                    extra = "%s %s" % (extra, "--unsafe")
            else:
                if (libvirt_version.version_compare(5, 6, 0) and
                   utils_misc.compare_qemu_version(4, 0, 0, False)):
                    test.cancel("All the cache modes are safe on "
                                "current libvirtd & qemu version,"
                                "skip negative tests.")

        if low_speed:
            control_migrate_speed(int(low_speed))
            if postcopy_options and libvirt_version.version_compare(5, 0, 0):
                control_migrate_speed(int(low_speed), opts=postcopy_options)
        if pause_vm_before_mig:
            suspend_vm(vm)

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        # Execute migration process
        if not asynch_migration:
            mig_result = do_migration(vm, dest_uri, options, extra)
            migration_test.check_result(mig_result, params)
        else:

            logging.debug("vm.connect_uri=%s", vm.connect_uri)
            vms = [vm]
            try:
                migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                            options, thread_timeout=900,
                                            ignore_status=True, virsh_opt=virsh_opt,
                                            func=action_during_mig, extra_opts=extra,
                                            **extra_args)
                mig_result = migration_test.ret
            except exceptions.TestFail as fail_detail:
                test.fail(fail_detail)
            except exceptions.TestCancel as cancel_detail:
                test.cancel(cancel_detail)
            except exceptions.TestError as error_detail:
                test.error(error_detail)
            except Exception as details:
                mig_result = migration_test.ret
                logging.error(details)

        if add_channel:
            # Get the channel device source path of remote guest
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            file_path = tempfile.mktemp(dir=data_dir.get_tmp_dir())
            remote_virsh_session.dumpxml(vm_name, to_file=file_path,
                                         debug=True,
                                         ignore_status=True)
            local_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            local_vmxml.xmltreefile = xml_utils.XMLTreeFile(file_path)
            for elem in local_vmxml.devices.by_device_tag('channel'):
                logging.debug("Found channel device {}".format(elem))
                if elem.type_name == channel_type_name:
                    host_source = elem.source.get('path')
                    logging.debug("Remote guest uses {} for channel device".format(host_source))
                    break
            remote_virsh_session.close_session()
            if not host_source:
                test.fail("Can not find source for %s channel on remote host" % channel_type_name)

            # Prepare to wait for message on remote host from the channel
            cmd_result = remote.run_remote_cmd(cmd_run_in_remote_host % host_source,
                                               cmd_parms,
                                               runner_on_target)

            # Send message from remote guest to the channel file
            remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            vm_ip = vm.get_address(session=remote_session, timeout=480)
            remote_session.close()
            vm_pwd = params.get("password")
            cmd_parms.update({'vm_ip': vm_ip, 'vm_pwd': vm_pwd})
            remote_vm_obj = remote.VMManager(cmd_parms)
            remote_vm_obj.setup_ssh_auth()
            cmd_result = remote_vm_obj.run_command(cmd_run_in_remote_guest_1)
            remote_vm_obj.run_command(cmd_run_in_remote_guest % cmd_result.stdout_text.strip())
            logging.debug("Sending message is done")

            # Check message on remote host from the channel
            remote.run_remote_cmd(cmd_run_in_remote_host_1, cmd_parms, runner_on_target)
            logging.debug("Receiving message is done")
            remote.run_remote_cmd(cmd_run_in_remote_host_2, cmd_parms, runner_on_target)

        if check_complete_job:
            opts = " --completed"
            check_virsh_command_and_option("domjobinfo", opts)
            if extra.count("comp-xbzrle-cache"):
                params.update({'compare_to_value': cache // 1024})
            # The output of domjobinfo with '--completed' option does not
            # contain "Expected downtime:". So update to check
            # "Total downtime". The value of "Total downtime" should be
            # around the expected value of "Expected downtime".
            if params.get("jobinfo_item", "") == "Expected downtime:":
                params.update({"jobinfo_item": "Total downtime:"})
            if timer_migration:
                opts = vm_name + " --completed"
                res = virsh.domjobinfo(opts, **virsh_args)
                if res.exit_status:
                    test.fail("Failed to get domjobinfo --completed: %s"
                              % res.stderr)
                actual_dt = re.findall(r"Total downtime:\s+(\d+)",
                                       res.stdout_text)
                if actual_dt:
                    min_time = eval(params.get("min_total_downtime", "3000"))
                    if int(actual_dt[0]) < min_time:
                        test.fail("The value of 'Total downtime' "
                                  "should be greater than {}s."
                                  .format(int(min_time/1000)))
                else:
                    test.fail("Unable to get value of 'Total downtime' "
                              "in '%s'." % res.stdout_text)
            else:
                check_domjobinfo(params, option=opts)
            if check_domjobinfo_results:
                check_domjobinfo_output(option=opts, is_mig_compelete=True)

        for grep_str in [grep_str_local_log, grep_str_local_log_1]:
            if grep_str:
                libvirt.check_logfile(grep_str, log_file)
        if grep_str_not_in_local_log:
            libvirt.check_logfile(grep_str_not_in_local_log, log_file, False)
        if grep_str_remote_log:
            libvirt.check_logfile(grep_str_remote_log, log_file, True, cmd_parms,
                                  runner_on_target)
        if grep_str_not_in_remote_log:
            libvirt.check_logfile(grep_str_not_in_remote_log, log_file, False,
                                  cmd_parms, runner_on_target)

        if check_event_output:
            if check_log_interval:
                # check the interval of the output in libvirt.log
                # make sure they are not fixed output
                check_interval_not_fixed(grep_str_local_log, log_file)
                server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                       server_user, server_pwd,
                                                       r"[\#\$]\s*$")
                check_interval_not_fixed(grep_str_remote_log, log_file,
                                         session=server_session)
                server_session.close()

            # Check events
            expectedEventSrc = params.get('expectedEventSrc')
            if expectedEventSrc:
                source_output = virsh_session.get_stripped_output()
                check_output(source_output, eval(expectedEventSrc))

            expectedEventTarget = params.get('expectedEventTarget')
            if expectedEventTarget:
                target_output = remote_session.get_stripped_output()
                check_output(target_output, eval(expectedEventTarget))

        if xml_check_after_mig:
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            target_guest_dumpxml = (
                remote_virsh_session.dumpxml(vm_name,
                                             debug=True,
                                             ignore_status=True).stdout_text.strip())
            if hpt_resize:
                check_str = hpt_resize
            elif htm_state:
                check_str = htm_state
            elif vcpu_num:
                check_str = vcpu_num

            if hpt_resize or htm_state:
                xml_check_after_mig = "%s'%s'" % (xml_check_after_mig, check_str)
            elif vcpu_num:
                xml_check_after_mig = ("%s%s</vcpu>"
                                       % (xml_check_after_mig, check_str))
            if hpt_resize or htm_state or vcpu_num:
                if not re.search(xml_check_after_mig, target_guest_dumpxml):
                    remote_virsh_session.close_session()
                    test.fail("Fail to search '%s' in target guest XML:\n%s"
                              % (xml_check_after_mig, target_guest_dumpxml))

            if contrl_index:
                all_ctrls = re.findall(xml_check_after_mig, target_guest_dumpxml)
                if len(all_ctrls) != int(contrl_index) + 1:
                    remote_virsh_session.close_session()
                    test.fail("%s pci-root controllers are expected in guest XML, "
                              "but found %s" % (int(contrl_index) + 1, len(all_ctrls)))
            remote_virsh_session.close_session()

        if int(mig_result.exit_status) == 0:
            if cmd_in_vm_after_migration:
                vm.connect_uri = dest_uri
                vm_session_after_mig = vm.wait_for_serial_login(timeout=240)
                vm_session_after_mig.cmd(cmd_in_vm_after_migration)
                vm_session_after_mig.close()
                vm.connect_uri = bk_uri

        if timer_migration:
            target_vm_host_time_diff = time_diff_between_vm_host(localvm=False)
            if abs(target_vm_host_time_diff - source_vm_host_time_diff) > 1:
                test.fail("The difference of target_vm_host_time_diff and "
                          "source_vm_host_time_diff "
                          "should not more than 1 second")
        if params.get("actions_after_migration"):
            do_actions_after_migrate(params)

        if migrate_vm_back:
            ssh_connection = utils_conn.SSHConnection(server_ip=client_ip,
                                                      server_pwd=client_pwd,
                                                      client_ip=server_ip,
                                                      client_pwd=server_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()

            # Pre migration setup for local machine
            src_full_uri = libvirt_vm.complete_uri(
                        params.get("migrate_source_host"))
            migration_test.migrate_pre_setup(src_full_uri, params)
            remove_dict = {"do_search": ('{"%s": "ssh:/"}' % src_full_uri)}
            remote_libvirt_file = libvirt_config\
                .remove_key_for_modular_daemon(remove_dict, remote_dargs)

            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              options, src_full_uri)
            logging.debug("Start migration: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)
            if cmd_result.exit_status:
                destroy_cmd = "virsh destroy %s" % vm_name
                remote.run_remote_cmd(destroy_cmd, params, runner_on_target,
                                      ignore_status=False)
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, cmd_result))

    finally:
        logging.debug("Recover test environment")
        vm.connect_uri = bk_uri
        try:
            # Remove firewall rule if needed
            if break_network_connection and 'firewall_rule' in locals():
                if use_firewall_cmd:
                    logging.debug("cleanup firewall rule via firewall-cmd.")
                    direct_rules = firewall_cmd.get(key="all-rules",
                                                    is_direct=True,
                                                    zone=None)
                    cmdRes = re.findall(firewall_rule, direct_rules)
                    if len(cmdRes):
                        firewall_cmd.remove_direct_rule(firewall_rule)
                else:
                    Iptables.setup_or_cleanup_iptables_rules(firewall_rule,
                                                             cleanup=True)

            # Clean VM on destination
            migration_test.cleanup_vm(vm, dest_uri)

            logging.info("Recover VM XML configration")
            orig_config_xml.sync()
            logging.debug("The current VM XML:\n%s", orig_config_xml.xmltreefile)

            # cleanup secret uuid
            if tpm_sec_uuid:
                virsh.secret_undefine(tpm_sec_uuid, debug=True,
                                      ignore_status=True)

            if dest_tmp_sec_uuid:
                cmd = "virsh secret-undefine %s" % dest_tmp_sec_uuid
                remote.run_remote_cmd(cmd, params, runner_on_target)

            if remote_virsh_session:
                remote_virsh_session.close_session()

            if remote_session:
                remote_session.close()

            # Clean up of pre migration setup for local machine
            if migrate_vm_back:
                if 'ssh_connection' in locals():
                    ssh_connection.auto_recover = True
                if 'src_full_uri' in locals():
                    migration_test.migrate_pre_setup(src_full_uri, params,
                                                     cleanup=True)

            # Delete files on target
            # Killing qemu process on target may lead a problem like
            # qemu process becomes a zombie process whose ppid is 1.
            # As a workaround, have to remove the files under
            # /var/run/libvirt/qemu to make libvirt work.
            if vm.is_qemu():
                dest_pid_files = os.path.join("/var/run/libvirt/qemu",
                                              vm_name + '*')
                cmd = "rm -f %s" % dest_pid_files
                logging.debug("Delete remote pid files '%s'", dest_pid_files)
                remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)

            if extra.count("--tls") and not disable_verify_peer:
                logging.debug("Recover the qemu configuration")
                libvirt.customize_libvirt_config(None,
                                                 config_type="qemu",
                                                 remote_host=True,
                                                 extra_params=params,
                                                 is_recover=True,
                                                 config_object=qemu_conf)

            local_conf_obj_list.reverse()
            for conf in local_conf_obj_list:
                logging.info("Recover the conf files on local host")
                libvirt.customize_libvirt_config(None,
                                                 remote_host=False,
                                                 is_recover=True,
                                                 config_object=conf)

            remote_conf_obj_list.reverse()
            for conf in remote_conf_obj_list:
                logging.info("Recover the conf files on remote host")
                del conf

            if src_libvirt_file:
                src_libvirt_file.restore()
            if remote_libvirt_file:
                del remote_libvirt_file

            logging.info("Remove local NFS image")
            source_file = params.get("source_file")
            if source_file:
                libvirt.delete_local_disk("file", path=source_file)

            if objs_list:
                for obj in objs_list:
                    logging.debug("Clean up local objs")
                    obj.__del__()

        except Exception as exception_detail:
            if (not test_exception and not is_TestError and
               not is_TestFail and not is_TestCancel):
                raise exception_detail
            else:
                # if any of above exceptions has been raised, only print
                # error log here to avoid of hiding the original issue
                logging.error(exception_detail)
