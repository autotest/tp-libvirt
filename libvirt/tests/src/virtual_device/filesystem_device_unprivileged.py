# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: smitterl@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import logging
import os
import shutil
import stat
import time

from aexpect import ShellSession

from avocado.core.exceptions import TestError
from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import utils_ids
from virttest import virsh
from virttest.staging import utils_memory
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_libvirt.libvirt_filesystem import check_idmap_xml_filesystem_device
from virttest.utils_libvirt.libvirt_unprivileged import get_unprivileged_vm
from virttest.utils_libvirt.libvirt_vmxml import create_vm_device_by_type

LOG = logging.getLogger("avocado." + __name__)
allow_all = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO

unpr_virsh = ""
vmxmls = {}
backupxmls = []
vms = []
test_user = ""
test_passwd = ""
_params = {}
backup_huge_pages_num = 0
user_config_path = ""
hugepages_user_path = "/user_hugepages"
with_hugepages = False
qemu_config = None


def get_unprivileged_vms():
    """
    Store the unprivileged user's VMs and the virsh
    object for interaction as unprivileged user.

    Assumes that the non-root user exists on the system
    and that they have VMs of given names.
    """

    global vmxmls, backupxmls, vms

    vm_names = _params.get("unpr_vms").split(",")
    unpr_vm_args = {
        "username": _params.get("username"),
        "password": _params.get("password"),
    }

    vms = [
        get_unprivileged_vm(vm_name, test_user, test_passwd, **unpr_vm_args)
        for vm_name in vm_names
    ]

    for vm_name in vm_names:
        vmxmls[vm_name] = VMXML.new_from_inactive_dumpxml(
            vm_name,
            virsh_instance=unpr_virsh,
        )

    backupxmls = [vmxmls[name].copy() for name in vmxmls]


def _initialize_unpr_virsh():
    """
    Initializes the global instance of the virsh commands object.
    """

    global unpr_virsh
    unpr_uri = f"qemu+ssh://{test_user}@localhost/session"
    unpr_virsh = virsh.VirshPersistent(uri=unpr_uri, safe=True)

    host_session = ShellSession("su")
    remote.VMManager.set_ssh_auth(host_session, "localhost", test_user, test_passwd)
    host_session.close()


def _refresh_vmxmls():
    """
    Refreshes the global list of VMXML instances under test
    so they are up-to-date after changes.
    """
    global vmxmls
    for name in vmxmls:
        vmxmls[name] = VMXML.new_from_dumpxml(name, virsh_instance=unpr_virsh)
        LOG.debug("Current XML: %s", vmxmls[name])


def configure_hugepages_for_unprivileged_user(mem):
    """
    Allocates hugepages and creates a mount for the unprivileged
    test user.

    Assume that all VMs have the same max_mem value so if
    the QEMU configuration has already been altered it assumes
    hugepages have already been set up and will return.

    It kills the user's process at the end to force reload
    with the new configuration.

    :param mem: The memory size that needs to be allocated
                in the same unit as the VMXML
    """
    global backup_huge_pages_num, qemu_config
    if qemu_config:
        return
    if not os.path.exists(user_config_path):
        shutil.copyfile(LibvirtQemuConfig.conf_path, user_config_path)
        os.chmod(user_config_path, allow_all)
    extra_hugepages = _params.get_numeric("extra_hugepages")
    host_hp_size = utils_memory.get_huge_page_size()
    backup_huge_pages_num = utils_memory.get_num_huge_pages()
    huge_pages_num = 0
    huge_pages_num += mem // host_hp_size + extra_hugepages
    utils_memory.set_num_huge_pages(huge_pages_num)
    process.run(f"mkdir {hugepages_user_path}", ignore_status=False)
    process.run(
        f"mount -t hugetlbfs hugetlbfs {hugepages_user_path}", ignore_status=False
    )
    process.run(f"chmod a+wrx {hugepages_user_path}", ignore_status=False)
    qemu_config = LibvirtQemuConfig(user_config_path)
    qemu_config.hugetlbfs_mount = [hugepages_user_path]
    process.run(f"killall --user {test_user} virtqemud", shell=True)


