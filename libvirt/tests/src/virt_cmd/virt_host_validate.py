import re
import logging

from avocado.utils import process


def simple_check(option, result):
    """
    This function is used to check simple test results
    Checking command with option like "-h" "-q" "-v"
    """
    # make a checklist for each simple cases
    if option == "-h":
        checklist = ["Hypervisor types", "Options", "--help", "--version", "--quiet", "qemu"]
    elif option == "-v":
        checklist = ["version", "virt-host-validate"]
    elif option == "-q":
        checklist = []

    status = 0
    for check in checklist:
        if not re.search(check, result):
            if not option == "-q":
                status = 1
    return status


def hardware_check(result_list):
    """
    Check if the "hardware virtualization" line
    in command output shows right result
    """
    status = 0
    cmd = "cat /proc/cpuinfo|grep 'model name'"
    ret = process.run(cmd, shell=True, ignore_status=True)

    if re.search("PASS", result_list):
        if re.search("Intel", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep vmx"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("vmx", ret.stdout):
                status = 1
        elif re.search("AMD", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep svm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("svm", ret.stdout):
                status = 1
        elif re.search("s390x", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep sie"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("sie", ret.stdout):
                status = 1
        # PPC do not display this line
        elif not re.search("PPC", ret.stdout):
            status = 1

    if re.search("WARN" or "FAIL", result_list):
        if not re.search("Only emulated CPUs are available, performance will be significantly limited", result_list):
            status = 1
        if re.search("Intel", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep vmx"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if re.search("vmx", ret.stdout):
                status = 1
        elif re.search("AMD", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep svm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if re.search("svm", ret.stdout):
                status = 1
        elif re.search("s390x", ret.stdout):
            cmd = "cat /proc/cpuinfo |grep sie"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if re.search("sie", ret.stdout):
                status = 1
        # PPC do not display this line
        elif not re.search("PPC", ret.stdout):
            status = 1

    return status


def dev_check(result_list, error_msg, error_msg_otherarch):
    """
    Check if the line include "/dev/"
    in command output shows right result
    """
    status = 0
    if re.search("PASS", result_list):
        if re.search("/dev/kvm exists", result_list):
            cmd = "ls /dev/kvm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("/dev/kvm", ret.stdout):
                status = 1
        elif re.search("/dev/kvm is accessible", result_list):
            cmd = "ls /dev/kvm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("/dev/kvm", ret.stdout):
                status = 1
        elif re.search("/dev/vhost-net", result_list):
            cmd = "ls /dev/vhost-net"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("/dev/vhost-net", ret.stdout):
                status = 1
        elif re.search("/dev/net/tun", result_list):
            cmd = "ls /dev/net/tun"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if not re.search("/dev/net/tun", ret.stdout):
                status = 1
    elif re.search("WARN" or "FAIL", result_list):
        if re.search("/dev/kvm is accessible", result_list):
            if not re.search(error_msg, result_list):
                logging.debug("Print Fail result: %s", result_list)
                status = 1
        elif re.search("/dev/kvm exists", result_list):
            cmd = "uname -a"
            ret = process.run(cmd, shell=True, ignore_status=True)
            if re.search("x86", ret.stdout):
                if not re.search(error_msg, result_list):
                    logging.debug("Print Fail result: %s", result_list)
                    status = 1
            else:
                if not re.search(error_msg_otherarch, result_list):
                    logging.debug("Print Fail result: %s", result_list)
                    status = 1
        elif re.search("/dev/vhost-net exists", result_list):
            if not re.search(error_msg, result_list):
                logging.debug("Print Fail result: %s", result_list)
                status = 1
    return status


def cgroup_support_check(result_list, obj):
    """
    Check if the line include "cgroup" and "support"
    in command output shows right result
    """
    status = 0
    if obj == "memory":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep memory"
            ret = process.run(cmd, shell=True)
            if not re.search("memory:/", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_MEMCG in kernel Kconfig file", result_list):
                status = 1

    elif obj == "cpu":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep cpu"
            ret = process.run(cmd, shell=True)
            if not re.search("cpu", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_CGROUP_CPU in kernel Kconfig file", result_list):
                status = 1

    elif obj == "cpuacct":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep cpuacct"
            ret = process.run(cmd, shell=True)
            if not re.search("cpuacct", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_CGROUP_CPUACCT in kernel Kconfig file", result_list):
                status = 1

    elif obj == "cpuset":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep cpuset"
            ret = process.run(cmd, shell=True)
            if not re.search("cpuset", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_CPUSETS in kernel Kconfig file", result_list):
                status = 1

    elif obj == "devices":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep devices"
            ret = process.run(cmd, shell=True)
            if not re.search("devices", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_CGROUP_DEVICES in kernel Kconfig file", result_list):
                status = 1

    elif obj == "blkio":
        if re.search("PASS", result_list):
            cmd = "cat /proc/self/cgroup|grep blkio"
            ret = process.run(cmd, shell=True)
            if not re.search("blkio", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Enable CONFIG_BLK_CGROUP in kernel Kconfig file", result_list):
                status = 1
    return status


def cgroup_mount_check(result_list, obj):
    """
    Check if the line include "cgroup" and "mount"
    in command output shows right result
    """
    status = 0
    if obj == "memory":
        if re.search("PASS", result_list):
            cmd = "cat /proc/mounts |grep cgroup.*memory"
            ret = process.run(cmd, shell=True)
            if not re.search("cgroup /sys/fs/cgroup/memory cgroup rw,seclabel,nosuid,nodev,noexec,relatime,memory 0 0", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'memory' cgroup controller", result_list):
                status = 1

    elif obj == "cpu":
        if re.search("PASS", result_list):
            cmd = "uname -a"
            ret = process.run(cmd, shell=True)
            if re.search("x86", ret.stdout):
                cmd = "cat /proc/mounts |grep cgroup.*cpu"
                ret = process.run(cmd, shell=True)
                if not re.search("cgroup /sys/fs/cgroup/cpu,cpuacct cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpuacct,cpu 0 0", ret.stdout):
                    status = 1
            elif re.search("ppc", ret.stdout):
                cmd = "cat /proc/mounts |grep cgroup.*cpu"
                ret = process.run(cmd, shell=True)
                if not re.search("cgroup /sys/fs/cgroup/cpu,cpuacct cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpu,cpuacct 0 0", ret.stdout):
                    status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'cpu' cgroup controller", result_list):
                status = 1

    elif obj == "cpuacct":
        if re.search("PASS", result_list):
            cmd = "uname -a"
            ret = process.run(cmd, shell=True)
            if re.search("x86", ret.stdout):
                cmd = "cat /proc/mounts |grep cgroup.*cpuacct"
                ret = process.run(cmd, shell=True)
                if not re.search("cgroup /sys/fs/cgroup/cpu,cpuacct cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpuacct,cpu 0 0", ret.stdout):
                    status = 1
            elif re.search("ppc", ret.stdout):
                cmd = "cat /proc/mounts |grep cgroup.*cpuacct"
                ret = process.run(cmd, shell=True)
                if not re.search("cgroup /sys/fs/cgroup/cpu,cpuacct cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpu,cpuacct 0 0", ret.stdout):
                    status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'cpuacct' cgroup controller", result_list):
                status = 1

    elif obj == "cpuset":
        if re.search("PASS", result_list):
            cmd = "cat /proc/mounts |grep cgroup.*cpuset"
            ret = process.run(cmd, shell=True)
            if not re.search("cgroup /sys/fs/cgroup/cpuset cgroup rw,seclabel,nosuid,nodev,noexec,relatime,cpuset 0 0", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'cpuset' cgroup controller", result_list):
                status = 1

    elif obj == "devices":
        if re.search("PASS", result_list):
            cmd = "cat /proc/mounts |grep cgroup.*devices"
            ret = process.run(cmd, shell=True)
            if not re.search("cgroup /sys/fs/cgroup/devices cgroup rw,seclabel,nosuid,nodev,noexec,relatime,devices 0 0", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'devices' cgroup controller", result_list):
                status = 1

    elif obj == "blkio":
        if re.search("PASS", result_list):
            cmd = "cat /proc/mounts |grep cgroup.*blkio"
            ret = process.run(cmd, shell=True)
            if not re.search("cgroup /sys/fs/cgroup/blkio cgroup rw,seclabel,nosuid,nodev,noexec,relatime,blkio 0 0", ret.stdout):
                status = 1
        elif re.search("WARN" or "FAIL", result_list):
            if not re.search("Mount 'blkio' cgroup controller", result_list):
                status = 1
    return status


def IOMMU_support_check(result_list):
    """
    Check if the "assignment IOMMU support" line
    in command output shows right result
    """
    status = 0
    if re.search("PASS", result_list):
        cmd = "cat /proc/cpuinfo|grep 'model name'"
        ret = process.run(cmd, shell=True, ignore_status=True)
        if re.search("Intel", ret.stdout):
            cmd = "ls /sys/firmware/acpi/tables/DMAR"
            ret = process.run(cmd, shell=True)
            if not re.search("/sys/firmware/acpi/tables/DMAR", ret.stdout):
                status = 1
        elif re.search("AMD", ret.stdout):
            cmd = "ls /sys/firmware/acpi/tables/IVRS"
            ret = process.run(cmd, shell=True)
            if not re.search("/sys/firmware/acpi/tables/IVRS", ret.stdout):
                status = 1
    elif re.search("WARN" or "FAIL", result_list):
        cmd = "cat /proc/cpuinfo|grep 'model name"
        ret = process.run(cmd, shell=True, ignore_status=True)
        if re.search("Inter", ret.stdout):
            if not re.search("No ACPI DMAR table found, IOMMU either disabled in BIOS or not supported by this hardware platform", result_list):
                status = 1
        if re.search("AMD", ret.stdout):
            if not re.search("No ACPI IVRS table found, IOMMU either disabled in BIOS or not supported by this hardware platform", result_list):
                status = 1
    return status


def IOMMU_enable_check(result_list):
    """
    Check if the "IOMMU is enabled by kernel" line
    in command output shows right result
    """
    status = 0
    if re.search("WARN" or "FAIL", result_list):
        cmd = "cat /proc/cpuinfo|grep 'model name'"
        ret = process.run(cmd, shell=True, ignore_status=True)
        if re.search("Intel", ret.stdout):
            if not re.search("WARN" and "IOMMU appears to be disabled in kernel. Add intel_iommu=on to kernel cmdline arguments", result_list):
                status = 1
        elif re.search("AMD", ret.stdout):
            if not re.search("WARN" and "IOMMU appears to be disabled in kernel. Add iommu=pt iommu=1 to kernel cmdline arguments", result_list):
                status = 1
        elif re.search("PPC", ret.stdout):
            if not re.search("WARN" and "IOMMU capability not compiled into kernel", result_list):
                status = 1
    elif re.search("PASS", result_list):
        cmd = "ls /sys/kernel/iommu_groups"
        ret = process.run(cmd, shell=True)
        if not re.search("0" or "1" or "2" or "3", ret.stdout):
            status = 1
    return status


def complex_check(option, result, error_msg, error_msg_otherarch):
    """
    This function is used to check complex command
    Checking command with options like "qemu" and ""
    """
    if option == "qemu" or option == "":
        result_list = result.split("\n")
        logging.debug("Print result_list: %s \n lenth: %s", result_list, len(result_list))

        status = 0
        flag = 0
        i = 0
        while(re.search("QEMU", result_list[i])):
            if re.search("hardware virtualization", result_list[i]):
                status = hardware_check(result_list[i])
                if status == 1:
                    logging.debug("Problem occur in hardware_check")
                    flag = 1

            elif re.search("/dev/", result_list[i]):
                status = dev_check(result_list[i], error_msg, error_msg_otherarch)
                if status == 1:
                    logging.debug("Problem occur in dev_check")
                    flag = 1

            elif re.search("cgroup" and "support", result_list[i]):
                obj = []
                obj = re.findall(r"'[a-z]+'", result_list[i])
                if not len(obj) == 0:
                    obj = re.findall(r"[a-z]+", obj[0])
                    status = cgroup_support_check(result_list[i], obj[0])
                    if status == 1:
                        logging.debug("Problem occur in cgroup_support_check")
                        flag = 1

            elif re.search("cgroup" and "mount-point", result_list[i]):
                obj = re.findall(r"'[a-z]+'", result_list[i])
                if not len(obj) == 0:
                    obj = re.findall(r"[a-z]+", obj[0])
                    status = cgroup_mount_check(result_list[i], obj[0])
                    if status == 1:
                        logging.debug("Problem occur in cgroup_mount_check")
                        flag = 1

            elif re.search("IOMMU support", result_list[i]):
                status = IOMMU_support_check(result_list[i])
                if status == 1:
                    logging.debug("Problem occur in IOMMU_support_check")
                    flag = 1

            elif re.search("IOMMU is enabled", result_list[i]):
                status = IOMMU_enable_check(result_list[i])
                if status == 1:
                    logging.debug("Problem occur in IOMMU_enable_check")
                    flag = 1

            i += 1

    return flag


def invalid_option_check(option, result):
    """
    This function is used to check error_test results
    """
    if option == "-k":
        checklist = ["Hypervisor types", "Options", "--help", "--version", "--quiet", "qemu", "invalid option"]
    status = 0
    for check in checklist:
        if not re.search(check, result):
            status = 1
    return status


def run(test, params, env):
    """
    Test the virt-host-validate command
    Both normal test and error test
    """

    # Get params
    option = params.get("validate_option")
    no_vhost_net = params.get("no_vhost_net", "no")
    no_devkvm = params.get("no_devkvm", "no")
    umount_cgroup = params.get("umount_cgroup", "no")
    inaccessible_devkvm = params.get("inaccessible_devkvm", "no")
    status_error = params.get("status_error", "no")
    negative_test = params.get("negative_test", "no")
    error_msg = params.get("error_msg")
    error_msg_otherarch = params.get("error_msg_otherarch")

    # Prepare for negative_test
    if negative_test == "yes":
        if no_vhost_net == "yes":
            cmd = "mv /dev/vhost-net /dev/vhost-net.bak"
            ret = process.run(cmd, shell=True)
        elif no_devkvm == "yes":
            cmd = "mv /dev/kvm /dev/kvm.bak"
            ret = process.run(cmd, shell=True)
        elif umount_cgroup == "yes":
            cmd = "umount /sys/fs/cgroup/cpuset"
            ret = process.run(cmd, shell=True)
        elif inaccessible_devkvm == "yes":
            cmd = "chomd 600 /dev/kvm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            cmd = "useradd test0"
            ret = process.run(cmd, shell=True)
            if ret.exit_status == 1:
                test.cancel("User test0 already exist! Conflict with test machine's username!")
            cmd = "su - test0"
            ret = process.run(cmd, shell=True)

        if ret.exit_status == 1:
            test.cancel("Preparation is failed")

    # Run virt command
    cmd = "virt-host-validate %s" % (option)
    result = process.run(cmd, shell=True, ignore_status=True)
    logging.debug("Print cmd: %s", cmd)

    # Check the test result
    status = 0
    if option == "-h" or option == "-v" or option == "-q":
        # Check cases with simple result
        status = simple_check(option, str(result))
    elif option == "qemu" or option == "":
        # Check cases with complex result
        status = complex_check(option, result.stdout, error_msg, error_msg_otherarch)
    elif status_error == "yes" and result.exit_status == 1:
        if option == "-k":
            status = invalid_option_check(option, result.stdout)
        status = 1

    # Recover from negative_test
    if negative_test == "yes":
        if no_vhost_net == "yes":
            cmd = "mv /dev/vhost-net.bak /dev/vhost-net"
            ret = process.run(cmd, shell=True)
        elif no_devkvm == "yes":
            cmd = "mv /dev/kvm.bak /dev/kvm"
            ret = process.run(cmd, shell=True)
        elif umount_cgroup == "yes":
            cmd = "mount -t cgroup -o rw,nosuid,nodev,noexec,relatime,cpuset cgroup /sys/fs/cgroup/cpuset"
            ret = process.run(cmd, shell=True)
        elif inaccessible_devkvm == "yes":
            cmd = "exit"
            ret = process.run(cmd, shell=True)
            cmd = "chmod 666 /dev/kvm"
            ret = process.run(cmd, shell=True, ignore_status=True)
            cmd = "userdel -r test0"
            ret = process.run(cmd, shell=True)

        logging.debug("Print command feature: %s", ret.stdout)

        if ret.exit_status == 0:
            logging.debug("Recover Successful!")

    # Check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
