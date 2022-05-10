import os
import re
import copy
import ast
import logging as log

from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import virsh
from virttest import utils_libvirtd
from virttest import cpu
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_vcpu_after_plug_unplug(test, vm_name, config_vcpus, option='--inactive'):
    re_dump_xml = virsh.dumpxml(vm_name, option).stdout.strip()
    # Check <vcpu current='number'> xx </vcpu>
    crt_vcpus = re.findall(r"vcpu.*current=.%s.*" % config_vcpus,
                           re_dump_xml)
    logging.info("dumpxml %s xml: \n %s", option, crt_vcpus)
    if len(crt_vcpus) != 1:
        test.fail("Dumpxml with {},"
                  "the vcpu current is not correct.".format(option))
    # Check <vcpu id='x' enabled='yes' .../> number should be correct
    vcpu_enabled_list = re.findall(r"vcpu.*enabled='yes'", re_dump_xml)
    if vcpu_enabled_list:
        if len(vcpu_enabled_list) != int(config_vcpus):
            test.fail("The enabled vcpu number is expected to be {}, "
                      "but found {}".format(config_vcpus,
                                            len(vcpu_enabled_list)))
    else:
        test.error("No vcpu is enabled")
    # Check  <vcpu id='x' enabled='xx' ... order='x'/> should disappear
    vcpu_order_list = re.findall(r"vcpu.*order=.*", re_dump_xml)
    if vcpu_order_list:
        test.fail("vcpu order info should be cleared, "
                  "but found {}".format(vcpu_order_list))


def check_vm_exist(test, vm_name, state):

    dom_output = virsh.dom_list("--all", debug=True).stdout.strip()
    search_res = re.search(r"-.*%s.*%s" % (vm_name, state), dom_output)
    if not search_res:
        test.fail("VM '{}' with the state '{}' is not "
                  "found".format(vm_name, state))
    else:
        logging.debug("VM '{}' with the state '{}' is "
                      "found".format(vm_name, state))