def clean_up_hugepages_for_unprivileged_user():
    """
    Cleans up the set up for hugepages
    """
    qemu_config.restore()
    process.run(f"killall --user {test_user} virtqemud", shell=True)
    utils_memory.set_num_huge_pages(backup_huge_pages_num)
    process.run(f"umount {hugepages_user_path}")
    process.run(f"rm -rf {hugepages_user_path}")


def add_memory_backing():
    """
    Adds <memoryBacking> and removes all present filesystem devices
    It also sets up hugepages for the unprivileged user assuming
    that all VMs would require the same number of memory
    """

    global backup_huge_pages_num, with_hugepages
    memorybacking = _params.get("memorybacking")
    with_hugepages = "with_hugepages" == memorybacking
    with_memfd = "with_memfd" == memorybacking
    with_numa = "yes" == _params.get("with_numa")
    vcpus_per_cell = _params.get("vcpus_per_cell")

    for name in vmxmls.copy():
        vmxml = vmxmls[name]
        if vmxml.max_mem < 1024000:
            vmxml.max_mem = 1024000
        if with_hugepages:
            configure_hugepages_for_unprivileged_user(len(vms) * vmxml.max_mem)
        numa_no = None
        if with_numa:
            numa_no = vmxml.vcpu // vcpus_per_cell if vmxml.vcpu != 1 else 1

        vmxml.remove_all_device_by_type("filesystem")

        VMXML.set_vm_vcpus(
            vmxml.vm_name,
            vmxml.vcpu,
            numa_number=numa_no,
            virsh_instance=None,
            vmxml=vmxml,
        )
        VMXML.set_memoryBacking_tag(
            vmxml.vm_name,
            access_mode="shared",
            hpgs=with_hugepages,
            memfd=with_memfd,
            virsh_instance=None,
            vmxml=vmxml,
        )

        _initialize_unpr_virsh()
        vmxml.sync(virsh_instance=unpr_virsh)
        vmxmls[vmxml.vm_name] = vmxml


def cold_or_hot_plug_filesystem():
    """
    Cold or hot plugs the filesystem and
    starts the VMs.

    It handles the VM state and makes sure
    they are running for further testing.
    """

    for vm in vms:
        if "hotplug" == _params.get("plugmode") and not vm.is_alive():
            vm.start()
            vm.wait_for_serial_login().close()
        if "coldplug" == _params.get("plugmode") and vm.is_alive():
            vm.destroy()

        for fs_dict in fs_dicts:
            source_dir = fs_dict["source"]["dir"]
            if not os.path.exists(source_dir):
                os.mkdir(source_dir)
                os.chmod(source_dir, allow_all)

            fs = create_vm_device_by_type("filesystem", fs_dict)
            os.chmod(fs.xml, allow_all)
            unpr_virsh.attach_device(
                vm.name, fs.xml, flagstr="--current", debug=True, ignore_status=False
            )

        if not vm.is_alive():
            vm.start()
            time.sleep(10)
    _refresh_vmxmls()


def check_virtiofs_idmap():
    """
    Checks if the unprivileged VM is running with the correct
    user related ids.
    """

    user_info = utils_ids.get_user_ids(test_user)
    for name in vmxmls:
        for fs in vmxmls[name].get_devices("filesystem"):
            check_idmap_xml_filesystem_device(user_info, fs)


def mount_fs(session):
    """
    Mounts the folder inside of the guest

    :param session: Guest console session
    """

    for fs_dict in fs_dicts:
        mount_tag = fs_dict["target"]["dir"]
        mount_dir = f"/mnt/{mount_tag}"
        session.cmd_output_safe(f"mkdir {mount_dir}")
        session.cmd_output_safe(f"mount -t virtiofs {mount_tag} {mount_dir}")


