import os
import time

from avocado.utils import cpu

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_misc
from virttest import utils_test


def run(test, params, env):
    """
    Different cpu compat mode scenario tests

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def check_feature(vm, feature="", vcpu=0):
        """
        Checks the given feature is present
        :param vm: VM Name
        :param feature: feature to be verified
        :param vcpu: vcpu number to pin guest test
        :return: true on success, test fail on failure
        """
        session = vm.wait_for_login()
        if 'power8' in feature:
            cmd = 'lscpu|grep -i "Model name:.*power8"'
        elif 'xive' in feature:
            # remove -v once guest xive support is available
            # right now power9 guest supports only xics
            cmd = "grep -v xive /sys/firmware/devicetree/base/interrupt-*/compatible"
        elif 'xics' in feature:
            cmd = "grep -v xive /sys/firmware/devicetree/base/interrupt-*/compatible"
        elif 'power9' in feature:
            cmd = 'lscpu|grep -i "Model name:.*power9"'
        elif 'hpt' in feature:
            cmd = 'grep "MMU.*: Hash" /proc/cpuinfo'
        elif 'rpt' in feature:
            cmd = 'grep "MMU.*: Radix" /proc/cpuinfo'
        elif 'isa' in feature:
            cmd = "echo 'int main(){asm volatile (\".long 0x7c0005e6\");"
            cmd += "return 0;}' > ~/a.c;cc ~/a.c;taskset -c %s ./a.out" % vcpu
        status = session.cmd_status(cmd)
        session.close()
        if feature != "isa2.7":
            if status != 0:
                test.fail("Feature: %s check failed inside "
                          "%s guest on %s host" % (feature,
                                                   guest_version,
                                                   host_version))
        else:
            if status == 0:
                test.fail("isa3.0 instruction succeeds in "
                          "%s guest on %s host" % (guest_version,
                                                   host_version))
        return True

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pin_vcpu = 0
    host_version = params.get("host_version")
    guest_version = params.get("guest_version")
    max_vcpu = params.get("cpucompat_vcpu_max", "")
    cur_vcpu = int(params.get("cpucompat_vcpu_cur", "1"))
    cores = int(params.get("topology_cores", '1'))
    sockets = int(params.get("topology_sockets", '1'))
    threads = int(params.get("topology_threads", '1'))
    status_error = "yes" == params.get("status_error", "no")
    condn = params.get("condn", "")
    guest_features = params.get("guest_features", "")
    if guest_features:
        guest_features = guest_features.split(',')
        if guest_version:
            guest_features.append(guest_version)
    if host_version not in cpu.get_cpu_arch():
        test.cancel("Unsupported Host cpu version")

    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    org_xml = vmxml.copy()
    # Destroy the vm
    vm.destroy()
    try:
        # Set cpu model
        if max_vcpu:
            pin_vcpu = int(max_vcpu) - 1
            libvirt_xml.VMXML.set_vm_vcpus(vm_name, int(max_vcpu), cur_vcpu,
                                           sockets=sockets, cores=cores,
                                           threads=threads, add_topology=True)
        libvirt_xml.VMXML.set_cpu_mode(vm_name, model=guest_version)
        try:
            vm.start()
        except virt_vm.VMStartError, detail:
            if not status_error:
                test.fail("%s" % detail)
            else:
                pass
        if max_vcpu:
            virsh.setvcpus(vm_name, int(max_vcpu), "--live",
                           ignore_status=False, debug=True)
            if not utils_misc.check_if_vm_vcpu_match(int(max_vcpu), vm):
                test.fail("Vcpu hotplug failed")
        if not status_error:
            for feature in guest_features:
                check_feature(vm, feature, vcpu=pin_vcpu)
        if condn == "filetrans":
            utils_test.run_file_transfer(test, params, env)
        elif condn == "stress":
            bt = utils_test.run_avocado_bg(vm, params, test)
            if not bt:
                test.cancel("guest stress failed to start")
        elif condn == "save":
            save_file = os.path.join(data_dir.get_tmp_dir(), vm_name + ".save")
            result = virsh.save(vm_name, save_file, ignore_status=True,
                                debug=True)
            utils_test.libvirt.check_exit_status(result)
            # Just sleep few secs before guest recovery
            time.sleep(2)
            if os.path.exists(save_file):
                result = virsh.restore(save_file, ignore_status=True,
                                       debug=True)
                utils_test.libvirt.check_exit_status(result)
                os.remove(save_file)
        elif condn == "suspend":
            result = virsh.suspend(vm_name, ignore_status=True, debug=True)
            utils_test.libvirt.check_exit_status(result)
            # Just sleep few secs before guest recovery
            time.sleep(2)
            result = virsh.resume(vm_name, ignore_status=True, debug=True)
            utils_test.libvirt.check_exit_status(result)
        else:
            pass
    finally:
        org_xml.sync()
