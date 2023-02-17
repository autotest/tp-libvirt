import ctypes
import re
from ctypes.util import find_library

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

libc = ctypes.CDLL(find_library('c'))

PR_SCHED_CORE = 62
PR_SCHED_CORE_GET = 0


def get_cookie(pid, test):
    """
    Get the cookie value for given pid

    :param pid: the process id
    :param test: test object
    :return: int, cookie value of the process
    """

    cookie = ctypes.c_ulong()
    libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong
            ]
    ret = libc.prctl(PR_SCHED_CORE, PR_SCHED_CORE_GET, pid, 0, ctypes.addressof(cookie))
    test.log.debug("Process '%d' has cookie '%d'", pid, cookie.value)
    if ret:
        test.error("Fail to get cookie for process '%d' with exit code %d" % (pid, ret))
    return cookie.value


def update_qemu_conf(params, test):
    """
    Update the /etc/libvirt/qemu.conf file with given values for sched_core

    :param params: str, new value for sched_core in conf file
    :param test: test object
    :return: LibvirtQemuConfig, the updated object
    """
    qemu_conf_dict = eval(params.get("qemu_conf_dict", "{}"))
    test.log.debug("Update qemu configuration file.")
    qemu_config = libvirt.customize_libvirt_config(qemu_conf_dict, "qemu")
    test.log.debug("Now qemu configuration file is\n%s", qemu_config)
    return qemu_config


def get_helper_process_ids():
    """
    Get helper process ID string

    :return: str, the ID string, like '60827 60823 60820 60816'
    """
    cmd = "pidof virtiofsd"
    helper_process_ids = process.run(cmd, verbose=True,
                                     shell=True).stdout_text.strip()
    return helper_process_ids


def get_emulator_vcpu_process_ids(test):
    """
    Get the process IDs of emulator and vcpus

    :param test: test object
    :return: tuple, (emulator pid string, vcpu pid string),
                     like ('60827 60823', '60820 60816')
    """
    cmd = 'cat /proc/`pidof qemu-kvm`/task/*/stat'
    ret = process.run(cmd, verbose=True, shell=True).stdout_text.strip()
    qemu_kvm_pattern = r"(\d+)\s*\(qemu-kvm\)"
    cpu_pattern = r"(\d+)\s*\(CPU \d+/KVM\)"
    emulator_pid_list = re.findall(qemu_kvm_pattern, ret)
    vcpu_pid_list = re.findall(cpu_pattern, ret)

    test.log.debug("Return emulator pid list: %s\n"
                   "vcpu pid list:%s", emulator_pid_list, vcpu_pid_list)
    return (' '.join(emulator_pid_list), ' '.join(vcpu_pid_list))


def get_newly_added_process_ids(old_ids, new_ids):
    """
    Get the newly added process IDs.

    :param old_ids: str includes old process ids, like '212822 212820'
    :param new_ids: str, includes new process ids, like '212935 212934 212822 212820'
    :return: str, the newly added ids, like '212935 212934'
    """
    return ' '.join(list(set(new_ids.strip().split()) - set(old_ids.strip().split())))


def get_cookie_by_pid(pids, test):
    """
    Get cookie value for given PID.

    :param pids: process ids, like '212822 212820'
    :param test: test object
    :return: list, the list of cookie values, like ['0', '0']
    """
    cookies_list = [get_cookie(int(pid), test) for pid in pids.strip().split(' ')]
    return cookies_list


def update_vm_xml(vmxml, params, test):
    """
    Update the vm xml

    :param vmxml: vm xml
    :param params: test parameters
    :param test: test object
    """
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    vmxml.setup_attrs(**vm_attrs)

    mem_backing = vm_xml.VMMemBackingXML()
    mem_backing_attrs = eval(params.get('mem_backing_attrs', '{}'))
    mem_backing.setup_attrs(**mem_backing_attrs)
    test.log.debug('memoryBacking xml is: %s', mem_backing)
    vmxml.mb = mem_backing

    filesystem_attrs = eval(params.get('filesystem_attrs', '{}'))
    libvirt_vmxml.modify_vm_device(vmxml, 'filesystem', filesystem_attrs, index=1)
    vmxml.sync()
    test.log.debug("New vm xml after changed:"
                   "%s", vm_xml.VMXML.new_from_inactive_dumpxml(params.get('main_vm')))