def create_file(session):
    """
    Creates a file in the guest's mounted filesystem(s)

    :param session: guest console session
    """

    for fs_dict in fs_dicts:
        mount_tag = fs_dict["target"]["dir"]
        mount_dir = f"/mnt/{mount_tag}"
        session.cmd_output_safe(
            f"dd if=/dev/zero of={mount_dir}/testfile bs=1M count=10"
        )
        session.cmd_output_safe("sync")


def check_md5sum(sessions):
    """
    Returns the md5sum of the created file

    :param sessions: a list of two console sessions
                     to take md5sum from and compare
                     them; the second session might
                     be a host session (if there is only
                     1 vm in the test scenario)
    """

    sums = {0: [], 1: []}
    for fs_dict in fs_dicts:
        for i in range(len(sessions)):
            mount_tag = fs_dict["target"]["dir"]
            mount_dir = f"/mnt/{mount_tag}"
            source_dir = fs_dict["source"]["dir"]
            cmd = f"md5sum {mount_dir}/testfile"
            if i == 1 and len(vms) == 1:
                cmd = f"md5sum {source_dir}/testfile"
            o = sessions[i].cmd_output_safe(cmd)
            sums[i].append(o.split()[0])

    if sums[0] != sums[1]:
        raise TestError("The md5sums don't match: %s" % sums)


def check_filesystem(after="attach"):
    """
    Prepares the filesystems in the guest(s) and runs some checks

    This assumes that the filesystem is shared either between guest
    and host or between two guests.

    :param after: if "attach" expect commands to succeed
                  if "detach" expect commands to fail
    """

    session1 = vms[0].wait_for_serial_login()
    if len(vms) == 2:
        session2 = vms[1].wait_for_serial_login()
    else:
        session2 = ShellSession("su")

    try:
        mount_fs(session1)
        create_file(session1)
        if len(vms) == 2:
            mount_fs(session2)
        check_md5sum([session1, session2])

    except TestError as e:
        if after == "detach":
            pass
        else:
            raise e

    finally:
        session1.close()
        session2.close()


def cold_or_hot_unplug_filesystem():
    """
    Cold or hot unplugs the filesystem and
    starts the VMs.

    It handles the VM state and makes sure they are
    running for further checks.
    """

    for vm in vms:
        if "coldplug" == _params.get("plugmode") and vm.is_alive():
            vm.destroy()

        for fs_dict in fs_dicts:
            fs = create_vm_device_by_type("filesystem", fs_dict)
            os.chmod(fs.xml, allow_all)
            unpr_virsh.detach_device(
                vm.name, fs.xml, flagstr="--current", debug=True, ignore_status=False
            )

        if not vm.is_alive():
            vm.start()


def initialize(params):
    """
    Initializes parameters that are needed globally for all test
    variants.

    :param params: the test parameters
    """

    global _params, fs_dicts, test_user, user_config_path, test_passwd
    libvirt_version.is_libvirt_feature_supported(params)
    _params = params
    fs_dicts = eval(_params.get("fs_dicts"))
    test_user = _params.get("test_user", "")
    test_passwd = _params.get("test_passwd", "")
    user_config_path = f"/home/{test_user}/.config/libvirt/qemu.conf"
    _initialize_unpr_virsh()


def run(test, params, env):
    """
    Test running VMs with a filesystem device as unprivileged users.
    """

    global backupxmls, unpr_virsh, fs_dicts, with_hugepages
    initialize(params)
    try:
        get_unprivileged_vms()
        add_memory_backing()
        cold_or_hot_plug_filesystem()
        check_virtiofs_idmap()
        check_filesystem(after="attach")
        cold_or_hot_unplug_filesystem()
        check_filesystem(after="detach")
    finally:
        for xml in backupxmls:
            xml.sync(virsh_instance=unpr_virsh)
        if unpr_virsh:
            del unpr_virsh
        for fs_dict in fs_dicts:
            source_dir = fs_dict["source"]["dir"]
            if os.path.exists(source_dir):
                shutil.rmtree(source_dir)
        if with_hugepages:
            clean_up_hugepages_for_unprivileged_user()
