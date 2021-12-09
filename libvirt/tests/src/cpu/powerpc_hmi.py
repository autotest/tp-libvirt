import os
import time
import logging

from avocado.utils import cpu
from avocado.utils.software_manager import SoftwareManager
from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_test


def run(test, params, env):
    """
    Test different hmi injections with guest

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def set_condn(action, recover=False):
        """
        Set/reset guest state/action
        :param action: Guest state change/action
        :param recover: whether to recover given state default: False
        """
        if not recover:
            if action == "pin_vcpu":
                for i in range(cur_vcpu):
                    virsh.vcpupin(vm_name, i, hmi_cpu, "--live",
                                  ignore_status=False, debug=True)
                    virsh.emulatorpin(vm_name,  hmi_cpu, "live",
                                      ignore_status=False, debug=True)
            elif action == "filetrans":
                utils_test.run_file_transfer(test, params, env)
            elif action == "save":
                save_file = os.path.join(data_dir.get_tmp_dir(),
                                         vm_name + ".save")
                result = virsh.save(vm_name, save_file, ignore_status=True,
                                    debug=True)
                utils_test.libvirt.check_exit_status(result)
                time.sleep(10)
                if os.path.exists(save_file):
                    result = virsh.restore(save_file, ignore_status=True,
                                           debug=True)
                    utils_test.libvirt.check_exit_status(result)
                    os.remove(save_file)
            elif action == "suspend":
                result = virsh.suspend(vm_name, ignore_status=True, debug=True)
                utils_test.libvirt.check_exit_status(result)
                time.sleep(10)
                result = virsh.resume(vm_name, ignore_status=True, debug=True)
                utils_test.libvirt.check_exit_status(result)
        return

    host_version = params.get("host_version")
    guest_version = params.get("guest_version", "")
    max_vcpu = int(params.get("ppchmi_vcpu_max", '1'))
    cur_vcpu = int(params.get("ppchmi_vcpu_cur", "1"))
    cores = int(params.get("ppchmi_cores", '1'))
    sockets = int(params.get("ppchmi_sockets", '1'))
    threads = int(params.get("ppchmi_threads", '1'))
    status_error = "yes" == params.get("status_error", "no")
    condition = params.get("condn", "")
    inject_code = params.get("inject_code", "")
    scom_base = params.get("scom_base", "")
    hmi_name = params.get("hmi_name", "")
    hmi_iterations = int(params.get("hmi_iterations", 1))

    if host_version not in cpu.get_cpu_arch():
        test.cancel("Unsupported Host cpu version")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    sm = SoftwareManager()
    if not sm.check_installed("opal-utils") and not sm.install("opal-utils"):
        test.cancel("opal-utils package install failed")
    cpus_list = cpu.cpu_online_list()
    cpu_idle_state = cpu.get_cpuidle_state()
    cpu.set_cpuidle_state()
    # Lets use second available host cpu
    hmi_cpu = cpus_list[1]
    pir = int(open('/sys/devices/system/cpu/cpu%s/pir' % hmi_cpu).read().strip(), 16)
    if host_version == 'power9':
        coreid = (((pir) >> 2) & 0x3f)
        nodeid = (((pir) >> 8) & 0x7f) & 0xf
        hmi_scom_addr = hex(((coreid & 0x1f + 0x20) << 24) | int(scom_base, 16))
    if host_version == 'power8':
        coreid = (((pir) >> 3) & 0xf)
        nodeid = (((pir) >> 7) & 0x3f)
        hmi_scom_addr = hex(((coreid & 0xf) << 24) | int(scom_base, 16))
    hmi_cmd = "putscom -c %s %s %s" % (nodeid, hmi_scom_addr, inject_code)

    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    org_xml = vmxml.copy()
    # Destroy the vm
    vm.destroy()
    try:
        session = None
        bgt = None
        libvirt_xml.VMXML.set_vm_vcpus(vm_name, max_vcpu, cur_vcpu,
                                       sockets=sockets, cores=cores,
                                       threads=threads, add_topology=True)
        if guest_version:
            libvirt_xml.VMXML.set_cpu_mode(vm_name, model=guest_version)
        vm.start()
        # Lets clear host and guest dmesg
        process.system("dmesg -C", verbose=False)
        session = vm.wait_for_login()
        session.cmd("dmesg -C")

        # Set condn
        if "vcpupin" in condition:
            set_condn("pin_vcpu")
        if "stress" in condition:
            utils_test.load_stress("stress_in_vms", params=params, vms=[vm])
        if "save" in condition:
            set_condn("save")
        if "suspend" in condition:
            set_condn("suspend")

        # hmi inject
        logging.debug("Injecting %s HMI on cpu %s", hmi_name, hmi_cpu)
        logging.debug("HMI Command: %s", hmi_cmd)
        process.run(hmi_cmd)

        # Check host and guest dmesg
        host_dmesg = process.run("dmesg -c", verbose=False).stdout_text
        guest_dmesg = session.cmd_output("dmesg")
        if "Unrecovered" in host_dmesg:
            test.fail("Unrecovered host hmi\n%s", host_dmesg)
        else:
            logging.debug("Host dmesg: %s", host_dmesg)
        logging.debug("Guest dmesg: %s", guest_dmesg)
        if "save" in condition:
            set_condn("save")
        if "suspend" in condition:
            set_condn("suspend")
    finally:
        if "stress" in condition:
            utils_test.unload_stress("stress_in_vms", params=params, vms=[vm])
        if session:
            session.close()
        org_xml.sync()
        cpu.set_cpuidle_state(setstate=cpu_idle_state)