def run(test, params, env):
    """
    Test vcpu hotpluggable item in xml

    1. Set the libvirtd log filter/level/file
    2. Restart libvirtd
    3. Start vm by xml with vcpu hotpluggable
    4. Check the qemu command line
    5. Check the libvirtd log
    6. Restart libvrtd
    7. Check the vm xml
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vcpus_placement = params.get("vcpus_placement", "static")
    vcpus_crt = int(params.get("vcpus_current", "4"))
    vcpus_max = int(params.get("vcpus_max", "8"))
    vcpus_enabled = params.get("vcpus_enabled", "")
    vcpus_hotplug = params.get("vcpus_hotpluggable", "")
    vcpus_order = params.get("vcpus_order")
    err_msg = params.get("err_msg", "")
    config_libvirtd = params.get("config_libvirtd", "yes") == "yes"
    log_file = params.get("log_file", "libvirtd.log")
    live_vcpus = params.get("set_live_vcpus", "")
    config_vcpus = params.get("set_config_vcpus", "")
    enable_vcpu = params.get("set_enable_vcpu", "")
    disable_vcpu = params.get("set_disable_vcpu", "")
    start_vm_after_config = params.get('start_vm_after_config', 'yes') == 'yes'

    # Backup domain XML
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        # Configure libvirtd log
        if config_libvirtd:
            config_path = os.path.join(data_dir.get_tmp_dir(), log_file)
            with open(config_path, 'a') as f:
                pass
            daemon_conf_dict = {"log_level": "1",
                                "log_filters": "\"1:json 1:libvirt 1:qemu 1:monitor 3:remote 4:event\"",
                                "log_outputs": "\"1:file:{}\"".format(config_path)}
            daemon_conf = libvirt.customize_libvirt_config(daemon_conf_dict)

            # Restart libvirtd to make the changes take effect in libvirt
            libvirtd.restart()

        # Set vcpu: placement,current,max vcpu
        vmxml.placement = vcpus_placement
        vmxml.vcpu = vcpus_max
        vmxml.current_vcpu = vcpus_crt
        del vmxml.cpuset

        # Create vcpu xml with vcpu hotpluggable and order
        vcpu_list = []
        vcpu = {}
        en_list = vcpus_enabled.split(",")
        hotplug_list = vcpus_hotplug.split(",")
        order_dict = ast.literal_eval(vcpus_order)

        for vcpu_id in range(vcpus_max):
            vcpu['id'] = str(vcpu_id)
            if str(vcpu_id) in en_list:
                vcpu['enabled'] = 'yes'
                if str(vcpu_id) in order_dict:
                    vcpu['order'] = order_dict[str(vcpu_id)]
            else:
                vcpu['enabled'] = 'no'
            if str(vcpu_id) in hotplug_list:
                vcpu['hotpluggable'] = 'yes'
            else:
                vcpu['hotpluggable'] = 'no'
            vcpu_list.append(copy.copy(vcpu))
            vcpu = {}

        vcpus_xml = vm_xml.VMVCPUSXML()
        vcpus_xml.vcpu = vcpu_list

        vmxml.vcpus = vcpus_xml

        # Remove influence from topology setting
        try:
            logging.info('Remove influence from topology setting')
            cpuxml = vmxml.cpu
            del cpuxml.topology
            vmxml.cpu = cpuxml
        except Exception as e:
            pass

        vmxml.sync()
        logging.debug("Before starting, VM xml:"
                      "\n%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
        # Start VM
        if start_vm_after_config:
            logging.info("Start VM with vcpu hotpluggable and order...")
            ret = virsh.start(vm_name, ignore_status=True)

        if err_msg:
            libvirt.check_result(ret, err_msg)
        else:
            if start_vm_after_config:
                # Wait for domain
                vm.wait_for_login()

            if enable_vcpu:
                ret = virsh.setvcpu(vm_name, enable_vcpu, "--enable",
                                    ignore_status=False, debug=True)
                vcpus_crt += 1
            if disable_vcpu:
                ret = virsh.setvcpu(vm_name, disable_vcpu, "--disable",
                                    ingnore_status=False, debug=True)
                vcpus_crt -= 1
            if live_vcpus:
                ret = virsh.setvcpus(vm_name, live_vcpus, ignore_status=False,
                                     debug=True)
                vcpus_crt = int(live_vcpus)
            if config_vcpus:
                ret = virsh.setvcpus(vm_name, config_vcpus, "--config",
                                     ignore_status=False, debug=True)

            # Check QEMU command line
            if start_vm_after_config:
                cmd = ("ps -ef| grep %s| grep 'maxcpus=%s'" % (vm_name, vcpus_max))
                ret = process.run(cmd, ignore_status=False, shell=True)
                if ret.exit_status != 0:
                    logging.error("Maxcpus in QEMU command line is wrong!")

            # Check libvirtd log
            if config_libvirtd and start_vm_after_config:
                for vcpu in vcpu_list:
                    if vcpu['enabled'] == 'yes' and vcpu['hotpluggable'] == 'yes':
                        cmd = ("cat %s| grep device_add| grep qemuMonitorIOWrite"
                               "| grep 'vcpu%s'" % (config_path, vcpu['id']))
                        ret = process.run(cmd, ignore_status=False, shell=True)
                        if ret.exit_status != 0:
                            logging.error("Failed to find lines about enabled vcpu%s"
                                          "in libvirtd log.", vcpu['id'])

            # Dumpxml
            dump_xml = virsh.dumpxml(vm_name).stdout.strip()
            vcpu_items = re.findall(r"vcpu.*", dump_xml)

            # Check guest vcpu count
            ret = virsh.vcpucount(vm_name, ignore_status=True, debug=True)
            output = ret.stdout.strip()
            max_list = re.findall(r"maximum.*[config|live].*%s\n" % vcpus_max, output)
            expect_num = 2 if start_vm_after_config else 1
            if len(max_list) != expect_num:
                test.fail("vcpucount maximum info is not correct.")

            if live_vcpus:
                crt_live_list = re.findall(r"current.*live.*%s" % live_vcpus, output)
                logging.info("vcpucount crt_live_list: \n %s", crt_live_list)
                if len(crt_live_list) != 1:
                    test.fail("vcpucount: current live info is not correct.")
            elif config_vcpus:
                crt_cfg_list = re.findall(r"current.*config.*%s" % config_vcpus, output)
                logging.info("vcpucount crt_cfg_list: \n %s", crt_cfg_list)
                if len(crt_cfg_list) != 1:
                    test.fail("vcpucount: current config info is not correct.")
            else:
                crt_live_list = re.findall(r"current.*live.*%s" % vcpus_crt, output)
                logging.info("vcpucount crt_live_list: \n %s", crt_live_list)
                if len(crt_live_list) != 1:
                    test.fail("vcpucount: current info is not correct.")

            # Check guest vcpu info
            ret = virsh.vcpuinfo(vm_name, ignore_status=True, debug=True)
            output = ret.stdout.strip()
            vcpu_lines = re.findall(r"VCPU:.*\n", output)
            logging.info("vcpuinfo vcpu_lines: \n %s", vcpu_lines)
            expect_num = vcpus_crt if start_vm_after_config else int(config_vcpus)
            if len(vcpu_lines) != expect_num:
                test.fail("vcpuinfo is not correct.")

            # Check cpu in guest
            if start_vm_after_config and not cpu.check_if_vm_vcpu_match(vcpus_crt, vm):
                test.fail("cpu number in VM is not correct, it should be %s cpus" % vcpus_crt)

            # Check VM xml change for cold-plug/cold-unplug
            if config_vcpus:
                check_vcpu_after_plug_unplug(test, vm_name, config_vcpus)

            # Restart libvirtd
            libvirtd.restart()
            if config_vcpus and not start_vm_after_config:
                check_vm_exist(test, vm_name, 'shut off')
            # Recheck VM xml
            re_dump_xml = virsh.dumpxml(vm_name).stdout.strip()
            re_vcpu_items = re.findall(r"vcpu.*", re_dump_xml)
            if vcpu_items != re_vcpu_items:
                test.fail("After restarting libvirtd,"
                          "VM xml changed unexpectedly.")

            # Check cgroup info
            if start_vm_after_config:
                en_vcpu_list = re.findall(r"vcpu.*enabled=.yes.*", re_dump_xml)
                for vcpu_sn in range(len(en_vcpu_list)):
                    vcpu_id = en_vcpu_list[vcpu_sn].split("=")[1].split()[0].strip('\'')
                    cg_obj = libvirt_cgroup.CgroupTest(vm.get_pid())
                    cg_path = cg_obj.get_cgroup_path("cpuset")
                    if cg_obj.is_cgroup_v2_enabled():
                        vcpu_path = os.path.join(cg_path, "vcpu%s" % vcpu_id)
                    else:
                        vcpu_path = os.path.join(cg_path, "../vcpu%s" % vcpu_id)
                    if not os.path.exists(vcpu_path):
                        test.fail("Failed to find the enabled vcpu{} in {}."
                                  .format(vcpu_id, cg_path))
    finally:
        # Recover libvirtd configuration
        if config_libvirtd and 'daemon_conf' in locals():
            libvirt.customize_libvirt_config(None, remote_host=False,
                                             is_recover=True,
                                             config_object=daemon_conf)

        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
