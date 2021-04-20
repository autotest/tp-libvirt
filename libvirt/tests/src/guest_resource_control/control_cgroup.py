import logging
import os
import re
import time

from virttest import libvirt_xml
from virttest import utils_disk
from virttest import utils_libvirtd
from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_cgroup import CgroupTest

from avocado.utils import process


def run(test, params, env):
    """
    Cgroup related cases in function 'guest resource control'

    1) Positive testing
    1.1) Set vm's cpu/io/mem cgroup params with valid values
    1.2) Check the values in virsh_output/cgroup_files/virsh_cmds amd make
         sure they are consistent
    2) Negative testing
    2.1) Set vm's cpu/io/mem cgroup params with invalid values
    2.2) Check libvirt or cgroup will have reasonable error handling
    """
    def get_host_first_disk():
        """
        Get the first block device on host
        """
        first_disk = ""
        disks = utils_disk.get_parts_list()
        for disk in disks:
            pattern = re.compile('[0-9]+')
            if not pattern.findall(disk):
                first_disk = disk
                break
        return first_disk

    def prepare_virsh_cmd(vm_name, virsh_cmd, virsh_cmd_param,
                          virsh_cmd_param_value, virsh_cmd_options=None):
        """
        Prepare a virsh cmd line to be executed

        :param vm_name: VM's name
        :param virsh_cmd: The virsh cmd name
        :param virsh_cmd_param: The params used in the cmd line
        :param virsh_cmd_param_value: The values assigned to the params
        :param virsh_cmd_options: Other options such as --config --live
        :return: The whole cmd line of the virsh cmd
        """
        if not vm_name or not virsh_cmd:
            test.fail("At least VM name and virsh cmd name should be provided "
                      "to prepare the virsh cmd line.")
        if virsh_cmd not in ["blkiotune", "memtune", "schedinfo"]:
            test.fail("Unsupported virsh cmd '%s' provided." % virsh_cmd)
        cmd_params = virsh_cmd_param.strip().split(";")
        cmd_values = virsh_cmd_param_value.strip().split(";")
        if len(cmd_params) == 0 or len(cmd_params) != len(cmd_values):
            test.fail("Insufficient virsh cmd params or values.")
        cmd = "virsh " + virsh_cmd + " " + vm_name
        if virsh_cmd in ["blkiotune", "memtune"]:
            for i in range(len(cmd_params)):
                cmd += " " + cmd_params[i] + " " + cmd_values[i]
        elif virsh_cmd in ["schedinfo"]:
            cmd += " --set"
            for i in range(len(cmd_params)):
                cmd += " " + cmd_params[i] + "=" + cmd_values[i]
        if virsh_cmd_options:
            cmd += " " + virsh_cmd_options
        return cmd

    def get_virsh_input_dict(virsh_params, virsh_values):
        """
        Transform the virsh cmd params and values to a dict

        :param virsh_params: The params of a virsh cmd
        :param virsh_values: The values assigned to the params

        :return: The dict containing the params/values info
        """
        input_dict = {}
        param_list = virsh_params.strip().split(";")
        value_list = virsh_values.strip().split(";")
        if len(param_list) == 0 or len(param_list) != len(value_list):
            logging.error("Wrong param-value pairs provided.")
            return None
        for i in range(len(param_list)):
            input_dict[param_list[i].lstrip("-").replace("-", "_")] = value_list[i]
        return input_dict

    def is_subset_dict(input_dict, compared_dict):
        """
        To check if the input_dict is equal to or belong to the compared_dict

        :param input_dict: The input dict
        :param compared_dict: The baseline dict

        :return: True means the input_dict is equal to or belong to the
                 compared_dict
                 False means not
        """
        try:
            for key, value in list(input_dict.items()):
                if type(value) is dict:
                    result = is_subset_dict(value, compared_dict[key])
                    assert result
                else:
                    assert compared_dict[key] == value
                    result = True
        except (AssertionError, KeyError):
            result = False
        return result

    def add_iothread(vm, thread_id="1"):
        """
        Add a iothread to a vm process

        :param vm: The vm to add iothread
        :param thread_id: The iothread id to be used
        """
        if vm.is_alive():
            virsh.iothreadadd(vm.name, thread_id)
        else:
            logging.debug("VM is not alive, cannot add iothread")

    def do_extra_operations(operations="daemon-reload"):
        """
        Do some extra operation after setting cgroup value

        :param operation: The operation to be executed
        """
        if "daemon-reload" in operations:
            process.run("systemctl daemon-reload",
                        ignore_status=False, shell=True)
            logging.debug("daemons reloaded after setting cgroup")
        if "restart-libvirtd" in operations:
            utils_libvirtd.libvirtd_restart()
            logging.debug("libvirtd restarted after setting cgroup")
        # Sleep 2 seconds to make sure daemons are reloaded or restarted
        time.sleep(2)

    # Run test case
    host_disk = get_host_first_disk()
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm", "yes")
    status_error = "yes" == params.get("status_error", "no")
    virsh_cmd = params.get("virsh_cmd")
    virsh_cmd_param = params.get("virsh_cmd_param", "")
    virsh_cmd_param = virsh_cmd_param.replace("sda", host_disk)
    virsh_cmd_param_value = params.get("virsh_cmd_param_value", "")
    virsh_cmd_param_value = virsh_cmd_param_value.replace("sda", host_disk)
    virsh_cmd_options = params.get("virsh_cmd_options", "")
    extra_operations = params.get("extra_operations")
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    cgtest = CgroupTest(None)
    if cgtest.is_cgroup_v2_enabled():
        logging.info("The case executed on cgroup v2 environment.")
        # Following is to deal the situation when realtime task existing.
        # In this situation, cpu controller cannot be enabled.
        # FYI: https://bugzilla.redhat.com/show_bug.cgi?id=1513930#c21
        if "schedinfo" in virsh_cmd:
            with open('/proc/mounts', 'r') as mnts:
                cg_mount_point = re.findall(r"\s(\S*cgroup)\s", mnts.read())[0]
            cg_subtree_file = os.path.join(cg_mount_point,
                                           "cgroup.subtree_control")
            with open(cg_subtree_file, 'r') as cg_subtree:
                if "cpu" not in cg_subtree.read().replace("cpuset", ""):
                    test.cancel("CPU controller not mounted. This is a "
                                "limitation of cgroup v2 when realtime task "
                                "existing on host.")
    else:
        logging.info("The case executed on cgroup v1 environment.")

    # Make sure vm is down if start not requested
    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    # Make sure host os using bfq or cfq scheduler when test blkiotune cases
    if virsh_cmd in ['blkiotune']:
        scheduler_file = "/sys/block/%s/queue/scheduler" % host_disk
        cmd = "cat %s" % scheduler_file
        iosche = process.run(cmd, shell=True).stdout_text
        logging.debug("iosche value is:%s", iosche)
        oldmode = re.findall(r"\[(.*?)\]", iosche)[0]
        cfq_enabled = False
        with open(scheduler_file, 'w') as scf:
            if 'cfq' in iosche:
                scf.write('cfq')
                cfq_enabled = True
            elif 'bfq' in iosche:
                scf.write('bfq')
            else:
                test.fail("Unknown scheduler in %s" % scheduler_file)
        if not cfq_enabled and "--device-weights" in virsh_cmd_param:
            logging.info("cfq scheduler cannot be enabled in the host, "
                         "so 'blkiotune --device-weights' is not supported. "
                         "An error will be generated.")
            status_error = True

    try:
        if start_vm == "yes" and not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
        virsh_cmd_line = prepare_virsh_cmd(vm_name, virsh_cmd,
                                           virsh_cmd_param,
                                           virsh_cmd_param_value,
                                           virsh_cmd_options)
        logging.debug("The virsh cmd about to run is: %s", virsh_cmd_line)
        if "iothread" in virsh_cmd_line:
            add_iothread(vm)
        cmd_result = process.run(virsh_cmd_line, ignore_status=True, shell=True)
        if cmd_result.exit_status:
            if status_error:
                logging.debug("Expected error happens")
            else:
                test.fail("Failed to run: %s" % virsh_cmd_line)
        else:
            if "--config" in virsh_cmd_options:
                try:
                    vm.start()
                    vm.wait_for_login().close()
                    if "iothread" in virsh_cmd_line:
                        add_iothread(vm)
                except virt_vm.VMStartError as detail:
                    if not status_error:
                        test.fail("VM failed to start")
                    else:
                        logging.debug("VM failed to start as expected")
            if vm.is_alive():
                if extra_operations:
                    do_extra_operations(extra_operations)
                vm_pid = vm.get_pid()
                logging.debug("vm's pid is: %s", vm_pid)
                cgtest = CgroupTest(vm_pid)
                virsh_input_dict = get_virsh_input_dict(
                    virsh_cmd_param, virsh_cmd_param_value)
                virsh_input_info = cgtest.get_standardized_virsh_info(
                    virsh_cmd, virsh_input_dict)
                virsh_output_info = cgtest.get_standardized_virsh_output_by_name(
                    vm_name, virsh_cmd)
                cgroup_info = cgtest.get_standardized_cgroup_info(virsh_cmd)
                # Following are testing blkiotune
                if virsh_cmd == "blkiotune":
                    logging.debug("blkiotune: The input info is: %s\n"
                                  "The ouput info is: %s\n"
                                  "The cgroup info is:%s",
                                  virsh_input_info, virsh_output_info,
                                  cgroup_info)
                    if not (is_subset_dict(virsh_input_info, virsh_output_info)
                            and is_subset_dict(virsh_output_info, cgroup_info)):
                        test.fail("blkiotune checking failed.")
                # Following are testing memtune
                elif virsh_cmd == "memtune":
                    logging.debug("memtune: The input info is: %s\n"
                                  "The ouput info is: %s\n"
                                  "The cgroup info is:%s",
                                  virsh_input_info, virsh_output_info,
                                  cgroup_info)
                    if not(is_subset_dict(virsh_output_info, cgroup_info)
                            and is_subset_dict(virsh_input_info, virsh_output_info)):
                        test.fail("memtune checking failed.")
                # Following are testing schedinfo
                elif virsh_cmd == "schedinfo":
                    logging.debug("schedinfo: The input info is: %s\n"
                                  "The ouput info is: %s\n"
                                  "The cgroup info is:%s",
                                  virsh_input_info, virsh_output_info,
                                  cgroup_info)
                    if not(is_subset_dict(cgroup_info, virsh_output_info)
                            and is_subset_dict(virsh_input_info, virsh_output_info)):
                        test.fail("schedinfo checking failed.")
            else:
                logging.info("VM cannot start as expected.")
    finally:
        # Restore guest
        logging.debug("Start cleanup job...")
        vmxml_backup.sync()
        # Recover scheduler setting
        if 'oldmode' in locals():
            with open(scheduler_file, 'w') as scf:
                scf.write(oldmode)
