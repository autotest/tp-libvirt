import os
import re
import copy
import ast
import logging

from avocado.utils import process
from avocado.utils import distro
from avocado.utils.software_manager import SoftwareManager

from virttest import libvirt_cgroup
from virttest import virsh
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import cpu
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


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
    # Install cgroup utils
    cgutils = "libcgroup-tools"
    if distro.detect().name == 'Ubuntu':
        cgutils = "cgroup-tools"
    sm = SoftwareManager()
    if not sm.check_installed(cgutils) and not sm.install(cgutils):
        test.cancel("cgroup utils package install failed")
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
            config = utils_config.LibvirtdConfig()
            log_outputs = "1:file:%s" % config_path
            config.log_outputs = log_outputs
            config.log_level = 1
            config.log_filters = "1:json 1:libvirt 1:qemu 1:monitor 3:remote 4:event"

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

        # Start VM
        logging.info("Start VM with vcpu hotpluggable and order...")
        ret = virsh.start(vm_name, ignore_status=True)

        if err_msg:
            libvirt.check_result(ret, err_msg)
        else:
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
            cmd = ("ps -ef| grep %s| grep 'maxcpus=%s'" % (vm_name, vcpus_max))
            ret = process.run(cmd, ignore_status=False, shell=True)
            if ret.exit_status != 0:
                logging.error("Maxcpus in QEMU command line is wrong!")

            # Check libvirtd log
            if config_libvirtd:
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
            if len(max_list) != 2:
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
            if len(vcpu_lines) != vcpus_crt:
                test.fail("vcpuinfo is not correct.")

            # Check cpu in guest
            if not cpu.check_if_vm_vcpu_match(vcpus_crt, vm):
                test.fail("cpu number in VM is not correct, it should be %s cpus" % vcpus_crt)

            # Check VM xml change for cold-plug/cold-unplug
            if config_vcpus:
                inactive_xml = virsh.dumpxml(vm_name, "--inactive").stdout.strip()
                crt_vcpus_xml = re.findall(r"vcpu.*current=.%s.*" %
                                           config_vcpus, inactive_xml)
                logging.info("dumpxml --inactive xml: \n %s", crt_vcpus_xml)
                if len(crt_vcpus_xml) != 1:
                    test.fail("Dumpxml with --inactive,"
                              "the vcpu current is not correct.")

            # Restart libvirtd
            libvirtd.restart()

            # Recheck VM xml
            re_dump_xml = virsh.dumpxml(vm_name).stdout.strip()
            re_vcpu_items = re.findall(r"vcpu.*", re_dump_xml)
            if vcpu_items != re_vcpu_items:
                test.fail("After restarting libvirtd,"
                          "VM xml changed unexpectedly.")

            # Check cgroup info
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
        # Recover libvirtd configration
        if config_libvirtd:
            config.restore()
            if os.path.exists(config_path):
                os.remove(config_path)

        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