def verify_cookies(vcpu_pids, emulator_pids, helper_pids, sched_core, test):
    """
    Verify the cookie values are as expected under different sched_core values

    :param vcpu_pids: str, vcpu PID string, separated by a space, like '255837 255835'
    :param emulator_pids: str, emulator PID string, separated by a space
    :param helper_pids: str, helper PID string, separated by a space
    :param sched_core: str, value for sched_core, like 'none', 'full', 'vcpu', 'emulator'
    :param test: test object
    """
    def _check_cookies(type, cookies_list, expected_value):
        """
        Check if the cookie is correct

        :param type: str, 'vcpu', 'emulator', 'helper processes'
        :param cookies_list: list, cookies, like [0, 0]
        :param expected_value: str, 'positive_int' or specified integer value
        """
        if not cookies_list:
            return
        for one_cookie in cookies_list:
            if expected_value == 'positive_int':
                if one_cookie <= 0:
                    test.fail("%s cookies are expected to be positive "
                              "integer, but found %d" % (type, one_cookie))
            elif one_cookie != expected_value:
                test.fail("%s cookies are expected to be %d, "
                          "but found %d" % (type, expected_value, one_cookie))

    vcpu_cookies_list, emulator_cookies_list, helper_cookies_list = None, None, None
    if vcpu_pids:
        vcpu_cookies_list = get_cookie_by_pid(vcpu_pids, test)
    if emulator_pids:
        emulator_cookies_list = get_cookie_by_pid(emulator_pids, test)
    if helper_pids:
        helper_cookies_list = get_cookie_by_pid(helper_pids, test)

    if sched_core == 'none':
        _check_cookies('vcpu', vcpu_cookies_list, 0)
        _check_cookies('emulator', emulator_cookies_list, 0)
        _check_cookies('helper processes', helper_cookies_list, 0)
    elif sched_core == 'vcpus':
        _check_cookies('vcpu', vcpu_cookies_list, 'positive_int')
        _check_cookies('emulator', emulator_cookies_list, 0)
        _check_cookies('helper processes', helper_cookies_list, 0)
    elif sched_core == 'emulator':
        _check_cookies('vcpu', vcpu_cookies_list, 'positive_int')
        _check_cookies('emulator', emulator_cookies_list, vcpu_cookies_list[0])
        _check_cookies('helper processes', helper_cookies_list, 0)
    elif sched_core == 'full':
        _check_cookies('vcpu', vcpu_cookies_list, 'positive_int')
        _check_cookies('emulator', emulator_cookies_list, vcpu_cookies_list[0])
        _check_cookies('helper processes', helper_cookies_list, vcpu_cookies_list[0])


def is_hardware_supported(test):
    """
    Check if the host cpu support SMT which is required by tests

    :param test: test object
    """
    cmd = 'cat /sys/devices/system/cpu/smt/control'
    ret = process.run(cmd, verbose=True, shell=True).stdout_text.strip()
    if ret != 'on':
        test.cancel("The test case needs host SMT is on, but found '%s'" % ret)


def run(test, params, env):
    """
    Test sched_core feature
    """
    is_hardware_supported(test)
    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    qemu_config = None
    sched_core = params.get('sched_core')
    virsh_options = {"ignore_status": False, "debug": True}
    try:
        test.log.info("Step 1: Update vm xml with required device and vcpu number")
        update_vm_xml(vmxml, params, test)

        test.log.info("Step 2: Update qemu.conf with required sched_core value")
        qemu_config = update_qemu_conf(params, test)

        test.log.info("Step 3: Start the vm")
        vm.start()

        test.log.info("Step 4: Check cookie values of qemu emulator, vcpus, "
                      "helper processes")
        emulator_pids, vcpu_pids = get_emulator_vcpu_process_ids(test)
        helper_process_pids = get_helper_process_ids()
        verify_cookies(vcpu_pids, emulator_pids,
                       helper_process_pids, sched_core, test)

        test.log.info("Step 5: Hotplug vcpu and check new vcpu's cookies")
        virsh.setvcpus(vm_name, params.get('new_vcpu_current'), **virsh_options)
        virsh.setvcpu(vm_name, "",
                      extra=params.get('setvcpu_extra_option'),
                      **virsh_options)
        _, new_vcpu_pids = get_emulator_vcpu_process_ids(test)
        verify_cookies(get_newly_added_process_ids(vcpu_pids, new_vcpu_pids),
                       None, None, sched_core, test)

        test.log.info("Step 6: Attach a device which have helper processes "
                      "and check helper processes' cookies")
        fs_attrs_attach = eval(params.get('filesystem_attrs_attach', '{}'))
        attached_fs = libvirt_vmxml.create_vm_device_by_type('filesystem',
                                                             fs_attrs_attach)
        virsh.attach_device(vm_name, filearg=attached_fs.xml, **virsh_options)
        new_helper_process_pids = get_helper_process_ids()
        verify_cookies(vcpu_pids, None,
                       get_newly_added_process_ids(helper_process_pids, new_helper_process_pids),
                       sched_core, test)

    finally:
        if qemu_config:
            libvirt.customize_libvirt_config(None, is_recover=True,
                                             config_type="qemu",
                                             config_object=qemu_config)
        if bkxml:
            bkxml.sync()
